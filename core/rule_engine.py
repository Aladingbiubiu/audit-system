import re
from dataclasses import dataclass, field

from .auditor import AuditIssue


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
        full_text = "\n".join(text for _, text in page_texts)
        pre_invitation_text = "\n".join(
            text for _, text in page_texts
            if not self._is_invitation_page(text)
        )

        is_enterprise = self._is_enterprise_group(full_text)
        has_personnel_list = self._has_personnel_list(full_text)
        has_public_notice = any(
            phrase in pre_invitation_text
            for phrase in ["公示无异议", "已按规定进行公示", "公示情况"]
        )
        has_translation_info = (
            any(
                phrase in pre_invitation_text
                for phrase in ["担任翻译", "翻译情况", "同志担任翻译"]
            ) or ("翻译" in pre_invitation_text and "担任" in pre_invitation_text)
        )
        transport_refs = self._find_transport_references(page_texts)
        duration_values = self._extract_duration_values(page_texts)
        invite_units = self._extract_invite_units(page_texts)

        result.facts.update({
            "企业团组": "是" if is_enterprise else "否",
            "已识别人员名单": "是" if has_personnel_list else "否",
            "已识别公示情况": "是" if has_public_notice else "否",
            "已识别翻译情况": "是" if has_translation_info else "否",
            "已识别交通班次信息": "是" if transport_refs else "否",
            "已提取停留天数": self._format_duration_facts(duration_values),
            "已提取邀请单位": self._format_invite_unit_facts(invite_units),
        })

        if is_enterprise:
            result.notes.append(
                "已按企业团组处理；列入计划情况、周末公务情况、翻译情况、是否学术交流团缺失不直接作为问题。"
            )
        if has_personnel_list:
            result.notes.append("材料已出现“团组人员名单”或名单式内容，不得判为缺少人员名单。")
        if has_public_notice:
            result.notes.append("材料已明确写出公示情况，如“已按规定进行公示，公示无异议”。")
        if has_translation_info:
            result.notes.append("材料已明确写出翻译情况，如“某某同志担任翻译”。")
        if transport_refs:
            result.notes.append(
                "材料中已识别到交通班次信息，不应再误判为缺少航班号或车次。"
            )

        result.issues.extend(self._find_banned_word_issues(page_texts))
        result.issues.extend(self._find_duration_consistency_issues(duration_values))
        result.issues.extend(self._find_invite_unit_consistency_issues(invite_units))
        return result

    def filter_llm_issues(
        self,
        llm_issues: list[AuditIssue],
        deterministic: DeterministicResult
    ) -> list[AuditIssue]:
        filtered = []
        is_enterprise = deterministic.facts.get("企业团组") == "是"
        has_personnel_list = deterministic.facts.get("已识别人员名单") == "是"
        has_public_notice = deterministic.facts.get("已识别公示情况") == "是"
        has_translation_info = deterministic.facts.get("已识别翻译情况") == "是"
        has_transport_refs = deterministic.facts.get("已识别交通班次信息") == "是"

        for issue in llm_issues:
            text = f"{issue.category} {issue.description} {issue.location}"
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
        return "组团单位" in text and any(keyword in text for keyword in keywords)

    def _has_personnel_list(self, text: str) -> bool:
        if "团组人员名单" in text or "人员名单" in text:
            return True
        required_fields = ["姓名", "身份证号码", "工作单位及职务"]
        return all(field in text for field in required_fields)

    def _find_banned_word_issues(self, page_texts: list[tuple[int, str]]) -> list[AuditIssue]:
        banned_words = ["参观", "考察", "调研", "学习"]
        findings = []
        for page_no, text in page_texts:
            if self._is_invitation_page(text):
                continue

            for word in banned_words:
                if word not in text:
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

    def _extract_duration_values(self, page_texts: list[tuple[int, str]]) -> dict[str, tuple[int, int]]:
        values: dict[str, tuple[int, int]] = {}
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

    def _extract_invite_units(self, page_texts: list[tuple[int, str]]) -> dict[str, tuple[int, set[str]]]:
        units: dict[str, tuple[int, set[str]]] = {}
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

    @staticmethod
    def _invite_units_match(base_units: set[str], other_units: set[str]) -> bool:
        if not base_units or not other_units:
            return False

        def normalize(unit: str) -> str:
            return unit.replace("中国", "").replace("（", "").replace("）", "").strip()

        normalized_base = {normalize(unit) for unit in base_units}
        normalized_other = {normalize(unit) for unit in other_units}

        for base in normalized_base:
            if any(base in other or other in base for other in normalized_other):
                return True
        return False

    @staticmethod
    def _is_banned_word_issue(issue: AuditIssue) -> bool:
        text = f"{issue.category} {issue.description}"
        return any(keyword in text for keyword in ["参观", "考察", "调研", "学习"])

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
        match = re.search(r"邀请单位\s*(.*?)\s*\(中外文\)", text, re.S)
        if not match:
            return set()
        return DeterministicRuleEngine._extract_chinese_unit_names(match.group(1))

    @staticmethod
    def _extract_units_from_invitation(text: str) -> set[str]:
        preferred_patterns = [
            r"《?“?([\u4e00-\u9fff]{2,}(?:大学|学院|集团|公司|有限公司|股份有限公司|研究院))”?",
            r"应([\u4e00-\u9fff]{2,}(?:大学|学院|集团|公司|有限公司|股份有限公司|研究院))邀请",
            r"联邦国家预算高等教育机构“([\u4e00-\u9fff]{2,}(?:大学|学院))”",
        ]
        for pattern in preferred_patterns:
            matches = re.findall(pattern, text)
            cleaned = {
                match.replace("(", "（").replace(")", "）").strip()
                for match in matches
                if match
            }
            if cleaned:
                return cleaned

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        candidates = []
        for line in lines[:6]:
            if re.search(r"[\u4e00-\u9fff]{2,}", line):
                candidates.append(line)
        return DeterministicRuleEngine._extract_chinese_unit_names(" ".join(candidates[:2]))

    @staticmethod
    def _extract_chinese_unit_names(text: str) -> set[str]:
        normalized = text.replace("，", " ").replace(",", " ").replace("、", " ")
        matches = re.findall(r"[\u4e00-\u9fff（）()]{2,}(?:大学|学院|集团|公司|有限公司|股份有限公司|研究院)", normalized)
        cleaned = {
            match.replace("(", "（").replace(")", "）").strip()
            for match in matches
        }
        return {item for item in cleaned if item}

    @staticmethod
    def _is_english_only_invitation(text: str) -> bool:
        english_markers = ["Invitation Letter", "To Whom It May Concern"]
        chinese_markers = ["中国", "邀请函", "时间:", "在国外停留时间"]
        return any(marker in text for marker in english_markers) and not any(marker in text for marker in chinese_markers)

    @staticmethod
    def _is_real_chinese_invitation_page(text: str) -> bool:
        required_markers = ["邀请函"]
        context_markers = ["时间:", "在国外停留时间", "地点:", "参与形式", "致有关人士"]
        return all(marker in text for marker in required_markers) and any(marker in text for marker in context_markers)

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
