from enum import Enum
from pathlib import Path
from typing import Optional
from datetime import datetime

from .database import Database
from .pdf_parser import extract_text_with_diagnostics, extract_metadata, validate_pdf
from .auditor import audit_document, AuditResult, AuditIssue
from .learning import load_guidelines, save_guideline
from .rule_engine import DeterministicRuleEngine


class AuditStatus(str, Enum):
    PENDING = "pending"
    AUDITING = "auditing"
    PASSED = "passed"
    REJECTED = "rejected"


class WorkflowManager:
    def __init__(self):
        self.db = Database()
        self.rule_engine = DeterministicRuleEngine()

    def upload_document(self, file_path: str | Path) -> dict:
        """上传文档并创建审核记录"""
        path = Path(file_path)

        is_valid, message = validate_pdf(path)
        if not is_valid:
            return {"success": False, "error": message}

        metadata = extract_metadata(path)
        record = self.db.create_record(
            filename=metadata.filename,
            original_path=str(path.absolute())
        )

        self.db.add_workflow_log(
            record.id,
            "upload",
            f"文件上传成功，共{metadata.page_count}页"
        )

        return {
            "success": True,
            "record_id": record.id,
            "metadata": {
                "filename": metadata.filename,
                "page_count": metadata.page_count,
                "text_page_count": metadata.text_page_count,
                "image_only_pages": metadata.image_only_pages or [],
                "has_tables": metadata.has_tables,
                "file_size_kb": metadata.file_size_kb
            }
        }

    def start_audit(self, record_id: int) -> dict:
        """开始审核"""
        record = self.db.get_record(record_id)
        if not record:
            return {"success": False, "error": "记录不存在"}

        if record.status != AuditStatus.PENDING.value:
            return {"success": False, "error": "该记录不在待审核状态"}

        self.db.update_record_status(record_id, AuditStatus.AUDITING.value)
        self.db.add_workflow_log(record_id, "start_audit")

        try:
            extraction = extract_text_with_diagnostics(record.original_path)
            content = extraction.text
            extraction_note = extraction.warning_text()
            if extraction_note:
                content = f"{extraction_note}\n\n{content}"

            deterministic = self.rule_engine.evaluate(extraction.page_texts)
            content = f"{deterministic.to_prompt_block()}\n\n{content}"

            guidelines = load_guidelines()
            review_cases = [
                self._review_case_to_dict(case)
                for case in self.db.find_similar_review_cases(content)
            ]
            result = audit_document(
                content,
                guidelines=guidelines,
                review_cases=review_cases
            )
            result.issues = self.rule_engine.filter_llm_issues(result.issues, deterministic)
            result.issues.extend(deterministic.issues)
            result.issues = self._sort_issues(result.issues)
            result.passed = len(result.issues) == 0

            status = AuditStatus.PASSED.value if result.passed else AuditStatus.REJECTED.value
            self.db.update_record_status(record_id, status, result.to_json())
            self.db.add_workflow_log(
                record_id,
                "complete_audit",
                f"审核{'通过' if result.passed else '未通过'}，发现{len(result.issues)}个问题"
            )

            return {
                "success": True,
                "result": result.to_dict(),
                "status": status
            }

        except Exception as e:
            self.db.update_record_status(record_id, AuditStatus.PENDING.value)
            return {"success": False, "error": str(e)}

    def approve(self, record_id: int, comment: Optional[str] = None) -> dict:
        """确认通过"""
        record = self.db.get_record(record_id)
        if not record:
            return {"success": False, "error": "记录不存在"}

        if record.status != AuditStatus.PASSED.value:
            return {"success": False, "error": "该记录不在已通过状态"}

        if comment:
            self.db.add_comment(record_id, comment)

        self.db.add_workflow_log(record_id, "approved", comment)
        return {"success": True, "message": "已确认通过"}

    def reject(self, record_id: int, reason: str) -> dict:
        """退回修改"""
        record = self.db.get_record(record_id)
        if not record:
            return {"success": False, "error": "记录不存在"}

        self.db.update_record_status(record_id, AuditStatus.REJECTED.value)
        self.db.add_comment(record_id, reason)
        self.db.add_workflow_log(record_id, "rejected", reason)

        return {"success": True, "message": "已退回修改"}

    def get_record(self, record_id: int) -> Optional[dict]:
        """获取审核记录详情"""
        record = self.db.get_record(record_id)
        if not record:
            return None

        result = None
        if record.result_json:
            import json
            result = AuditResult.from_dict(json.loads(record.result_json))

        return {
            "id": record.id,
            "filename": record.filename,
            "upload_time": record.upload_time.isoformat() if record.upload_time else None,
            "status": record.status,
            "result": result.to_dict() if result else None,
            "comment": record.auditor_comment,
            "completed_time": record.completed_time.isoformat() if record.completed_time else None
        }

    def list_records(self, status: Optional[str] = None) -> list[dict]:
        """列出审核记录"""
        records = self.db.list_records(status=status)
        return [
            {
                "id": r.id,
                "filename": r.filename,
                "upload_time": r.upload_time.isoformat() if r.upload_time else None,
                "status": r.status,
                "completed_time": r.completed_time.isoformat() if r.completed_time else None
            }
            for r in records
        ]

    def get_logs(self, record_id: int) -> list[dict]:
        """获取流转日志"""
        logs = self.db.get_workflow_logs(record_id)
        return [
            {
                "action": log.action,
                "details": log.details,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None
            }
            for log in logs
        ]

    def save_feedback(
        self,
        record_id: int,
        case_summary: str,
        ai_issue: str,
        human_decision: str,
        human_reason: str,
        learn_as_guideline: bool = False,
        guideline_title: str = "",
        guideline_applies_when: str = "",
        guideline_text: str = "",
        severity_override: str = ""
    ) -> dict:
        """保存人工反馈，必要时沉淀为长期审核口径。"""
        record = self.db.get_record(record_id)
        if not record:
            return {"success": False, "error": "记录不存在"}

        if not case_summary.strip():
            return {"success": False, "error": "请填写案例摘要"}
        if not human_reason.strip():
            return {"success": False, "error": "请填写人工判断理由"}

        saved_guideline = None
        if learn_as_guideline:
            saved_guideline = save_guideline(
                title=guideline_title or case_summary,
                applies_when=guideline_applies_when or case_summary,
                guidance=guideline_text or human_reason,
                severity_override=severity_override or human_decision,
                source=f"record:{record_id}"
            )

        case = self.db.add_review_case(
            record_id=record_id,
            filename=record.filename,
            case_summary=case_summary,
            ai_issue=ai_issue,
            human_decision=human_decision,
            human_reason=human_reason,
            learned_as_guideline=learn_as_guideline,
            guideline_text=guideline_text if learn_as_guideline else None
        )
        self.db.add_workflow_log(
            record_id,
            "feedback_saved",
            f"保存人工反馈判例 #{case.id}"
        )

        return {
            "success": True,
            "case_id": case.id,
            "guideline": saved_guideline
        }

    def list_review_cases(self) -> list[dict]:
        """列出最近保存的人工反馈判例。"""
        return [
            self._review_case_to_dict(case)
            for case in self.db.list_review_cases(limit=30)
        ]

    def cleanup_old_files(self, days: int = 30) -> dict:
        """清理过期文件"""
        from config.settings import UPLOADS_DIR

        deleted_paths = self.db.cleanup_old_files(days)

        deleted_count = 0
        for path_str in deleted_paths:
            path = Path(path_str)
            if path.exists():
                try:
                    path.unlink()
                    deleted_count += 1
                except Exception:
                    pass

        return {
            "success": True,
            "deleted_records": len(deleted_paths),
            "deleted_files": deleted_count
        }

    def close(self):
        self.db.close()

    @staticmethod
    def _review_case_to_dict(case) -> dict:
        return {
            "id": case.id,
            "record_id": case.record_id,
            "filename": case.filename,
            "case_summary": case.case_summary,
            "ai_issue": case.ai_issue,
            "human_decision": case.human_decision,
            "human_reason": case.human_reason,
            "learned_as_guideline": case.learned_as_guideline,
            "guideline_text": case.guideline_text,
            "created_at": case.created_at.isoformat() if case.created_at else None
        }

    @staticmethod
    def _sort_issues(issues: list[AuditIssue]) -> list[AuditIssue]:
        severity_order = {"严重": 0, "一般": 1, "提示": 2}
        category_order = {
            "文字规范审核": 0,
            "呈报表审核": 1,
            "出访行程审核": 2,
            "情况说明审核": 3,
            "邀请函审核": 4,
            "预算审批意见表审核": 5,
            "重点检查": 6,
            "其他": 7,
        }

        def page_number(issue: AuditIssue) -> int:
            import re
            match = re.search(r"第(\d+)页", issue.location or "")
            if match:
                return int(match.group(1))
            return 9999

        return sorted(
            issues,
            key=lambda issue: (
                severity_order.get(issue.severity, 9),
                category_order.get(issue.category, 99),
                page_number(issue),
                issue.description
            )
        )
