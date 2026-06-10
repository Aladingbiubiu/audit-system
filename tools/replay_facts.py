from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.pdf_parser import extract_text_with_diagnostics
from core.rule_engine import DeterministicRuleEngine
from core.rule_model import DocumentFacts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay PDF/OCR fact extraction for audit field diagnostics."
    )
    parser.add_argument("path", help="PDF file or directory to replay")
    parser.add_argument("--no-ocr", action="store_true", help="Disable OCR for image-only pages")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of PDFs when replaying a directory")
    args = parser.parse_args()

    target = Path(args.path)
    pdfs = find_pdfs(target)
    if args.limit > 0:
        pdfs = pdfs[:args.limit]

    engine = DeterministicRuleEngine()
    results = []
    for pdf_path in pdfs:
        results.append(replay_pdf(pdf_path, engine, enable_ocr=not args.no_ocr))

    output: Any = results[0] if target.is_file() and len(results) == 1 else results
    print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def find_pdfs(path: Path) -> list[Path]:
    if path.is_file():
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"Only PDF files are supported: {path}")
        return [path]
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"Path does not exist or is not a directory: {path}")
    return sorted(item for item in path.rglob("*.pdf") if item.is_file())


def replay_pdf(pdf_path: Path, engine: DeterministicRuleEngine, *, enable_ocr: bool) -> dict[str, Any]:
    try:
        extraction = extract_text_with_diagnostics(pdf_path, enable_ocr=enable_ocr)
        facts = engine.extract_facts(extraction.page_texts)
        return {
            "path": str(pdf_path),
            "status": "ok",
            "diagnostics": {
                "page_count": extraction.page_count,
                "text_page_count": extraction.text_page_count,
                "image_only_pages": extraction.image_only_pages,
                "ocr_pages": extraction.ocr_pages,
                "paddle_ocr_pages": extraction.paddle_ocr_pages,
                "unreadable_pages": extraction.unreadable_pages,
            },
            "facts": facts_to_dict(facts),
        }
    except Exception as exc:
        return {
            "path": str(pdf_path),
            "status": "failed",
            "error": str(exc),
        }


def facts_to_dict(facts: DocumentFacts) -> dict[str, Any]:
    presentment = asdict(facts.presentment)
    presentment["traveler_names"] = sorted(facts.presentment.traveler_names)
    return {
        "profile": asdict(facts.profile),
        "group_unit_name": facts.group_unit_name,
        "presentment": presentment,
        "budget": asdict(facts.budget),
        "flags": {
            "has_personnel_list": facts.has_personnel_list,
            "has_public_notice": facts.has_public_notice,
            "has_translation_info": facts.has_translation_info,
            "has_academic_group_marker": facts.has_academic_group_marker,
        },
        "transport_refs": [
            {"page_no": page_no, "text": text}
            for page_no, text in facts.transport_refs
        ],
        "personnel_names": facts.personnel_names,
        "personnel_birth_dates": {
            name: {"page_no": value[0], "birth_date": value[1]}
            for name, value in facts.personnel_birth_dates.items()
        },
        "invitation_birth_dates": {
            name: {"page_no": value[0], "birth_date": value[1]}
            for name, value in facts.invitation_birth_dates.items()
        },
        "duration_values": {
            label: {"page_no": value[0], "duration_days": value[1]}
            for label, value in facts.duration_values.items()
        },
        "invite_units": {
            label: {"page_no": value[0], "units": sorted(value[1])}
            for label, value in facts.invite_units.items()
        },
        "dispatch_source": facts.dispatch_source,
        "dispatch_material": facts.dispatch_material,
        "expense_source": facts.expense_source,
    }


if __name__ == "__main__":
    raise SystemExit(main())
