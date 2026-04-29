from __future__ import annotations

from dataclasses import dataclass, field

from .repository import HistoryRepository, record_to_dict


@dataclass
class RecommendationItem:
    record: dict
    score: int
    reasons: list[str] = field(default_factory=list)


class TripRecommender:
    def __init__(self, repository: HistoryRepository | None = None):
        self.repository = repository or HistoryRepository()

    def recommend(self, country: str, industry: str | None = None, limit: int = 20) -> dict:
        country = (country or "").strip()
        industry = (industry or "").strip() or None
        if not country:
            return {"history": [], "organizations": [], "similar": [], "region_stats": []}

        history_records = self.repository.list_records(country=country, limit=limit)
        organizations = self.repository.list_organizations(country=country, limit=20)
        similar = self._similar_records(country, industry, limit=limit)
        region_stats = self._region_stats(history_records)

        return {
            "history": [record_to_dict(record) for record in history_records],
            "organizations": [
                {
                    "id": org.id,
                    "name_cn": org.name_cn,
                    "name_en": org.name_en,
                    "country": org.country,
                    "region": org.region,
                    "industry": org.industry,
                    "org_type": org.org_type,
                    "visit_count": org.visit_count,
                }
                for org in organizations
            ],
            "similar": [
                {
                    "record": item.record,
                    "score": item.score,
                    "reasons": item.reasons,
                }
                for item in similar
            ],
            "region_stats": region_stats,
        }

    def _similar_records(self, country: str, industry: str | None, limit: int) -> list[RecommendationItem]:
        all_records = self.repository.list_records(limit=300)
        target_region = None
        for record in all_records:
            if record.country == country and record.region:
                target_region = record.region
                break

        scored: list[RecommendationItem] = []
        for record in all_records:
            reasons: list[str] = []
            score = 0
            if record.country == country:
                score += 6
                reasons.append(f"同为{country}出访")
            if industry and record.industry == industry:
                score += 4
                reasons.append(f"业务领域同为{industry}")
            elif industry and record.industry and industry in record.industry:
                score += 2
                reasons.append(f"业务领域包含{industry}")
            if target_region and record.region == target_region and record.country != country:
                score += 2
                reasons.append(f"同属{target_region}地区")
            if record.organization_name_cn and record.country == country:
                score += 1
                reasons.append("可参考该国既有国外单位")
            if record.group_unit:
                score += 1
            if score > 0:
                scored.append(RecommendationItem(record=record_to_dict(record), score=score, reasons=reasons))

        scored.sort(key=lambda item: (item.score, item.record.get("created_at") or ""), reverse=True)
        return scored[:limit]

    def _region_stats(self, history_records) -> list[dict]:
        regions: dict[str, int] = {}
        for record in history_records:
            if not record.region:
                continue
            regions[record.region] = regions.get(record.region, 0) + 1
        return [
            {"region": region, "visit_count": count}
            for region, count in sorted(regions.items(), key=lambda item: item[1], reverse=True)
        ]

