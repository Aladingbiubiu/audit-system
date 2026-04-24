import re
from dataclasses import dataclass, field

from .auditor import AuditIssue
from .rule_model import (
    BudgetFacts,
    DocumentFacts,
    GroupProfile,
    PolicySelector,
    PresentmentFacts,
    Rule,
    RuleMetadata,
    RuleRunner,
)


@dataclass
class DeterministicResult:
    issues: list[AuditIssue] = field(default_factory=list)
    facts: dict[str, object] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        lines = []
        if self.facts:
            lines.append("## 程序规则判定")
            for key, value in self.facts.items():
                lines.append(f"- {key}: {value}")
        if self.notes:
            lines.append("## 程序规则说明")
            for note in self.notes:
                lines.append(f"- {note}")
        if self.issues:
            lines.append("## 程序已识别问题")
            for issue in self.issues:
                lines.append(
                    f"- [{issue.severity}] {issue.category}: {issue.description} ({issue.location})"
                )
        return "\n".join(lines) if lines else "## 程序规则判定\n- 暂无程序判定结果"


class DeterministicRuleEngine:
    def evaluate(self, page_texts: list[tuple[int, str]]) -> DeterministicResult:
        result = DeterministicResult()
        facts = self._extract_document_facts(page_texts)
        policy = PolicySelector.select(facts.profile)

        result.facts.update(self._format_facts_for_prompt(facts))
        result.notes.extend(policy.notes)
        result.notes.extend(self._build_policy_notes(facts))
        result.issues.extend(RuleRunner(self._build_rules()).run(facts, policy))
        return result

    def _extract_document_facts(self, page_texts: list[tuple[int, str]]) -> DocumentFacts:
        full_text = "\n".join(text for _, text in page_texts)
        pre_invitation_text = "\n".join(
            text for _, text in page_texts if not self._is_invitation_page(text)
        )

        presentment = self._parse_presentment_facts(page_texts)
        budget = self._parse_budget_facts(page_texts)
        group_unit_name = presentment.group_unit
        is_enterprise = self._is_enterprise_group(group_unit_name or "")
        is_academic = self._is_academic_group(group_unit_name or "") and not is_enterprise
        academic_modes = self._detect_academic_modes(full_text)

        has_personnel_list = self._has_personnel_list(full_text)
        has_public_notice = self._has_public_notice(pre_invitation_text)
        has_translation_info = (
            any(
                phrase in pre_invitation_text
                for phrase in ["担任翻译", "翻译情况", "同志担任翻译"]
            ) or ("翻译" in pre_invitation_text and "担任" in pre_invitation_text)
        )
        transport_refs = self._find_transport_references(page_texts)
        duration_values = self._extract_duration_values(page_texts, presentment)
        invite_units = self._extract_invite_units(page_texts, presentment)
        dispatch_source = self._detect_dispatch_source(full_text)
        dispatch_material = self._detect_dispatch_material(page_texts)
        expense_source = presentment.expense_source
        has_academic_group_marker = self._has_academic_group_marker(page_texts)

        is_long_term_visiting = (
            is_academic
            and "访学" in academic_modes
            and self._max_duration(duration_values) >= 90
        )
        is_academic_exchange_group = (
            is_academic
            and "访学" not in academic_modes
            and any(mode in academic_modes for mode in ["学术交流", "校际交流"])
        )

        if is_enterprise:
            group_type = "enterprise"
        elif is_academic:
            group_type = "academic"
        else:
            group_type = "government"

        profile = GroupProfile(
            group_type=group_type,
            academic_modes=academic_modes,
            is_enterprise=is_enterprise,
            is_academic=is_academic,
            is_long_term_visiting=is_long_term_visiting,
            is_academic_exchange_group=is_academic_exchange_group,
        )

        return DocumentFacts(
            page_texts=page_texts,
            full_text=full_text,
            pre_invitation_text=pre_invitation_text,
            profile=profile,
            presentment=presentment,
            budget=budget,
            group_unit_name=group_unit_name,
            has_personnel_list=has_personnel_list,
            has_public_notice=has_public_notice,
            has_translation_info=has_translation_info,
            transport_refs=transport_refs,
            duration_values=duration_values,
            invite_units=invite_units,
            dispatch_source=dispatch_source,
            dispatch_material=dispatch_material,
            expense_source=expense_source,
            has_academic_group_marker=has_academic_group_marker,
        )

    def _format_facts_for_prompt(self, facts: DocumentFacts) -> dict[str, object]:
        return {
            "呈报表类型": self._format_presentment_type(facts.presentment.presentment_type),
            "已提取组团单位": facts.group_unit_name or "未识别",
            "已提取出访地": facts.presentment.visit_destination or "未识别",
            "已提取经停地": facts.presentment.transit_destination or "未识别",
            "企业团组": "是" if facts.profile.is_enterprise else "否",
            "高校科研院所团组": "是" if facts.profile.is_academic else "否",
            "任务类型": "、".join(facts.profile.academic_modes) if facts.profile.academic_modes else "未识别",
            "已识别人员名单": "是" if facts.has_personnel_list else "否",
            "已识别公示情况": "是" if facts.has_public_notice else "否",
            "已识别翻译情况": "是" if facts.has_translation_info else "否",
            "已识别交通班次信息": "是" if facts.transport_refs else "否",
            "已提取停留天数": self._format_duration_facts(facts.duration_values),
            "已提取邀请单位": self._format_invite_unit_facts(facts.invite_units),
            "预算表已提取组团单位": facts.budget.group_unit or "未识别",
            "预算表已提取出访国别": facts.budget.visit_countries or "未识别",
            "预算表已提取出访天数": f"{facts.budget.duration_days}天" if facts.budget.duration_days is not None else "未识别",
            "已识别委派来源": facts.dispatch_source or "未识别",
            "已识别委派材料": facts.dispatch_material or "未识别",
            "已识别经费列支": facts.expense_source or "未识别",
            "已识别学术交流团组标注": "是" if facts.has_academic_group_marker else "否",
        }

    def _build_policy_notes(self, facts: DocumentFacts) -> list[str]:
        notes: list[str] = []
        if facts.profile.is_enterprise:
            notes.append(
                "已按企业团组处理；列入计划情况、周末公务情况、翻译情况、是否学术交流团缺失不直接作为问题。"
            )
        if facts.profile.is_academic:
            notes.append(
                "已按高校、科研院所团组处理；应优先区分访学、学术交流、校际交流，再按对应政策口径审核。"
            )
        if facts.profile.is_long_term_visiting:
            notes.append(
                "已识别为高校、科研院所长期访学团组（通常三个月以上）；如呈报表末尾已写明公示情况，不再机械要求列入计划情况、周末公务情况、翻译情况等表述。"
            )
        if facts.dispatch_source:
            notes.append(
                f"已识别委派来源为“{facts.dispatch_source}”；应关注是否附有相关委派材料，并核对呈报表“费用来源开支项目”栏是否与该委派来源一致。"
            )
        if facts.dispatch_material:
            notes.append(
                f"已通过附件第一页标题关键词识别到“{facts.dispatch_material}”类委派材料。"
            )
        if facts.profile.is_academic_exchange_group:
            notes.append(
                "已识别为高校、科研院所学术交流/校际交流团组；应重点检查呈报表最后一页是否标注类似“此团系学术交流团组”。"
            )
        if facts.has_personnel_list:
            notes.append("材料已出现“团组人员名单”或名单式内容，不得判为缺少人员名单。")
        if facts.has_public_notice:
            notes.append("材料已明确写出公示情况，如“已按规定进行公示，公示无异议”。")
        if facts.has_translation_info:
            notes.append("材料已明确写出翻译情况，如“某某同志担任翻译”。")
        if facts.transport_refs:
            notes.append("材料中已识别到交通班次信息，不应再误判为缺少航班号或车次。")
        return notes

    def _build_rules(self) -> list[Rule]:
        all_group_types = ("enterprise", "academic", "government")
        return [
            Rule(
                RuleMetadata(
                    id="text.banned_words",
                    name="禁用词审核",
                    layer="structure",
                    severity="严重",
                    applies_to=all_group_types,
                    requires_facts=("page_texts", "profile"),
                ),
                lambda facts: self._find_banned_word_issues(
                    facts.page_texts,
                    is_academic=facts.profile.is_academic,
                ),
            ),
            Rule(
                RuleMetadata(
                    id="duration.cross_material_consistency",
                    name="跨材料停留天数一致性",
                    layer="consistency",
                    severity="严重",
                    applies_to=all_group_types,
                    requires_facts=("duration_values",),
                ),
                lambda facts: self._find_duration_consistency_issues(facts.duration_values),
            ),
            Rule(
                RuleMetadata(
                    id="invite_unit.chinese_name_consistency",
                    name="邀请单位中文名称一致性",
                    layer="consistency",
                    severity="严重",
                    applies_to=all_group_types,
                    requires_facts=("invite_units",),
                ),
                lambda facts: self._find_invite_unit_consistency_issues(facts.invite_units),
            ),
            Rule(
                RuleMetadata(
                    id="academic_group.policy_checks",
                    name="高校科研院所团组策略审核",
                    layer="strategy",
                    severity="严重",
                    applies_to=("academic",),
                    requires_facts=(
                        "profile",
                        "has_public_notice",
                        "dispatch_source",
                        "dispatch_material",
                        "expense_source",
                        "has_academic_group_marker",
                    ),
                ),
                lambda facts: self._find_academic_group_policy_issues(
                    is_academic=facts.profile.is_academic,
                    academic_modes=facts.profile.academic_modes,
                    is_long_term_visiting=facts.profile.is_long_term_visiting,
                    has_public_notice=facts.has_public_notice,
                    dispatch_source=facts.dispatch_source,
                    dispatch_material=facts.dispatch_material,
                    expense_source=facts.expense_source,
                    has_academic_group_marker=facts.has_academic_group_marker,
                    page_texts=facts.page_texts,
                ),
            )
        ]

    def filter_llm_issues(
        self,
        llm_issues: list[AuditIssue],
        deterministic: DeterministicResult
    ) -> list[AuditIssue]:
        filtered = []
        is_enterprise = deterministic.facts.get("企业团组") == "是"
        is_academic = deterministic.facts.get("高校科研院所团组") == "是"
        task_modes = str(deterministic.facts.get("任务类型") or "")
        has_personnel_list = deterministic.facts.get("已识别人员名单") == "是"
        has_public_notice = deterministic.facts.get("已识别公示情况") == "是"
        has_translation_info = deterministic.facts.get("已识别翻译情况") == "是"
        has_transport_refs = deterministic.facts.get("已识别交通班次信息") == "是"
        is_long_term_visiting = is_academic and "访学" in task_modes and has_public_notice

        for issue in llm_issues:
            text = f"{issue.category} {issue.description} {issue.location}"
            if is_academic and self._is_academic_learning_issue(text):
                continue
            if self._is_banned_word_issue(issue):
                continue
            if self._is_document_code_issue(text):
                continue
            if self._is_allowed_learning_issue(text):
                continue
            if self._is_ocr_header_duplication_issue(text):
                continue
            if self._is_consistent_duration_issue(text):
                continue
            if self._is_consistent_group_size_issue(text):
                continue
            if self._is_uncertain_stamp_date_issue(text):
                continue
            if has_personnel_list and "人员名单" in text and any(word in text for word in ["未附", "未出现", "缺少", "缺失"]):
                continue
            if is_enterprise and any(word in text for word in ["列入计划情况", "周末公务情况", "翻译情况", "是否学术交流团"]):
                continue
            if is_long_term_visiting and any(word in text for word in ["列入计划情况", "周末公务情况", "翻译情况", "是否学术交流团"]):
                continue
            if has_public_notice and any(word in text for word in ["公示情况", "未公示", "未注明公示"]):
                continue
            if has_translation_info and any(word in text for word in ["翻译情况", "未注明翻译", "未写翻译"]):
                continue
            if has_transport_refs and any(word in text for word in ["缺少航班号", "未注明航班号", "缺少车次", "未注明车次"]):
                continue
            if self._is_duration_mismatch_issue(text):
                continue
            if self._is_invite_unit_mismatch_issue(text):
                continue
            filtered.append(issue)
        return filtered

    def _is_enterprise_group(self, text: str) -> bool:
        keywords = ["集团", "公司", "有限公司", "股份公司", "股份有限公司"]
        return any(keyword in text for keyword in keywords)

    def _is_academic_group(self, text: str) -> bool:
        keywords = ["大学", "学院", "学校", "研究院", "研究所", "实验室", "科研院所"]
        return any(keyword in text for keyword in keywords)

    def _parse_presentment_facts(self, page_texts: list[tuple[int, str]]) -> PresentmentFacts:
        for page_no, text in page_texts:
            if not self._is_presentment_page(text):
                continue

            presentment_type = self._detect_presentment_type(text)
            invite_units = self._extract_units_from_presentment(text)
            duration = self._extract_presentment_duration(text, presentment_type)
            return PresentmentFacts(
                presentment_type=presentment_type,
                page_no=page_no,
                group_unit=self._extract_group_unit_name_from_text(text),
                visit_destination=self._extract_single_presentment_field(
                    text,
                    "出访地",
                    stop_markers=["经停地", "停留时间", "在外停留", "邀请单位"],
                ),
                transit_destination=self._extract_single_presentment_field(
                    text,
                    "经停地",
                    stop_markers=["停留时间", "在外停留", "邀请单位", "费用来源"],
                ),
                duration_days=duration,
                invite_unit_cn=next(iter(invite_units), None),
                invite_unit_foreign=self._extract_invite_unit_foreign_from_presentment(text),
                expense_source=self._extract_expense_source_from_presentment_text(text),
                visit_reason=self._extract_presentment_visit_reason(text),
            )
        return PresentmentFacts()

    def _parse_budget_facts(self, page_texts: list[tuple[int, str]]) -> BudgetFacts:
        for page_no, text in page_texts:
            if not self._is_budget_page(text):
                continue

            group_unit = self._extract_single_budget_field(
                text,
                "组团单位",
                stop_markers=["团长", "团组人数", "出访国别"],
            )
            return BudgetFacts(
                page_no=page_no,
                unit_name=self._extract_single_budget_field(
                    text,
                    "单位名称",
                    stop_markers=["姓名", "姓  名", "职务", "职  务"],
                ) or group_unit,
                person_name=self._extract_single_budget_field(
                    text,
                    "姓名",
                    stop_markers=["职务", "职  务", "团组名称"],
                ),
                position=self._extract_single_budget_field(
                    text,
                    "职务",
                    stop_markers=["团组名称", "组团单位"],
                ),
                group_name=self._extract_single_budget_field(
                    text,
                    "团组名称",
                    stop_markers=["组团单位", "团长", "团组人数"],
                ),
                group_unit=group_unit,
                leader_level=self._extract_budget_leader_level(text),
                group_size=self._extract_single_budget_field(
                    text,
                    "团组人数",
                    stop_markers=["出访国别", "出访时间"],
                ),
                visit_countries=self._extract_budget_visit_countries(text),
                duration_days=self._extract_budget_duration_days(text),
                plan_included=self._extract_budget_yes_no_field(text, "是否列入出国计划"),
                time_country_compliant=self._extract_budget_yes_no_field(text, "时间和国别"),
                route_compliant=self._extract_budget_yes_no_field(text, "路线是否符合规定"),
                group_size_compliant=self._extract_budget_yes_no_field(text, "团组人数是否符合规定"),
                annual_budget_included=self._extract_budget_yes_no_field(text, "是否列入年度预算"),
                # 费用金额在 PDF/OCR 中常被横向表格拆散，暂不做自动金额抽取。
                total_cost=None,
                international_travel_cost=None,
                accommodation_cost=None,
                meal_cost=None,
                miscellaneous_cost=None,
                other_cost=None,
                pre_approval_items=self._extract_single_budget_field(
                    text,
                    "须事先报批的支出事项",
                    stop_markers=["其他事项", "审核意见"],
                ),
                other_matters=self._extract_single_budget_field(
                    text,
                    "其他事项",
                    stop_markers=["审核意见", "单位外事部门意见", "单位财务部门意见"],
                ),
            )
        return BudgetFacts()

    @staticmethod
    def _is_budget_page(text: str) -> bool:
        markers = ["预算审批意见表", "任务和预算审批意见表", "审核内容", "国际旅费", "单位财务部门意见"]
        return sum(1 for marker in markers if marker in text) >= 2

    def _extract_single_budget_field(
        self,
        text: str,
        label: str,
        *,
        stop_markers: list[str],
    ) -> str | None:
        values = self._extract_field_values_after_label(
            text,
            label,
            stop_markers=stop_markers,
            max_lines=6,
        )
        for value in values:
            cleaned = self._clean_budget_field_value(value)
            if cleaned:
                return cleaned
        return None

    @staticmethod
    def _clean_budget_field_value(text: str) -> str | None:
        cleaned = (text or "").strip(" ：:，,。；;、 \t\r\n")
        if not cleaned:
            return None
        ignored_values = {
            "单位名称",
            "姓名",
            "姓  名",
            "职务",
            "职  务",
            "团组名称",
            "组团单位",
            "团长（级别）",
            "团组人数",
            "审核内容",
        }
        if cleaned in ignored_values:
            return None
        return cleaned

    def _extract_budget_visit_countries(self, text: str) -> str | None:
        value = self._extract_single_budget_field(
            text,
            "出访国别",
            stop_markers=["出访时间", "审核内容", "是否列入出国计划"],
        )
        if value and value not in ["（地区）（含经停）", "(地区)(含经停)"]:
            return value

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            if "出访国别" not in line:
                continue
            for candidate in lines[index + 1:index + 5]:
                cleaned = self._clean_budget_country_value(candidate)
                if cleaned:
                    return cleaned
            for candidate in reversed(lines[max(0, index - 3):index]):
                cleaned = self._clean_budget_country_value(candidate)
                if cleaned:
                    return cleaned
        return None

    @staticmethod
    def _clean_budget_country_value(text: str) -> str | None:
        cleaned = DeterministicRuleEngine._clean_budget_field_value(text)
        if not cleaned:
            return None
        if not DeterministicRuleEngine._has_chinese_text(cleaned):
            return None
        invalid_markers = [
            "大学",
            "学院",
            "学校",
            "单位",
            "团组",
            "出访时间",
            "姓名",
            "职务",
            "是否",
            "符合规定",
            "审核内容",
            "预算",
        ]
        if any(marker in cleaned for marker in invalid_markers):
            return None
        if len(cleaned) > 30:
            return None
        return cleaned

    @staticmethod
    def _extract_budget_duration_days(text: str) -> int | None:
        patterns = [
            r"出访时间（天数）\s*(\d+)\s*天?",
            r"出访时间\(天数\)\s*(\d+)\s*天?",
            r"出访时间[^\d]{0,20}(\d+)\s*天",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return int(match.group(1))
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            if "出访时间" not in line:
                continue
            for candidate in lines[index + 1:index + 5]:
                match = re.fullmatch(r"\d{1,3}", candidate.strip())
                if match:
                    return int(match.group(0))
        return None

    @staticmethod
    def _extract_budget_leader_level(text: str) -> str | None:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            if "级别" not in line:
                continue
            for candidate in lines[index + 1:index + 4]:
                cleaned = DeterministicRuleEngine._clean_budget_field_value(candidate)
                if cleaned:
                    return cleaned
        return None

    @staticmethod
    def _extract_budget_yes_no_field(text: str, label: str) -> str | None:
        pattern = rf"{re.escape(label)}[^：:\n]*[:：]?\s*([是否])"
        match = re.search(pattern, text)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _is_presentment_page(text: str) -> bool:
        markers = ["呈报表", "出呈字", "出随字", "组团单位", "出访地", "邀请单位"]
        return sum(1 for marker in markers if marker in text) >= 3

    @staticmethod
    def _detect_presentment_type(text: str) -> str:
        if "随团任务呈报表" in text or "出随字" in text or "随 团人员" in text or "随团人员" in text:
            return "delegation"
        if "任务呈报表" in text or "出呈字" in text:
            return "group"
        return "unknown"

    @staticmethod
    def _format_presentment_type(presentment_type: str) -> str:
        mapping = {
            "group": "组团任务呈报表",
            "delegation": "随团任务呈报表",
            "unknown": "未识别",
        }
        return mapping.get(presentment_type, presentment_type or "未识别")

    def _extract_single_presentment_field(
        self,
        text: str,
        label: str,
        *,
        stop_markers: list[str],
    ) -> str | None:
        values = self._extract_field_values_after_label(
            text,
            label,
            stop_markers=stop_markers,
            max_lines=6,
        )
        for value in values:
            cleaned = self._clean_presentment_field_value(value)
            if cleaned:
                return cleaned
        return None

    @staticmethod
    def _clean_presentment_field_value(text: str) -> str | None:
        cleaned = (text or "").strip(" ：:，,。；;、 \t\r\n")
        if not cleaned:
            return None
        ignored_values = {"出访地", "经停地", "停留时间", "在外停留", "邀请单位"}
        if cleaned in ignored_values:
            return None
        if re.fullmatch(r"[\d\s\-—年月日]+", cleaned):
            return None
        return cleaned

    @staticmethod
    def _extract_presentment_duration(text: str, presentment_type: str) -> int | None:
        patterns = [
            r"停留时间\s*(\d+)\s*天",
            r"在外停留\s*(\d+)\s*天",
        ]
        if presentment_type == "delegation":
            patterns.insert(0, r"在外停留\s*[^\d]{0,10}(\d+)\s*天?")
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _extract_invite_unit_foreign_from_presentment(text: str) -> str | None:
        values = DeterministicRuleEngine._extract_field_values_after_label(
            text,
            "邀请单位",
            stop_markers=["费用来源", "开支项目", "出访地", "经停地", "停留时间"],
            max_lines=8,
        )
        for value in values:
            if re.search(r"[A-Za-z]{3,}", value):
                return value.strip(" ：:，,。；;、 \t\r\n")
        return None

    @staticmethod
    def _extract_presentment_visit_reason(text: str) -> str | None:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        start_index = None
        for index, line in enumerate(lines):
            compact = line.replace(" ", "")
            if compact in {"出访事由", "出访事由"}:
                start_index = index + 1
                break
            if "出访事由" in compact:
                start_index = index + 1
                break
        if start_index is None:
            return None

        stop_markers = ["单位公章", "团组人员名单", "附件：", "附件"]
        values = []
        for line in lines[start_index:start_index + 12]:
            if any(marker in line for marker in stop_markers):
                break
            cleaned = line.strip(" ：:，,。；;、 \t\r\n")
            if cleaned:
                values.append(cleaned)
        return " ".join(values).strip() or None

    def _extract_group_unit_name(self, page_texts: list[tuple[int, str]]) -> str | None:
        presentment_pages = [
            text for _, text in page_texts
            if "呈报表" in text or "出呈字" in text or "组团单位" in text
        ]
        for text in presentment_pages:
            extracted = self._extract_group_unit_name_from_text(text)
            if extracted:
                return extracted
        return None

    @staticmethod
    def _extract_group_unit_name_from_text(text: str) -> str | None:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            same_line = re.search(r"组团单位\s*[:：]?\s*(.+)$", line)
            if same_line:
                value = same_line.group(1).strip()
                if value and value != "组团单位":
                    cleaned = DeterministicRuleEngine._clean_group_unit_name(value)
                    if cleaned:
                        return cleaned

            if line != "组团单位":
                continue

            for next_line in lines[index + 1:index + 8]:
                cleaned = DeterministicRuleEngine._clean_group_unit_name(next_line)
                if cleaned:
                    return cleaned
        return None

    @staticmethod
    def _clean_group_unit_name(text: str) -> str | None:
        cleaned = (text or "").strip(" ：:，,。；;、 \t\r\n")
        if not cleaned:
            return None
        ignored_values = {"组团单位", "（全称）", "(全称)", "全称", "组织机构代码", "统一社会信用代码"}
        if cleaned in ignored_values:
            return None
        if re.fullmatch(r"[A-Z0-9]{6,}", cleaned):
            return None
        if re.fullmatch(r"[\d\s\-—]+", cleaned):
            return None
        return cleaned

    def _has_personnel_list(self, text: str) -> bool:
        if "团组人员名单" in text or "人员名单" in text:
            return True
        required_fields = ["姓名", "身份证号码", "工作单位及职务"]
        return all(field in text for field in required_fields)

    @staticmethod
    def _has_public_notice(text: str) -> bool:
        direct_phrases = [
            "公示无异议",
            "已按规定进行公示",
            "已按规定公示",
            "按规定进行公示",
            "按规定公示",
            "已公示",
            "公示情况",
            "经公示无异议",
            "群众无异议",
        ]
        if any(phrase in text for phrase in direct_phrases):
            return True

        has_notice_word = "公示" in text
        has_no_objection_word = any(
            phrase in text for phrase in ["无异议", "群众无异议", "无不同意见"]
        )
        return has_notice_word and has_no_objection_word

    @staticmethod
    def _detect_academic_modes(text: str) -> list[str]:
        modes = []
        if "访学" in text:
            modes.append("访学")
        if "校际交流" in text:
            modes.append("校际交流")
        if "学术交流" in text:
            modes.append("学术交流")
        return modes

    def _find_banned_word_issues(
        self,
        page_texts: list[tuple[int, str]],
        is_academic: bool = False
    ) -> list[AuditIssue]:
        banned_words = ["参观", "考察", "调研", "学习"]
        findings = []
        for page_no, text in page_texts:
            if self._is_invitation_page(text):
                continue
            if not self._is_core_review_page(text):
                continue

            for word in banned_words:
                if word not in text:
                    continue
                if word == "学习" and is_academic:
                    continue
                if word == "学习" and any(marker in text for marker in ["培训", "访学", "学术交流"]):
                    continue
                findings.append((word, page_no, self._extract_keyword_snippet(text, word)))

        if not findings:
            return []

        unique_words = []
        locations = []
        examples = []
        for word, page_no, snippet in findings:
            if word not in unique_words:
                unique_words.append(word)
            page_label = f"第{page_no}页"
            if page_label not in locations:
                locations.append(page_label)
            if len(examples) < 3:
                examples.append(f"{word}：{snippet}")

        description = (
            f"材料中出现禁用词 {'、'.join(f'“{word}”' for word in unique_words)}，"
            "属于严重问题，需统一修改。"
        )
        if examples:
            description += f" 示例：{'；'.join(examples)}"

        return [
            AuditIssue(
                severity="严重",
                category="文字规范审核",
                description=description,
                location="、".join(locations)
            )
        ]

    def _find_transport_references(self, page_texts: list[tuple[int, str]]) -> list[tuple[int, str]]:
        refs = []
        patterns = [
            r"\b[A-Z]{2}\d{2,4}\b",
            r"\b[A-Z]{1,3}\d{2,4}\b",
            r"\b\d{1,4}次\b",
            r"\b航班\s*[A-Z0-9]{2,6}\b",
        ]
        for page_no, text in page_texts:
            if "日程安排" not in text and "周" not in text and "上午" not in text and "下午" not in text:
                continue
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    refs.append((page_no, match.group(0)))
                    break
        return refs

    def _extract_duration_values(
        self,
        page_texts: list[tuple[int, str]],
        presentment: PresentmentFacts | None = None,
    ) -> dict[str, tuple[int, int]]:
        values: dict[str, tuple[int, int]] = {}
        if presentment and presentment.duration_days is not None:
            values["呈报表"] = (presentment.page_no or 0, presentment.duration_days)

        patterns = [
            ("呈报表", [r"停留时间\s*(\d+)\s*天"]),
            ("邀请函翻译件", [r"在国外停留时间[:：]?\s*(\d+)\s*天", r"停留时间[:：]?\s*(\d+)\s*天"]),
            ("预算审批意见表", [r"出访时间（天数）\s*(\d+)\s*天", r"出访时间\(天数\)\s*(\d+)\s*天"]),
        ]

        for page_no, text in page_texts:
            for label, rule_patterns in patterns:
                if label in values:
                    continue
                if label == "邀请函翻译件" and ("邀请函" not in text or self._is_english_only_invitation(text)):
                    continue
                if label == "预算审批意见表" and "预算审批意见表" not in text:
                    continue
                if label == "呈报表" and "呈报表" not in text:
                    continue

                for pattern in rule_patterns:
                    match = re.search(pattern, text)
                    if match:
                        values[label] = (page_no, int(match.group(1)))
                        break
        return values

    def _extract_invite_units(
        self,
        page_texts: list[tuple[int, str]],
        presentment: PresentmentFacts | None = None,
    ) -> dict[str, tuple[int, set[str]]]:
        units: dict[str, tuple[int, set[str]]] = {}
        if presentment and presentment.invite_unit_cn:
            units["呈报表"] = (presentment.page_no or 0, {presentment.invite_unit_cn})

        for page_no, text in page_texts:
            if "呈报表" in text and "邀请单位" in text and "呈报表" not in units:
                extracted = self._extract_units_from_presentment(text)
                if extracted:
                    units["呈报表"] = (page_no, extracted)

            if self._is_real_chinese_invitation_page(text):
                extracted = self._extract_units_from_invitation(text)
                if extracted:
                    key = f"邀请函翻译件-第{page_no}页"
                    units[key] = (page_no, extracted)
        return units

    def _find_duration_consistency_issues(self, duration_values: dict[str, tuple[int, int]]) -> list[AuditIssue]:
        if len(duration_values) < 2:
            return []

        values = {label: value for label, (_, value) in duration_values.items()}
        if len(set(values.values())) == 1:
            return []

        description = "停留时间跨材料不一致：" + "；".join(
            f"{label}{value}天"
            for label, value in values.items()
        )
        location = "、".join(
            f"第{page_no}页"
            for page_no, _ in duration_values.values()
        )
        return [AuditIssue("严重", "跨材料一致性校验", description, location)]

    def _find_invite_unit_consistency_issues(self, invite_units: dict[str, tuple[int, set[str]]]) -> list[AuditIssue]:
        if "呈报表" not in invite_units:
            return []

        base_page, base_units = invite_units["呈报表"]
        if not base_units:
            return []

        mismatches = []
        locations = [f"第{base_page}页"]
        for label, (page_no, units) in invite_units.items():
            if label == "呈报表":
                continue
            locations.append(f"第{page_no}页")
            if not self._invite_units_match(base_units, units):
                mismatches.append((label, units))

        if not mismatches:
            return []

        description = "邀请单位中文名称跨材料不一致：呈报表为" + "、".join(sorted(base_units))
        for label, units in mismatches:
            description += f"；{label}为" + "、".join(sorted(units))

        return [AuditIssue("严重", "跨材料一致性校验", description, "、".join(dict.fromkeys(locations)))]

    def _find_academic_group_policy_issues(
        self,
        *,
        is_academic: bool,
        academic_modes: list[str],
        is_long_term_visiting: bool,
        has_public_notice: bool,
        dispatch_source: str | None,
        dispatch_material: str | None,
        expense_source: str | None,
        has_academic_group_marker: bool,
        page_texts: list[tuple[int, str]],
    ) -> list[AuditIssue]:
        if not is_academic:
            return []

        issues: list[AuditIssue] = []

        if is_long_term_visiting and not has_public_notice:
            issues.append(
                AuditIssue(
                    "严重",
                    "呈报表审核",
                    "高校、科研院所长期访学团组（通常三个月以上）应在呈报表末尾明确写明公示情况，当前未稳定识别到相关表述。",
                    self._find_presentment_last_page(page_texts),
                )
            )

        if dispatch_source and not dispatch_material:
            issues.append(
                AuditIssue(
                    "严重",
                    "呈报表审核",
                    f"已识别该高校、科研院所团组属于“{dispatch_source}”情形，但当前未通过附件第一页标题稳定识别到相应委派材料。",
                    self._find_dispatch_keyword_location(page_texts),
                )
            )

        if dispatch_source and expense_source and not self._dispatch_matches_expense(dispatch_source, expense_source):
            issues.append(
                AuditIssue(
                    "严重",
                    "跨材料一致性校验",
                    f"高校、科研院所委派来源与呈报表“费用来源开支项目”疑似不一致：委派来源为“{dispatch_source}”，该栏中识别为“{expense_source}”。",
                    self._find_presentment_and_dispatch_locations(page_texts),
                )
            )

        if (
            "访学" not in academic_modes
            and any(mode in academic_modes for mode in ["学术交流", "校际交流"])
            and not has_academic_group_marker
        ):
            issues.append(
                AuditIssue(
                    "严重",
                    "呈报表审核",
                    "高校、科研院所学术交流/校际交流团组应在呈报表最后一页标注类似“此团系学术交流团组”的说明，当前未稳定识别到该标注。",
                    self._find_presentment_last_page(page_texts),
                )
            )

        return issues

    @staticmethod
    def _invite_units_match(base_units: set[str], other_units: set[str]) -> bool:
        if not base_units or not other_units:
            return False

        def normalize(unit: str) -> str:
            return DeterministicRuleEngine._normalize_invite_unit_for_match(unit)

        normalized_base = {normalize(unit) for unit in base_units if normalize(unit)}
        normalized_other = {normalize(unit) for unit in other_units if normalize(unit)}

        for base in normalized_base:
            if any(base in other or other in base for other in normalized_other):
                return True
        return False

    @staticmethod
    def _detect_dispatch_source(text: str) -> str | None:
        rules = [
            ("国家留学基金委", ["国家留学基金委", "国家公派留学", "国家留学基金管理委员会"]),
            ("省派", ["省派", "省公派", "省公派留学"]),
            ("校派", ["校派", "学校公派", "校级公派"]),
        ]
        for label, markers in rules:
            if any(marker in text for marker in markers):
                return label
        return None

    def _detect_dispatch_material(self, page_texts: list[tuple[int, str]]) -> str | None:
        for _, text in page_texts:
            title_scope = "\n".join(
                line.strip() for line in text.splitlines()[:8] if line.strip()
            )
            if not title_scope:
                continue
            if self._is_non_dispatch_material_page(title_scope):
                continue
            if "国家留学基金委" in title_scope:
                return "国家留学基金委"
            if "省" in title_scope and any(marker in title_scope for marker in ["公派出国留学", "公派留学", "省派"]):
                return "省派"
            if any(marker in title_scope for marker in ["通知", "关于"]) and any(
                marker in title_scope for marker in ["大学", "学院", "学校", "研究院", "研究所"]
            ):
                return "校派"
        return None

    @staticmethod
    def _is_non_dispatch_material_page(text: str) -> bool:
        markers = [
            "呈报表",
            "团组人员名单",
            "人员名单",
            "日程安排",
            "邀请函",
            "预算审批意见表",
            "情况说明",
        ]
        return any(marker in text for marker in markers)

    @staticmethod
    def _extract_presentment_expense_source(page_texts: list[tuple[int, str]]) -> str | None:
        presentment_text = "\n".join(
            text for _, text in page_texts
            if "呈报表" in text or "出呈字" in text
        )
        if not presentment_text:
            return None

        return DeterministicRuleEngine._extract_expense_source_from_presentment_text(presentment_text)

    @staticmethod
    def _extract_expense_source_from_presentment_text(presentment_text: str) -> str | None:
        field_values = DeterministicRuleEngine._extract_field_values_after_label(
            presentment_text,
            "费用来源",
            stop_markers=[
                "开支项目",
                "出访事由",
                "邀请单位",
                "出访地",
                "经停地",
                "停留时间",
            ],
            max_lines=8,
        )
        for value in field_values:
            cleaned = DeterministicRuleEngine._clean_expense_source_value(value)
            if cleaned:
                return cleaned

        patterns = [
            r"费用来源开支项目\s*[:：]?\s*([^\n]{0,80})",
            r"费用来源开支项目[^\n]{0,20}?([^\n]{0,80})",
            r"经费列支情况\s*[:：]?\s*([^\n]{0,80})",
            r"费用由([^\n]{0,50})承担",
        ]

        for pattern in patterns:
            match = re.search(pattern, presentment_text)
            if match:
                value = DeterministicRuleEngine._clean_expense_source_value(match.group(1))
                if value:
                    return value
        return None

    @staticmethod
    def _clean_expense_source_value(text: str) -> str | None:
        cleaned = " ".join((text or "").split()).strip("：:，,。；; ")
        if not cleaned:
            return None
        ignored_values = {"费用来源", "开支项目", "费用来源开支项目"}
        if cleaned in ignored_values:
            return None
        if not DeterministicRuleEngine._has_chinese_text(cleaned):
            return None
        return cleaned

    @staticmethod
    def _dispatch_matches_expense(dispatch_source: str, expense_source: str) -> bool:
        if "派员单位承担" in expense_source:
            return True
        mapping = {
            "国家留学基金委": ["国家留学基金委", "国家公派", "国家公派留学", "留学基金委承担"],
            "省派": ["省派", "省公派", "省公派留学", "省派承担"],
            "校派": ["校派", "学校", "校级", "校公派", "派员单位承担"],
        }
        return any(marker in expense_source for marker in mapping.get(dispatch_source, []))

    @staticmethod
    def _has_academic_group_marker(page_texts: list[tuple[int, str]]) -> bool:
        markers = ["此团系学术交流团组", "系学术交流团组", "属于学术交流团组"]
        return any(
            any(marker in text for marker in markers)
            for _, text in page_texts
            if "呈报表" in text
        )

    @staticmethod
    def _max_duration(duration_values: dict[str, tuple[int, int]]) -> int:
        if not duration_values:
            return 0
        return max(value for _, value in duration_values.values())

    @staticmethod
    def _find_presentment_last_page(page_texts: list[tuple[int, str]]) -> str:
        pages = [page_no for page_no, text in page_texts if "呈报表" in text]
        if not pages:
            return "位置待人工核对"
        return f"第{max(pages)}页"

    @staticmethod
    def _find_presentment_and_dispatch_locations(page_texts: list[tuple[int, str]]) -> str:
        locations = []
        presentment_pages = [page_no for page_no, text in page_texts if "呈报表" in text]
        if presentment_pages:
            locations.append(f"第{max(presentment_pages)}页")
        dispatch_pages = [
            page_no for page_no, text in page_texts
            if any(marker in text for marker in ["国家留学基金委", "省派", "省公派", "校派", "国家公派留学"])
        ]
        if dispatch_pages:
            locations.extend(f"第{page}页" for page in dispatch_pages[:2])
        return "、".join(dict.fromkeys(locations)) if locations else "位置待人工核对"

    @staticmethod
    def _find_dispatch_keyword_location(page_texts: list[tuple[int, str]]) -> str:
        dispatch_pages = [
            page_no for page_no, text in page_texts
            if any(marker in text for marker in ["国家留学基金委", "省派", "省公派", "校派", "国家公派留学"])
        ]
        if dispatch_pages:
            return "、".join(f"第{page}页" for page in dict.fromkeys(dispatch_pages))
        return "位置待人工核对"

    @staticmethod
    def _is_core_review_page(text: str) -> bool:
        markers = [
            "呈报表",
            "日程安排",
            "情况说明",
            "团组人员名单",
            "人员名单",
            "预算审批意见表",
        ]
        return any(marker in text for marker in markers)

    @staticmethod
    def _is_banned_word_issue(issue: AuditIssue) -> bool:
        text = f"{issue.category} {issue.description}"
        return any(keyword in text for keyword in ["参观", "考察", "调研", "学习"])

    @staticmethod
    def _is_academic_learning_issue(text: str) -> bool:
        return "学习" in text

    @staticmethod
    def _is_document_code_issue(text: str) -> bool:
        keywords = ["文号", "出呈字", "格式不规范", "不符合规范格式"]
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _is_allowed_learning_issue(text: str) -> bool:
        return "学习" in text and any(marker in text for marker in ["培训", "访学", "学术交流"])

    @staticmethod
    def _is_ocr_header_duplication_issue(text: str) -> bool:
        header_keywords = ["组团单位", "团组人数", "出访日期", "邀请单位", "工作单位及职务"]
        duplication_markers = ["重复填写", "重复出现", "填写两次", "格式不规范"]
        return any(keyword in text for keyword in header_keywords) and any(marker in text for marker in duplication_markers)

    @staticmethod
    def _is_consistent_duration_issue(text: str) -> bool:
        duration_keywords = ["停留时间", "在外停留天数", "日期区间", "出访日期"]
        contradiction_markers = ["实际为", "计算为", "共", "一致"]
        false_alarm_markers = ["不符", "不一致", "表述方式不规范", "格式不规范"]
        return (
            any(keyword in text for keyword in duration_keywords)
            and any(marker in text for marker in contradiction_markers)
            and any(marker in text for marker in false_alarm_markers)
        )

    @staticmethod
    def _is_consistent_group_size_issue(text: str) -> bool:
        keywords = ["团组人数", "人员名单", "姓名", "2人", "3人", "4人"]
        false_alarm_markers = ["格式不规范", "不规范", "重复", "但实际列出"]
        return any(keyword in text for keyword in keywords) and any(marker in text for marker in false_alarm_markers)

    @staticmethod
    def _is_uncertain_stamp_date_issue(text: str) -> bool:
        location_keywords = ["单位盖章", "落款", "保证书", "签字", "印章"]
        date_keywords = ["日期格式", "日期错误", "无法确定具体日期", "落款日期", "盖章日期"]
        return any(keyword in text for keyword in location_keywords) and any(keyword in text for keyword in date_keywords)

    @staticmethod
    def _is_duration_mismatch_issue(text: str) -> bool:
        keywords = ["停留时间", "在外停留天数", "出访时间（天数）", "出访时间(天数)", "90天", "91天", "不一致"]
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _is_invite_unit_mismatch_issue(text: str) -> bool:
        keywords = ["邀请单位", "不一致", "中文名称", "跨材料"]
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _extract_keyword_snippet(text: str, keyword: str, radius: int = 24) -> str:
        index = text.find(keyword)
        if index < 0:
            return keyword
        snippet = text[max(0, index - radius): index + radius]
        return " ".join(snippet.split())

    @staticmethod
    def _extract_units_from_presentment(text: str) -> set[str]:
        field_values = DeterministicRuleEngine._extract_field_values_after_label(
            text,
            "邀请单位",
            stop_markers=[
                "(中外文)",
                "（中外文）",
                "费用来源",
                "开支项目",
                "出访地",
                "经停地",
                "停留时间",
            ],
            max_lines=8,
        )
        if field_values:
            return {
                DeterministicRuleEngine._clean_invite_unit_candidate(value)
                for value in field_values
                if DeterministicRuleEngine._clean_invite_unit_candidate(value)
            }

        match = re.search(r"邀请单位\s*(.*?)\s*\(中外文\)", text, re.S)
        if not match:
            return set()
        return DeterministicRuleEngine._extract_chinese_unit_names(match.group(1))

    @staticmethod
    def _extract_units_from_invitation(text: str) -> set[str]:
        semantic_patterns = [
            r"作为主办机构[，,]\s*([^。\n；;]{2,80}?)(?:诚挚地)?邀请",
            r"(?:应|受)([^。\n；;]{2,80}?)(?:邀请)",
            r"(?:邀请单位|主办单位)\s*[:：]?\s*([^。\n；;]{2,80})",
            r"([^。\n；;]{2,80}?)(?:诚挚地|诚挚)?邀请(?:您|贵校|代表团|访问|前来)",
        ]
        semantic_units = DeterministicRuleEngine._extract_units_by_context_patterns(
            text,
            semantic_patterns,
            skip_noisy_context=False,
        )
        if semantic_units:
            return semantic_units

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        candidates = []
        for line in lines[:8]:
            if DeterministicRuleEngine._is_noisy_invite_unit_context(line):
                continue
            if re.search(r"[\u4e00-\u9fff]{2,}", line):
                candidates.append(line)
        return DeterministicRuleEngine._extract_chinese_unit_names(
            " ".join(candidates[:3]),
            skip_noisy_context=True,
        )

    @staticmethod
    def _extract_chinese_unit_names(text: str, skip_noisy_context: bool = False) -> set[str]:
        normalized = text.replace("，", " ").replace(",", " ").replace("、", " ")
        unit_pattern = r"[\u4e00-\u9fff（）()《》“”]{2,}(?:股份有限公司|有限公司|股份公司|大学|学院|集团|公司|研究院|研究所)"
        cleaned = set()
        for match in re.finditer(unit_pattern, normalized):
            context_start = max(0, match.start() - 16)
            context_end = min(len(normalized), match.end() + 16)
            context = normalized[context_start:context_end]
            if skip_noisy_context and DeterministicRuleEngine._is_noisy_invite_unit_context(context):
                continue
            candidate = DeterministicRuleEngine._clean_chinese_unit_name(match.group(0))
            if candidate:
                cleaned.add(candidate)
        return {item for item in cleaned if item}

    @staticmethod
    def _extract_field_values_after_label(
        text: str,
        label: str,
        *,
        stop_markers: list[str],
        max_lines: int,
    ) -> list[str]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            same_line = re.search(rf"{re.escape(label)}\s*[:：]?\s*(.+)$", line)
            if same_line:
                value = same_line.group(1).strip()
                if value and value != label:
                    return [value]

            if line != label:
                continue

            values = []
            for next_line in lines[index + 1:index + 1 + max_lines]:
                if any(marker in next_line for marker in stop_markers):
                    break
                if DeterministicRuleEngine._is_noise_invite_unit_value(next_line):
                    continue
                values.append(next_line)
                if DeterministicRuleEngine._has_chinese_text(next_line):
                    break
            return values
        return []

    @staticmethod
    def _clean_invite_unit_candidate(text: str) -> str | None:
        cleaned = (text or "").strip(" ：:，,。；;、 \t\r\n《》“”\"'")
        if not cleaned:
            return None
        if DeterministicRuleEngine._is_noise_invite_unit_value(cleaned):
            return None
        if not DeterministicRuleEngine._has_chinese_text(cleaned):
            return None
        return cleaned

    @staticmethod
    def _has_chinese_text(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]{2,}", text or ""))

    @staticmethod
    def _is_noise_invite_unit_value(text: str) -> bool:
        stripped = (text or "").strip()
        if not stripped:
            return True
        noise_values = {
            "邀请单位",
            "(中外文)",
            "（中外文）",
            "中外文",
            "全称",
        }
        if stripped in noise_values:
            return True
        if re.fullmatch(r"[A-Za-z0-9\s,，.()（）&\-]+", stripped):
            return True
        return False

    @staticmethod
    def _extract_units_by_context_patterns(
        text: str,
        patterns: list[str],
        *,
        skip_noisy_context: bool,
    ) -> set[str]:
        units: set[str] = set()
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                scope = match.group(1) if match.groups() else match.group(0)
                scope = DeterministicRuleEngine._trim_invite_unit_scope(scope)
                if skip_noisy_context and DeterministicRuleEngine._is_noisy_invite_unit_context(scope):
                    continue
                cleaned = DeterministicRuleEngine._clean_invite_unit_candidate(scope)
                if cleaned:
                    units.add(cleaned)
            if units:
                return units
        return units

    @staticmethod
    def _trim_invite_unit_scope(text: str) -> str:
        scope = text or ""
        boundary_markers = [
            "联系人",
            "联系地址",
            "地址",
            "电话",
            "邮箱",
            "电子邮件",
            "抄送",
            "附件",
            "承办单位",
        ]
        indexes = [scope.find(marker) for marker in boundary_markers if marker in scope]
        if indexes:
            scope = scope[:min(indexes)]
        return scope.strip(" ：:，,。；;、 \t\r\n")

    @staticmethod
    def _clean_chinese_unit_name(unit: str) -> str:
        cleaned = (unit or "").replace("(", "（").replace(")", "）").strip()
        cleaned = cleaned.strip(" ：:，,。；;、 \t\r\n《》“”\"'")
        prefix_patterns = [
            r"^.*?联邦国家预算高等教育机构[“”\"]?",
            r"^.*?国家预算高等教育机构[“”\"]?",
            r"^.*?高等教育机构[“”\"]?",
            r"^.*?应",
            r"^.*?受",
        ]
        for pattern in prefix_patterns:
            cleaned = re.sub(pattern, "", cleaned).strip(" ：:，,。；;、 \t\r\n《》“”\"'")
        cleaned = re.sub(r"^(邀请单位|主办单位|承办单位)\s*[:：]?", "", cleaned)
        return cleaned.strip(" ：:，,。；;、 \t\r\n《》“”\"'")

    @staticmethod
    def _normalize_invite_unit_for_match(unit: str) -> str:
        normalized = DeterministicRuleEngine._clean_chinese_unit_name(unit)
        remove_tokens = [
            "中国",
            "中华人民共和国",
            "俄罗斯联邦",
            "联邦国家预算高等教育机构",
            "国家预算高等教育机构",
            "高等教育机构",
            "（",
            "）",
            "(",
            ")",
            " ",
            "\u3000",
            "《",
            "》",
            "“",
            "”",
            "\"",
        ]
        for token in remove_tokens:
            normalized = normalized.replace(token, "")
        return normalized.strip()

    @staticmethod
    def _is_noisy_invite_unit_context(text: str) -> bool:
        noise_markers = [
            "承办单位",
            "联系人",
            "联系地址",
            "地址",
            "电话",
            "邮箱",
            "电子邮件",
            "抄送",
            "附件",
            "通知",
            "批复",
            "证明",
            "经费",
            "费用",
            "派出",
            "派员",
            "工作单位",
        ]
        return any(marker in text for marker in noise_markers)

    @staticmethod
    def _is_english_only_invitation(text: str) -> bool:
        english_markers = ["Invitation Letter", "To Whom It May Concern"]
        chinese_markers = ["中国", "邀请函", "时间:", "在国外停留时间"]
        return any(marker in text for marker in english_markers) and not any(marker in text for marker in chinese_markers)

    @staticmethod
    def _is_real_chinese_invitation_page(text: str) -> bool:
        required_markers = ["邀请函"]
        context_markers = ["时间:", "在国外停留时间", "地点:", "参与形式", "致有关人士"]
        semantic_markers = ["作为主办机构", "诚挚地邀请", "诚挚邀请", "邀请您", "邀请贵校"]
        if all(marker in text for marker in required_markers) and any(marker in text for marker in context_markers):
            return True
        return any(marker in text for marker in semantic_markers)

    @staticmethod
    def _format_duration_facts(duration_values: dict[str, tuple[int, int]]) -> str:
        if not duration_values:
            return "未识别"
        return "；".join(f"{label}{value}天" for label, (_, value) in duration_values.items())

    @staticmethod
    def _format_invite_unit_facts(invite_units: dict[str, tuple[int, set[str]]]) -> str:
        if not invite_units:
            return "未识别"
        parts = []
        for label, (_, units) in invite_units.items():
            if units:
                parts.append(f"{label}:" + "、".join(sorted(units)))
        return "；".join(parts) if parts else "未识别"

    @staticmethod
    def _is_invitation_page(text: str) -> bool:
        markers = ["邀请函", "Invitation Letter", "To Whom It May Concern"]
        return any(marker in text for marker in markers)
