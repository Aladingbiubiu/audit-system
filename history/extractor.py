from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.settings import ANTHROPIC_API_KEY, USE_ZHIPU, ZHIPU_API_KEY
from core.rule_model import DocumentFacts

from .audit_adapter import AuditExtractionAdapter, ParsedPdf


COUNTRY_REGIONS = {
    "德国": "欧洲",
    "法国": "欧洲",
    "英国": "欧洲",
    "意大利": "欧洲",
    "西班牙": "欧洲",
    "荷兰": "欧洲",
    "比利时": "欧洲",
    "瑞士": "欧洲",
    "瑞典": "欧洲",
    "丹麦": "欧洲",
    "芬兰": "欧洲",
    "挪威": "欧洲",
    "奥地利": "欧洲",
    "俄罗斯": "欧洲",
    "美国": "北美",
    "加拿大": "北美",
    "墨西哥": "北美",
    "日本": "东亚",
    "韩国": "东亚",
    "新加坡": "东南亚",
    "马来西亚": "东南亚",
    "泰国": "东南亚",
    "越南": "东南亚",
    "印度尼西亚": "东南亚",
    "菲律宾": "东南亚",
    "印度": "南亚",
    "巴基斯坦": "南亚",
    "阿联酋": "中东",
    "沙特": "中东",
    "以色列": "中东",
    "土耳其": "中东",
    "澳大利亚": "大洋洲",
    "新西兰": "大洋洲",
    "南非": "非洲",
    "埃及": "非洲",
    "巴西": "南美",
    "阿根廷": "南美",
    "智利": "南美",
    "哈萨克斯坦": "中亚",
    "乌兹别克斯坦": "中亚",
}

INDUSTRY_KEYWORDS = {
    "教育科研": ["大学", "学院", "研究", "实验室", "科研", "学术", "访学", "校际", "教育"],
    "智能制造": ["制造", "装备", "工业", "自动化", "机器人", "工厂", "生产线", "机械"],
    "新能源": ["新能源", "光伏", "风电", "储能", "电池", "氢能", "太阳能", "能源"],
    "信息技术": ["信息", "软件", "数据", "数字", "人工智能", "网络", "通信", "芯片", "半导体"],
    "生物医药": ["生物", "医药", "医疗", "医院", "制药", "临床", "健康"],
    "金融服务": ["金融", "银行", "证券", "保险", "基金", "投资"],
    "农业食品": ["农业", "食品", "农产品", "种业", "畜牧", "渔业"],
    "文化旅游": ["文化", "旅游", "艺术", "博物馆", "体育"],
    "政府公共事务": ["政府", "市政", "公共", "协会", "商会", "议会"],
}


@dataclass
class TripExtraction:
    data: dict[str, Any]
    contacts: list[dict[str, str | None]] = field(default_factory=list)
    parsed_pdf: ParsedPdf | None = None


class TripExtractor:
    def __init__(self, adapter: AuditExtractionAdapter | None = None):
        self.adapter = adapter or AuditExtractionAdapter()

    def extract_pdf(self, pdf_path: str | Path, *, enable_ocr: bool = True, enable_llm: bool = False) -> TripExtraction:
        parsed = self.adapter.parse_pdf(pdf_path, enable_ocr=enable_ocr)
        data = self._extract_from_facts(parsed.facts, parsed.path, parsed.extraction.text)
        contacts = self._extract_contacts(parsed.extraction.text)

        if enable_llm:
            enrichment = self._llm_enrich(data, parsed.extraction.text)
            if enrichment:
                for key in ["industry", "region", "city", "visit_date", "org_type", "visit_summary"]:
                    if enrichment.get(key) and not data.get(key):
                        data[key] = enrichment[key]
                if enrichment.get("contacts"):
                    contacts.extend(enrichment["contacts"])

        return TripExtraction(data=data, contacts=contacts, parsed_pdf=parsed)

    def _extract_from_facts(self, facts: DocumentFacts, path: Path, full_text: str) -> dict[str, Any]:
        country = pick_country(facts.budget.visit_countries) or pick_country(facts.presentment.visit_destination)
        organization_name = pick_invite_unit(facts)
        visit_purpose = clean_text(facts.presentment.visit_reason)
        combined_context = " ".join(
            part
            for part in [
                organization_name,
                facts.presentment.invite_unit_foreign,
                visit_purpose,
                facts.group_unit_name,
                full_text[:3000],
            ]
            if part
        )
        industry = infer_industry(combined_context)
        region = COUNTRY_REGIONS.get(country or "", None)
        org_type = infer_org_type(organization_name or "", combined_context)
        visit_date = extract_visit_date(full_text)
        city = extract_city(facts.presentment.visit_destination, country)
        duration = facts.presentment.duration_days or facts.budget.duration_days or max_duration(facts)
        summary = summarize_visit(visit_purpose, full_text)

        return {
            "country": country,
            "region": region,
            "city": city,
            "organization_name_cn": organization_name,
            "organization_name_en": clean_text(facts.presentment.invite_unit_foreign),
            "normalized_organization_name": normalize_org_name(organization_name or ""),
            "org_type": org_type,
            "industry": industry,
            "visit_purpose": visit_purpose,
            "visit_summary": summary,
            "duration_days": duration,
            "group_unit": clean_text(facts.group_unit_name or facts.presentment.group_unit or facts.budget.group_unit),
            "group_type": facts.profile.group_type,
            "group_size": clean_text(facts.budget.group_size),
            "expense_source": clean_text(facts.expense_source or facts.presentment.expense_source),
            "visit_date": visit_date,
            "source_filename": path.name,
            "source_path": str(path.absolute()),
            "extraction_warnings": parsed_warning_flags(facts, full_text),
        }

    def _extract_contacts(self, text: str) -> list[dict[str, str | None]]:
        contacts: list[dict[str, str | None]] = []
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            email = find_first(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", line)
            phone = find_first(r"(?:\+?\d[\d\s\-()]{6,}\d)", line)
            if not email and not phone and not any(marker in line for marker in ["联系人", "Contact", "contact", "电话", "邮箱"]):
                continue

            scope = " ".join(lines[max(0, index - 1): index + 2])
            name = find_first(r"(?:联系人|Contact Person|Contact|邀请人)[:：\s]*([\u4e00-\u9fa5A-Za-z .·-]{2,40})", scope)
            title = find_first(r"(?:职务|职位|Title|Position)[:：\s]*([\u4e00-\u9fa5A-Za-z .·-]{2,60})", scope)
            if not name and email:
                name = email.split("@", 1)[0]
            if name:
                contacts.append(
                    {
                        "name": clean_text(name),
                        "title": clean_text(title),
                        "email": email,
                        "phone": clean_text(phone),
                    }
                )
        return dedupe_contacts(contacts)

    def _llm_enrich(self, data: dict[str, Any], text: str) -> dict[str, Any]:
        if USE_ZHIPU and ZHIPU_API_KEY:
            return self._llm_enrich_zhipu(data, text)
        if ANTHROPIC_API_KEY:
            return self._llm_enrich_claude(data, text)
        return {}

    def _build_enrichment_prompt(self, data: dict[str, Any], text: str) -> str:
        return f"""请基于出访材料补充结构化字段。只返回 JSON，不要输出解释。

已知信息：
- 国家：{data.get("country")}
- 国外单位：{data.get("organization_name_cn")}
- 出访事由：{data.get("visit_purpose")}
- 组团单位：{data.get("group_unit")}

需要返回：
{{
  "industry": "行业/业务领域",
  "region": "欧洲/东南亚/北美/东亚/中东/非洲/南美/大洋洲/中亚/南亚",
  "city": "城市",
  "visit_date": "出访日期或日期区间",
  "org_type": "企业/高校/研究所/政府机构/国际组织/其他",
  "visit_summary": "50字以内出访摘要",
  "contacts": [
    {{"name": "姓名", "title": "职务", "email": "邮箱", "phone": "电话"}}
  ]
}}

无法判断的字段使用 null。

材料文本：
{text[:12000]}"""

    def _llm_enrich_zhipu(self, data: dict[str, Any], text: str) -> dict[str, Any]:
        from zhipuai import ZhipuAI

        client = ZhipuAI(api_key=ZHIPU_API_KEY)
        response = client.chat.completions.create(
            model="glm-4-plus",
            messages=[
                {"role": "system", "content": "你是出访材料信息抽取助手。"},
                {"role": "user", "content": self._build_enrichment_prompt(data, text)},
            ],
        )
        return load_json_object(response.choices[0].message.content)

    def _llm_enrich_claude(self, data: dict[str, Any], text: str) -> dict[str, Any]:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-6-20250514",
            max_tokens=2048,
            system="你是出访材料信息抽取助手。",
            messages=[{"role": "user", "content": self._build_enrichment_prompt(data, text)}],
        )
        return load_json_object(message.content[0].text)


def pick_country(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    for country in sorted(COUNTRY_REGIONS, key=len, reverse=True):
        if country in text:
            return country
    cleaned = re.sub(r"(出访国别|出访地|国家|地区|经停|含|访问|赴|前往|：|:)", " ", text)
    parts = [part.strip() for part in re.split(r"[、，,;/；\s]+|和|及", cleaned) if part.strip()]
    return parts[0] if parts else None


def pick_invite_unit(facts: DocumentFacts) -> str | None:
    if facts.presentment.invite_unit_cn:
        return clean_text(facts.presentment.invite_unit_cn)
    for _, unit_set in facts.invite_units.values():
        if unit_set:
            return clean_text(sorted(unit_set, key=len, reverse=True)[0])
    return None


def max_duration(facts: DocumentFacts) -> int | None:
    values = [value[1] for value in facts.duration_values.values() if value and value[1] is not None]
    return max(values) if values else None


def infer_industry(text: str) -> str | None:
    scores: list[tuple[int, str]] = []
    for industry, keywords in INDUSTRY_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in text)
        if score:
            scores.append((score, industry))
    if not scores:
        return None
    scores.sort(reverse=True)
    return scores[0][1]


def infer_org_type(name: str, context: str) -> str | None:
    text = f"{name} {context}"
    if any(token in text for token in ["大学", "学院", "学校"]):
        return "高校"
    if any(token in text for token in ["研究所", "研究院", "研究中心", "实验室", "中心"]):
        return "研究所"
    if any(token in text for token in ["公司", "集团", "有限公司", "股份"]):
        return "企业"
    if any(token in text for token in ["政府", "部", "厅", "局", "市政", "议会"]):
        return "政府机构"
    if any(token in text for token in ["联合国", "世界银行", "国际组织", "协会", "商会"]):
        return "国际组织"
    return None


def extract_visit_date(text: str) -> str | None:
    patterns = [
        r"(20\d{2})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})日?\s*(?:至|到|-|—|~)\s*(20\d{2})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})日?",
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日",
        r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})",
    ]
    range_match = re.search(patterns[0], text)
    if range_match:
        y1, m1, d1, y2, m2, d2 = range_match.groups()
        return f"{int(y1):04d}-{int(m1):02d}-{int(d1):02d} 至 {int(y2):04d}-{int(m2):02d}-{int(d2):02d}"
    for pattern in patterns[1:]:
        match = re.search(pattern, text)
        if match:
            y, m, d = match.groups()
            return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return None


def extract_city(destination: str | None, country: str | None) -> str | None:
    text = clean_text(destination)
    if not text or not country:
        return None
    tail = text.replace(country, "")
    parts = [part.strip() for part in re.split(r"[、，,;/；\s]+", tail) if part.strip()]
    if not parts:
        return None
    first = parts[0]
    if first in ["经停地", "停留时间", "邀请单位"]:
        return None
    return first[:100]


def summarize_visit(visit_purpose: str | None, full_text: str) -> str | None:
    text = clean_text(visit_purpose)
    if not text:
        text = clean_text(full_text[:500])
    if not text:
        return None
    return text[:180]


def normalize_org_name(name: str) -> str:
    normalized = clean_text(name) or ""
    normalized = re.sub(r"^(邀请单位|主办单位|承办单位)[:：]?", "", normalized)
    normalized = re.sub(r"[\s（）()“”\"'《》]+", "", normalized)
    return normalized.lower()


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip(" :：;；,，。")
    return text or None


def parsed_warning_flags(facts: DocumentFacts, full_text: str) -> list[str]:
    warnings = []
    if not facts.budget.visit_countries and not facts.presentment.visit_destination:
        warnings.append("未稳定识别出访国家")
    if not facts.presentment.invite_unit_cn and not facts.invite_units:
        warnings.append("未稳定识别国外单位")
    if not facts.presentment.visit_reason:
        warnings.append("未稳定识别出访事由")
    if len(full_text.strip()) < 100:
        warnings.append("可读文本较少，可能需要人工复核 OCR")
    return warnings


def find_first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    if not match:
        return None
    return match.group(1) if match.groups() else match.group(0)


def dedupe_contacts(contacts: list[dict[str, str | None]]) -> list[dict[str, str | None]]:
    seen = set()
    result = []
    for contact in contacts:
        key = (contact.get("name"), contact.get("email"), contact.get("phone"))
        if key in seen:
            continue
        seen.add(key)
        result.append(contact)
    return result


def load_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start >= 0 and end > start:
        cleaned = cleaned[start:end]
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}

