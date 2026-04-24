from datetime import datetime
from typing import Optional
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import SQLAlchemyError

from config.settings import DATABASE_URL

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class AuditRecord(Base):
    __tablename__ = "audit_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(255), nullable=False)
    original_path = Column(String(512))
    upload_time = Column(DateTime, default=datetime.now)
    status = Column(String(20), default="pending")
    result_json = Column(Text)
    auditor_comment = Column(Text)
    completed_time = Column(DateTime)


class AuditRule(Base):
    __tablename__ = "audit_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(100))
    rule_text = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)


class WorkflowLog(Base):
    __tablename__ = "workflow_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    record_id = Column(Integer, nullable=False)
    action = Column(String(50))
    details = Column(Text)
    timestamp = Column(DateTime, default=datetime.now)


class ReviewCase(Base):
    __tablename__ = "review_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    record_id = Column(Integer, nullable=True)
    filename = Column(String(255))
    case_summary = Column(Text, nullable=False)
    ai_issue = Column(Text)
    human_decision = Column(String(50), nullable=False)
    human_reason = Column(Text, nullable=False)
    learned_as_guideline = Column(Boolean, default=False)
    guideline_text = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


Base.metadata.create_all(bind=engine)


class Database:
    def __init__(self):
        self.session = SessionLocal()

    def create_record(
        self,
        filename: str,
        original_path: Optional[str] = None
    ) -> AuditRecord:
        """创建新的审核记录"""
        record = AuditRecord(
            filename=filename,
            original_path=original_path,
            status="pending"
        )
        self.session.add(record)
        self.session.commit()
        return record

    def get_record(self, record_id: int) -> Optional[AuditRecord]:
        """获取审核记录"""
        return self.session.query(AuditRecord).filter_by(id=record_id).first()

    def update_record_status(
        self,
        record_id: int,
        status: str,
        result_json: Optional[str] = None
    ) -> bool:
        """更新审核状态"""
        record = self.get_record(record_id)
        if record:
            record.status = status
            if result_json:
                record.result_json = result_json
            if status in ("passed", "rejected"):
                record.completed_time = datetime.now()
            self.session.commit()
            return True
        return False

    def add_comment(self, record_id: int, comment: str) -> bool:
        """添加审核意见"""
        record = self.get_record(record_id)
        if record:
            record.auditor_comment = comment
            self.session.commit()
            return True
        return False

    def list_records(
        self,
        status: Optional[str] = None,
        limit: int = 50
    ) -> list[AuditRecord]:
        """列出审核记录"""
        query = self.session.query(AuditRecord).order_by(
            AuditRecord.upload_time.desc()
        )
        if status:
            query = query.filter_by(status=status)
        return query.limit(limit).all()

    def add_workflow_log(
        self,
        record_id: int,
        action: str,
        details: Optional[str] = None
    ) -> WorkflowLog:
        """添加流转日志"""
        log = WorkflowLog(
            record_id=record_id,
            action=action,
            details=details
        )
        self.session.add(log)
        self.session.commit()
        return log

    def get_workflow_logs(self, record_id: int) -> list[WorkflowLog]:
        """获取流转日志"""
        return self.session.query(WorkflowLog).filter_by(
            record_id=record_id
        ).order_by(WorkflowLog.timestamp).all()

    def add_review_case(
        self,
        case_summary: str,
        human_decision: str,
        human_reason: str,
        record_id: Optional[int] = None,
        filename: Optional[str] = None,
        ai_issue: Optional[str] = None,
        learned_as_guideline: bool = False,
        guideline_text: Optional[str] = None
    ) -> ReviewCase:
        """保存人工反馈判例，用于后续审核参考。"""
        case = ReviewCase(
            record_id=record_id,
            filename=filename,
            case_summary=case_summary,
            ai_issue=ai_issue,
            human_decision=human_decision,
            human_reason=human_reason,
            learned_as_guideline=learned_as_guideline,
            guideline_text=guideline_text
        )
        self.session.add(case)
        self.session.commit()
        return case

    def list_review_cases(self, limit: int = 20) -> list[ReviewCase]:
        """列出最近的人工审核判例。"""
        return self.session.query(ReviewCase).order_by(
            ReviewCase.created_at.desc()
        ).limit(limit).all()

    def find_similar_review_cases(
        self,
        content: str,
        limit: int = 5
    ) -> list[ReviewCase]:
        """用简单关键词重叠检索相似判例，后续可替换为向量检索。"""
        cases = self.list_review_cases(limit=50)
        tokens = {
            token
            for token in _tokenize_for_similarity(content)
            if len(token) >= 2
        }
        if not tokens:
            return cases[:limit]

        scored_cases = []
        for case in cases:
            case_text = " ".join(
                value or ""
                for value in [
                    case.case_summary,
                    case.ai_issue,
                    case.human_decision,
                    case.human_reason,
                    case.guideline_text
                ]
            )
            case_tokens = set(_tokenize_for_similarity(case_text))
            score = len(tokens & case_tokens)
            if score > 0:
                scored_cases.append((score, case))

        scored_cases.sort(key=lambda item: item[0], reverse=True)
        return [case for _, case in scored_cases[:limit]]

    def delete_record(self, record_id: int) -> bool:
        """删除审核记录"""
        record = self.get_record(record_id)
        if record:
            self.session.delete(record)
            self.session.commit()
            return True
        return False

    def cleanup_old_files(self, days: int = 30) -> list[str]:
        """清理过期文件，返回被删除的文件路径列表"""
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=days)

        old_records = self.session.query(AuditRecord).filter(
            AuditRecord.upload_time < cutoff,
            AuditRecord.original_path.isnot(None)
        ).all()

        deleted_paths = []
        for record in old_records:
            if record.original_path:
                deleted_paths.append(record.original_path)
            self.session.delete(record)

        self.session.commit()
        return deleted_paths

    def close(self):
        self.session.close()


def _tokenize_for_similarity(text: str) -> list[str]:
    separators = "，。！？；：、（）()[]【】\n\r\t ,.;:!?\"'"
    normalized = text
    for separator in separators:
        normalized = normalized.replace(separator, " ")
    return [token.strip() for token in normalized.split() if token.strip()]
