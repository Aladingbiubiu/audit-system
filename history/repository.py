from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import or_

from .models import (
    Organization,
    SessionLocal,
    TripContact,
    TripImportJob,
    TripRecord,
    TripSourceFile,
)


class HistoryRepository:
    def __init__(self):
        self.session = SessionLocal()

    def create_import_job(self, root_path: str, total_files: int) -> TripImportJob:
        job = TripImportJob(
            root_path=root_path,
            status="running",
            total_files=total_files,
            started_at=datetime.now(),
        )
        self.session.add(job)
        self.session.commit()
        return job

    def finish_import_job(self, job_id: int, status: str = "completed", message: str | None = None) -> None:
        job = self.session.query(TripImportJob).filter_by(id=job_id).first()
        if not job:
            return
        job.status = status
        job.message = message
        job.completed_at = datetime.now()
        self.session.commit()

    def increment_job(self, job_id: int, *, success: bool = False, failed: bool = False, review: bool = False) -> None:
        job = self.session.query(TripImportJob).filter_by(id=job_id).first()
        if not job:
            return
        job.processed_count = (job.processed_count or 0) + 1
        if success:
            job.success_count = (job.success_count or 0) + 1
        if failed:
            job.failed_count = (job.failed_count or 0) + 1
        if review:
            job.review_count = (job.review_count or 0) + 1
        self.session.commit()

    def list_import_jobs(self, limit: int = 20) -> list[TripImportJob]:
        return (
            self.session.query(TripImportJob)
            .order_by(TripImportJob.started_at.desc())
            .limit(limit)
            .all()
        )

    def create_source_file(
        self,
        *,
        import_job_id: int | None,
        filename: str,
        file_path: str,
        file_hash: str,
        file_size_kb: float,
    ) -> TripSourceFile:
        source = TripSourceFile(
            import_job_id=import_job_id,
            filename=filename,
            file_path=file_path,
            file_hash=file_hash,
            file_size_kb=file_size_kb,
            status="processing",
        )
        self.session.add(source)
        self.session.commit()
        return source

    def mark_source_file(
        self,
        source_file_id: int,
        *,
        status: str,
        trip_record_id: int | None = None,
        error_message: str | None = None,
    ) -> None:
        source = self.session.query(TripSourceFile).filter_by(id=source_file_id).first()
        if not source:
            return
        source.status = status
        source.trip_record_id = trip_record_id
        source.error_message = error_message
        source.processed_at = datetime.now()
        self.session.commit()

    def find_source_by_hash(self, file_hash: str) -> TripSourceFile | None:
        return self.session.query(TripSourceFile).filter_by(file_hash=file_hash, status="completed").first()

    def find_organization(self, normalized_name: str, country: str | None = None) -> Organization | None:
        query = self.session.query(Organization).filter_by(normalized_name=normalized_name)
        if country:
            match = query.filter_by(country=country).first()
            if match:
                return match
        return query.first()

    def upsert_organization(self, data: dict[str, Any]) -> Organization | None:
        name_cn = (data.get("name_cn") or "").strip()
        normalized_name = (data.get("normalized_name") or name_cn).strip()
        if not name_cn:
            return None

        organization = self.find_organization(normalized_name, data.get("country"))
        if organization is None:
            organization = Organization(
                name_cn=name_cn,
                normalized_name=normalized_name,
                name_en=data.get("name_en"),
                country=data.get("country"),
                region=data.get("region"),
                industry=data.get("industry"),
                org_type=data.get("org_type"),
                notes=data.get("notes"),
                visit_count=0,
            )
            self.session.add(organization)
        else:
            for field in ["name_en", "country", "region", "industry", "org_type", "notes"]:
                value = data.get(field)
                if value and not getattr(organization, field):
                    setattr(organization, field, value)
        organization.visit_count = (organization.visit_count or 0) + 1
        self.session.commit()
        return organization

    def create_trip_record(
        self,
        data: dict[str, Any],
        *,
        import_job_id: int | None,
        source_file_id: int | None,
        organization_id: int | None,
        duplicate_status: str,
        duplicate_reason: str | None,
        needs_review: bool,
    ) -> TripRecord:
        record = TripRecord(
            import_job_id=import_job_id,
            source_file_id=source_file_id,
            organization_id=organization_id,
            country=data.get("country"),
            region=data.get("region"),
            city=data.get("city"),
            organization_name_cn=data.get("organization_name_cn"),
            organization_name_en=data.get("organization_name_en"),
            org_type=data.get("org_type"),
            industry=data.get("industry"),
            visit_purpose=data.get("visit_purpose"),
            visit_summary=data.get("visit_summary"),
            duration_days=data.get("duration_days"),
            group_unit=data.get("group_unit"),
            group_type=data.get("group_type"),
            group_size=data.get("group_size"),
            expense_source=data.get("expense_source"),
            visit_date=data.get("visit_date"),
            source_filename=data.get("source_filename"),
            source_path=data.get("source_path"),
            source_hash=data.get("source_hash"),
            status="needs_review" if needs_review else "completed",
            needs_review=needs_review,
            duplicate_status=duplicate_status,
            duplicate_reason=duplicate_reason,
            extraction_json=json.dumps(data, ensure_ascii=False),
        )
        self.session.add(record)
        self.session.commit()
        return record

    def add_contacts(self, trip_record_id: int, organization_id: int | None, contacts: list[dict[str, Any]]) -> None:
        for contact in contacts:
            name = (contact.get("name") or "").strip()
            if not name:
                continue
            self.session.add(
                TripContact(
                    trip_record_id=trip_record_id,
                    organization_id=organization_id,
                    name=name,
                    title=contact.get("title"),
                    email=contact.get("email"),
                    phone=contact.get("phone"),
                )
            )
        self.session.commit()

    def find_possible_duplicates(self, data: dict[str, Any], limit: int = 5) -> list[TripRecord]:
        query = self.session.query(TripRecord)
        country = data.get("country")
        group_unit = data.get("group_unit")
        org_name = data.get("organization_name_cn")
        visit_date = data.get("visit_date")
        duration_days = data.get("duration_days")

        if country:
            query = query.filter(TripRecord.country == country)

        candidates = query.order_by(TripRecord.created_at.desc()).limit(200).all()
        scored: list[tuple[int, TripRecord]] = []
        for record in candidates:
            score = 0
            if country and record.country == country:
                score += 2
            if group_unit and record.group_unit == group_unit:
                score += 2
            if org_name and record.organization_name_cn == org_name:
                score += 3
            if visit_date and record.visit_date == visit_date:
                score += 3
            if duration_days is not None and record.duration_days == duration_days:
                score += 1
            if score >= 5:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in scored[:limit]]

    def list_records(
        self,
        *,
        country: str | None = None,
        industry: str | None = None,
        organization: str | None = None,
        group_unit: str | None = None,
        duplicate_status: str | None = None,
        search: str | None = None,
        limit: int = 100,
    ) -> list[TripRecord]:
        query = self.session.query(TripRecord)
        if country:
            query = query.filter(TripRecord.country.like(f"%{country}%"))
        if industry:
            query = query.filter(TripRecord.industry.like(f"%{industry}%"))
        if organization:
            query = query.filter(TripRecord.organization_name_cn.like(f"%{organization}%"))
        if group_unit:
            query = query.filter(TripRecord.group_unit.like(f"%{group_unit}%"))
        if duplicate_status:
            query = query.filter(TripRecord.duplicate_status == duplicate_status)
        if search:
            pattern = f"%{search}%"
            query = query.filter(
                or_(
                    TripRecord.visit_purpose.like(pattern),
                    TripRecord.visit_summary.like(pattern),
                    TripRecord.source_filename.like(pattern),
                    TripRecord.organization_name_cn.like(pattern),
                )
            )
        return query.order_by(TripRecord.created_at.desc()).limit(limit).all()

    def get_record(self, record_id: int) -> TripRecord | None:
        return self.session.query(TripRecord).filter_by(id=record_id).first()

    def update_record(self, record_id: int, updates: dict[str, Any]) -> bool:
        record = self.get_record(record_id)
        if not record:
            return False
        editable_fields = {
            "country",
            "region",
            "city",
            "organization_name_cn",
            "organization_name_en",
            "org_type",
            "industry",
            "visit_purpose",
            "visit_summary",
            "duration_days",
            "group_unit",
            "group_type",
            "group_size",
            "expense_source",
            "visit_date",
            "duplicate_status",
            "duplicate_reason",
            "needs_review",
            "status",
        }
        for key, value in updates.items():
            if key in editable_fields:
                setattr(record, key, value)
        record.updated_at = datetime.now()
        self.session.commit()
        return True

    def list_organizations(self, country: str | None = None, limit: int = 100) -> list[Organization]:
        query = self.session.query(Organization)
        if country:
            query = query.filter(Organization.country.like(f"%{country}%"))
        return query.order_by(Organization.visit_count.desc(), Organization.updated_at.desc()).limit(limit).all()

    def list_contacts(self, trip_record_id: int) -> list[TripContact]:
        return self.session.query(TripContact).filter_by(trip_record_id=trip_record_id).all()

    def close(self) -> None:
        self.session.close()


def record_to_dict(record: TripRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "country": record.country,
        "region": record.region,
        "city": record.city,
        "organization_name_cn": record.organization_name_cn,
        "organization_name_en": record.organization_name_en,
        "org_type": record.org_type,
        "industry": record.industry,
        "visit_purpose": record.visit_purpose,
        "visit_summary": record.visit_summary,
        "duration_days": record.duration_days,
        "group_unit": record.group_unit,
        "group_type": record.group_type,
        "group_size": record.group_size,
        "expense_source": record.expense_source,
        "visit_date": record.visit_date,
        "source_filename": record.source_filename,
        "source_path": record.source_path,
        "status": record.status,
        "needs_review": record.needs_review,
        "duplicate_status": record.duplicate_status,
        "duplicate_reason": record.duplicate_reason,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }

