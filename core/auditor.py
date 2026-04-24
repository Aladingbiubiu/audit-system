import json
import re
from dataclasses import dataclass, asdict

from config.settings import ANTHROPIC_API_KEY, ZHIPU_API_KEY, USE_ZHIPU, RULES_FILE
from core.learning import format_guidelines_for_prompt, format_review_cases_for_prompt
import yaml


@dataclass
class AuditIssue:
    severity: str
    category: str
    description: str
    location: str = ""


@dataclass
class AuditResult:
    passed: bool
    issues: list[AuditIssue]
    summary: str
    raw_response: str = ""

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "issues": [asdict(issue) for issue in self.issues],
            "summary": self.summary
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "AuditResult":
        issues = [AuditIssue(**i) for i in data.get("issues", [])]
        return cls(
            passed=data.get("passed", False),
            issues=issues,
            summary=data.get("summary", "")
        )


def load_rules() -> list[str]:
    """加载审核规则"""
    if not RULES_FILE.exists():
        return []

    with open(RULES_FILE, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    rules = []
    for category in config.get("categories", []):
        category_name = category.get("name", "")
        if _is_disabled_text(category_name):
            continue

        for rule in category.get("rules", []):
            if _is_disabled_text(rule):
                continue
            rules.append(f"[{category_name}] {rule}")

    for check in config.get("priority_checks", []):
        if _is_disabled_text(check):
            continue
        rules.append(f"[重点检查] {check}")

    for issue in config.get("common_issues", []):
        if _is_disabled_text(issue):
            continue
        rules.append(f"[常见问题提示] {issue}")

    for rule in config.get("custom_rules", []):
        if _is_disabled_text(rule):
            continue
        rules.append(f"[自定义规则] {rule}")

    return rules


def _is_disabled_text(text: str) -> bool:
    normalized = (text or "").replace(" ", "")
    disabled_markers = ["暂时不用", "暂不审核", "暂时不审核"]
    return any(marker in normalized for marker in disabled_markers)


def format_rules_for_prompt(rules: list[str]) -> str:
    """格式化规则用于Prompt"""
    return "\n".join(f"{i+1}. {rule}" for i, rule in enumerate(rules))


def audit_document(
    content: str,
    rules: list[str] | None = None,
    guidelines: list[dict] | None = None,
    review_cases: list[dict] | None = None
) -> AuditResult:
    """使用AI API审核文档"""
    if rules is None:
        rules = load_rules()
    guidelines = guidelines or []
    review_cases = review_cases or []

    if not rules:
        raise ValueError("未配置审核规则")

    if USE_ZHIPU:
        return _audit_with_zhipu(content, rules, guidelines, review_cases)
    elif ANTHROPIC_API_KEY:
        return _audit_with_claude(content, rules, guidelines, review_cases)
    else:
        raise ValueError("未设置 API 密钥，请设置 ZHIPU_API_KEY 或 ANTHROPIC_API_KEY")


def _build_prompts(
    content: str,
    rules: list[str],
    guidelines: list[dict],
    review_cases: list[dict]
) -> tuple[str, str]:
    system_prompt = """你是一个专业的行政审批材料审核专家。你需要根据给定的审核规则、补充审核口径和历史判例，对材料进行合规性审查。

你的职责是：
1. 仔细阅读材料内容，优先基于材料证据判断
2. 逐条对照审核规则进行检查
3. 遇到需要语境理解的规则时，参考补充审核口径和历史判例
4. 指出发现的问题，包括问题的严重程度、类别、具体描述和位置
5. 对于因补充口径或判例而不构成问题的事项，不要列入 issues，可在 summary 中简要说明
6. 对于格式类问题，只有在材料中的实际文本与规范写法存在明确可见差异时才能指出；如果应为内容与实际内容一致，或只是怀疑存在空格、字符间距、隐藏字符等问题但无法明确举证，不得判为问题
7. 如果你使用“应为XXX，实际为YYY”这类表述，必须保证 XXX 与 YYY 明显不同；若两者一致，不得输出该问题
8. 命中禁用词（如“调研”“参观”“考察”，以及不允许场景下的“学习”）时，默认按严重问题处理，除非规则已明确允许

问题严重程度分为：严重（必须修改）、一般（建议修改）、提示（可选修改）"""

    user_prompt = f"""请根据以下信息审核材料：

## 审核规则
{format_rules_for_prompt(rules)}

## 补充审核口径
{format_guidelines_for_prompt(guidelines)}

## 可参考的历史判例
{format_review_cases_for_prompt(review_cases)}

## 材料内容
{content}

## 输出要求
请以严格的JSON格式输出审核结果，不要添加任何其他文字。格式如下：
{{
    "passed": true或false,
    "issues": [
        {{
            "severity": "严重/一般/提示",
            "category": "问题类别",
            "description": "具体问题描述。若参考了某条规则、口径或判例，请在描述中简要说明依据。",
            "location": "问题所在位置（如第几页、哪个字段）"
        }}
    ],
    "summary": "总体评价和修改建议。请说明是否应用了补充口径或历史判例。"
}}"""

    return system_prompt, user_prompt


def _audit_with_zhipu(
    content: str,
    rules: list[str],
    guidelines: list[dict],
    review_cases: list[dict]
) -> AuditResult:
    """使用智谱 GLM API 审核"""
    from zhipuai import ZhipuAI

    client = ZhipuAI(api_key=ZHIPU_API_KEY)

    system_prompt, user_prompt = _build_prompts(
        content,
        rules,
        guidelines,
        review_cases
    )

    response = client.chat.completions.create(
        model="glm-4-plus",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    )

    response_text = response.choices[0].message.content
    return _parse_response(response_text)


def _audit_with_claude(
    content: str,
    rules: list[str],
    guidelines: list[dict],
    review_cases: list[dict]
) -> AuditResult:
    """使用 Claude API 审核"""
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    system_prompt, user_prompt = _build_prompts(
        content,
        rules,
        guidelines,
        review_cases
    )

    message = client.messages.create(
        model="claude-sonnet-4-6-20250514",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    response_text = message.content[0].text
    return _parse_response(response_text)


def _parse_response(response_text: str) -> AuditResult:
    """解析AI响应"""
    try:
        cleaned_text = _strip_code_fences(response_text)
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            json_str = cleaned_text[cleaned_text.find("{"):cleaned_text.rfind("}") + 1]
            result_data = _load_json_with_repair(json_str)
        else:
            result_data = _load_json_with_repair(cleaned_text)

        issues = [
            AuditIssue(
                severity=i.get("severity", "一般"),
                category=i.get("category", "其他"),
                description=i.get("description", ""),
                location=i.get("location", "")
            )
            for i in result_data.get("issues", [])
        ]
        issues = _filter_inconsistent_issues(issues)
        issues = _normalize_issue_severity(issues)

        return AuditResult(
            passed=result_data.get("passed", False),
            issues=issues,
            summary=_normalize_summary(result_data.get("summary", ""), issues),
            raw_response=cleaned_text
        )

    except json.JSONDecodeError:
        return AuditResult(
            passed=False,
            issues=[AuditIssue("严重", "解析错误", "无法解析审核结果", "")],
            summary=f"审核结果解析失败，请重试。原始返回片段：{_summarize_raw_response(response_text)}",
            raw_response=response_text
        )


def _filter_inconsistent_issues(issues: list[AuditIssue]) -> list[AuditIssue]:
    """过滤明显自相矛盾的格式类问题。"""
    filtered = []
    for issue in issues:
        if _is_self_contradictory_format_issue(issue.description):
            continue
        filtered.append(issue)
    return filtered


def _is_self_contradictory_format_issue(description: str) -> bool:
    text = (description or "").strip()
    if "应为" not in text or "实际" not in text:
        return False

    expected = _extract_between_markers(text, "应为", "实际")
    actual = _extract_after_marker(text, "实际")
    if not expected or not actual:
        return False

    return _normalize_comparison_text(expected) == _normalize_comparison_text(actual)


def _extract_between_markers(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0 or end <= start:
        return ""
    return _cleanup_comparison_segment(
        text[start + len(start_marker):end]
    )


def _extract_after_marker(text: str, marker: str) -> str:
    start = text.find(marker)
    if start < 0:
        return ""
    return _cleanup_comparison_segment(text[start + len(marker):])


def _cleanup_comparison_segment(text: str) -> str:
    segment = (text or "").strip(" 为：:，,。；;“”\"'")
    segment = re.split(
        r"[，,。；;]\s*(缺少|多了|少了|存在|出现|使用了|未使用|格式|其中|但|说明)",
        segment,
        maxsplit=1
    )[0]
    return segment.strip(" ：:，,。；;“”\"'")


def _normalize_comparison_text(text: str) -> str:
    normalized = text
    for token in [" ", "\u3000", "\n", "\r", "\t", "“", "”", "\"", "'", "，", ",", "。", "；", ";", "：", ":"]:
        normalized = normalized.replace(token, "")
    return normalized


def _normalize_summary(summary: str, issues: list[AuditIssue]) -> str:
    text = (summary or "").strip()
    if not text:
        return text

    if any(_contains_banned_word_issue(issue) for issue in issues):
        contradictory_patterns = [
            "未出现禁止使用的词汇",
            "未发现禁止使用的词汇",
            "没有出现禁止使用的词汇",
            "未发现“学习”“调研”“参观”“考察”等词汇",
            "未出现“学习”“调研”“参观”“考察”等词汇"
        ]
        for pattern in contradictory_patterns:
            text = text.replace(pattern, "")
        text = _clean_summary_text(text)

    if not any("学习" in f"{issue.category} {issue.description} {issue.location}" for issue in issues):
        learning_patterns = [
            r"[^。！？]*学习[^。！？]*[。！？]?",
            r"[^。！？]*访学[^。！？]*[。！？]?",
        ]
        for pattern in learning_patterns:
            text = re.sub(pattern, "", text)
        text = _clean_summary_text(text)

    if not issues and text:
        return f"{text} 本次结果已自动过滤明显自相矛盾的问题描述。"
    return text


def _clean_summary_text(text: str) -> str:
    cleaned = re.sub(r"[，,。；;、]\s*[，,。；;、]+", "。", text)
    cleaned = cleaned.replace("材料中，但", "材料中")
    cleaned = cleaned.replace("材料中, 但", "材料中")
    cleaned = cleaned.replace("，但", "，")
    cleaned = cleaned.replace(", but", ",")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip("，,；;、 ")
    if cleaned and cleaned[-1] not in "。！？":
        cleaned += "。"
    return cleaned


def _strip_code_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _summarize_raw_response(text: str, limit: int = 300) -> str:
    cleaned = _strip_code_fences(text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "..."


def _load_json_with_repair(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = _repair_json_string_quotes(text)
        return json.loads(repaired)


def _repair_json_string_quotes(text: str) -> str:
    repaired: list[str] = []
    in_string = False
    escape = False
    i = 0
    length = len(text)

    while i < length:
        ch = text[i]

        if escape:
            repaired.append(ch)
            escape = False
            i += 1
            continue

        if ch == "\\":
            repaired.append(ch)
            escape = True
            i += 1
            continue

        if ch == '"':
            if not in_string:
                in_string = True
                repaired.append(ch)
                i += 1
                continue

            j = i + 1
            while j < length and text[j].isspace():
                j += 1
            next_char = text[j] if j < length else ""

            if next_char in [",", "}", "]", ":"]:
                in_string = False
                repaired.append(ch)
            else:
                repaired.append('\\"')
            i += 1
            continue

        repaired.append(ch)
        i += 1

    return "".join(repaired)


def _normalize_issue_severity(issues: list[AuditIssue]) -> list[AuditIssue]:
    for issue in issues:
        if _contains_banned_word_issue(issue):
            issue.severity = "严重"
    return issues


def _contains_banned_word_issue(issue: AuditIssue) -> bool:
    text = f"{issue.category} {issue.description} {issue.location}"
    banned_keywords = ["调研", "参观", "考察"]
    learning_keyword = "学习"

    if any(keyword in text for keyword in banned_keywords):
        return True

    if learning_keyword in text and not any(marker in text for marker in ["培训", "访学", "学术交流"]):
        return True

    return False
