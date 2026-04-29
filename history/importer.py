from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .extractor import TripExtractor, normalize_org_name
from .repository import HistoryRepository, record_to_dict


@dataclass
class ImportFileResult:
    path: str
    status: str
    trip_record_id: int | None = None
    duplicate_status: str = "unique"
    message: str | None = None


@dataclass
class ImportSummary:
    job_id: int
    root_path: str
    total_files: int
    success_count: int = 0
    failed_count: int = 0
    review_count: int = 0
    results: list[ImportFileResult] = field(default_factory=list)


class TripBatchImporter:
    def __init__(self, repository: HistoryRepository | None = None, extractor: TripExtractor | None = None):
        self.repository = repository or HistoryRepository()
        self.extractor = extractor or TripExtractor()

    def scan_pdfs(self, root_path: str | Path) -> list[Path]:
        root = Path(root_path)
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"历史材料根目录不存在或不是文件夹: {root}")
        return sorted(path for path in root.rglob("*.pdf") if path.is_file())

    def import_root(
        self,
        root_path: str | Path,
        *,
        enable_ocr: bool = True,
        enable_llm: bool = False,
        progress_callback=None,
    ) -> ImportSummary:
        pdfs = self.scan_pdfs(root_path)
        job = self.repository.create_import_job(str(Path(root_path).absolute()), len(pdfs))
        summary = ImportSummary(job_id=job.id, root_path=str(root_path), total_files=len(pdfs))

        try:
            for pdf_path in pdfs:
                result = self.import_pdf(
                    pdf_path,
                    import_job_id=job.id,
                    enable_ocr=enable_ocr,
                    enable_llm=enable_llm,
                )
                summary.results.append(result)
                if result.status == "failed":
                    summary.failed_count += 1
                    self.repository.increment_job(job.id, failed=True)
                else:
                    summary.success_count += 1
                    needs_review = result.duplicate_status != "unique" or result.status == "needs_review"
                    if needs_review:
                        summary.review_count += 1
                    self.repository.increment_job(job.id, success=True, review=needs_review)
                if progress_callback:
                    progress_callback(summary)

            self.repository.finish_import_job(job.id, "completed")
        except Exception as exc:
            self.repository.finish_import_job(job.id, "failed", str(exc))
            raise

        return summary

    def import_pdf(
        self,
        pdf_path: str | Path,
        *,
        import_job_id: int | None = None,
        enable_ocr: bool = True,
        enable_llm: bool = False,
    ) -> ImportFileResult:
        path = Path(pdf_path)
        file_hash = sha256_file(path)
        source = self.repository.create_source_file(
            import_job_id=import_job_id,
            filename=path.name,
            file_path=str(path.absolute()),
            file_hash=file_hash,
            file_size_kb=round(path.stat().st_size / 1024, 2),
        )

        try:
            exact_duplicate = self.repository.find_source_by_hash(file_hash)
            if exact_duplicate and exact_duplicate.trip_record_id:
                original = self.repository.get_record(exact_duplicate.trip_record_id)
                if original:
                    data = clone_record_data(original, path, file_hash)
                    duplicate_status = "exact_duplicate"
                    duplicate_reason = f"文件内容与历史来源 #{exact_duplicate.id} 完全一致"
                    needs_review = True
                    record = self._persist_trip(
                        data,
                        contacts=[],
                        import_job_id=import_job_id,
                        source_file_id=source.id,
                        duplicate_status=duplicate_status,
                        duplicate_reason=duplicate_reason,
                        needs_review=needs_review,
                    )
                    self.repository.mark_source_file(source.id, status="completed", trip_record_id=record.id)
                    return ImportFileResult(
                        path=str(path),
                        status="needs_review",
                        trip_record_id=record.id,
                        duplicate_status=duplicate_status,
                        message=duplicate_reason,
                    )

            extraction = self.extractor.extract_pdf(path, enable_ocr=enable_ocr, enable_llm=enable_llm)
            data = extraction.data
            data["source_hash"] = file_hash
            data["source_filename"] = path.name
            data["source_path"] = str(path.absolute())

            duplicate_status, duplicate_reason = self._duplicate_status(data)
            needs_review = duplicate_status != "unique" or bool(data.get("extraction_warnings"))
            record = self._persist_trip(
                data,
                contacts=extraction.contacts,
                import_job_id=import_job_id,
                source_file_id=source.id,
                duplicate_status=duplicate_status,
                duplicate_reason=duplicate_reason,
                needs_review=needs_review,
            )
            self.repository.mark_source_file(source.id, status="completed", trip_record_id=record.id)
            return ImportFileResult(
                path=str(path),
                status="needs_review" if needs_review else "completed",
                trip_record_id=record.id,
                duplicate_status=duplicate_status,
                message=duplicate_reason,
            )
        except Exception as exc:
            self.repository.mark_source_file(source.id, status="failed", error_message=str(exc))
            return ImportFileResult(path=str(path), status="failed", message=str(exc))

    def _persist_trip(
        self,
        data: dict[str, Any],
        *,
        contacts: list[dict[str, Any]],
        import_job_id: int | None,
        source_file_id: int | None,
        duplicate_status: str,
        duplicate_reason: str | None,
        needs_review: bool,
    ):
        org_data = {
            "name_cn": data.get("organization_name_cn"),
            "normalized_name": data.get("normalized_organization_name") or normalize_org_name(data.get("organization_name_cn") or ""),
            "name_en": data.get("organization_name_en"),
            "country": data.get("country"),
            "region": data.get("region"),
            "industry": data.get("industry"),
            "org_type": data.get("org_type"),
        }
        organization = self.repository.upsert_organization(org_data)
        record = self.repository.create_trip_record(
            data,
            import_job_id=import_job_id,
            source_file_id=source_file_id,
            organization_id=organization.id if organization else None,
            duplicate_status=duplicate_status,
            duplicate_reason=duplicate_reason,
            needs_review=needs_review,
        )
        self.repository.add_contacts(record.id, organization.id if organization else None, contacts)
        return record

    def _duplicate_status(self, data: dict[str, Any]) -> tuple[str, str | None]:
        matches = self.repository.find_possible_duplicates(data)
        if not matches:
            return "unique", None
        ids = ", ".join(f"#{record.id}" for record in matches)
        return "possible_duplicate", f"国家、时间、单位或天数与历史记录 {ids} 相似"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clone_record_data(record, path: Path, file_hash: str) -> dict[str, Any]:
    data = record_to_dict(record)
    data.pop("id", None)
    data.pop("created_at", None)
    data["source_filename"] = path.name
    data["source_path"] = str(path.absolute())
    data["source_hash"] = file_hash
    data["normalized_organization_name"] = normalize_org_name(data.get("organization_name_cn") or "")
    data["extraction_warnings"] = ["精确重复文件，字段复制自已入库记录"]
    return data
