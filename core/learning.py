from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from config.settings import GUIDELINES_FILE


def load_guidelines() -> list[dict[str, Any]]:
    """加载需要结合语境判断的审核口径。"""
    if not GUIDELINES_FILE.exists():
        return []

    with open(GUIDELINES_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return data.get("guidelines", []) or []


def save_guideline(
    title: str,
    applies_when: str,
    guidance: str,
    severity_override: str,
    source: str = "feedback"
) -> dict[str, Any]:
    """追加一条长期审核口径。"""
    GUIDELINES_FILE.parent.mkdir(exist_ok=True)
    guidelines = load_guidelines()
    guideline = {
        "title": title.strip(),
        "applies_when": applies_when.strip(),
        "guidance": guidance.strip(),
        "severity_override": severity_override.strip(),
        "source": source,
        "created_at": datetime.now().isoformat(timespec="seconds")
    }
    guidelines.append(guideline)

    with open(GUIDELINES_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            {"guidelines": guidelines},
            f,
            allow_unicode=True,
            sort_keys=False
        )

    return guideline


def format_guidelines_for_prompt(guidelines: list[dict[str, Any]]) -> str:
    if not guidelines:
        return "暂无补充审核口径。"

    lines = []
    for i, item in enumerate(guidelines, 1):
        title = item.get("title", "未命名口径")
        applies_when = item.get("applies_when", "")
        guidance = item.get("guidance", "")
        severity_override = item.get("severity_override", "")
        lines.append(
            f"{i}. {title}\n"
            f"   适用场景：{applies_when}\n"
            f"   判断口径：{guidance}\n"
            f"   处理方式：{severity_override}"
        )
    return "\n".join(lines)


def format_review_cases_for_prompt(cases: list[dict[str, Any]]) -> str:
    if not cases:
        return "暂无可参考的历史判例。"

    lines = []
    for i, case in enumerate(cases, 1):
        lines.append(
            f"{i}. 案例：{case.get('case_summary', '')}\n"
            f"   AI原判断：{case.get('ai_issue', '')}\n"
            f"   人工决定：{case.get('human_decision', '')}\n"
            f"   理由：{case.get('human_reason', '')}"
        )
    return "\n".join(lines)

