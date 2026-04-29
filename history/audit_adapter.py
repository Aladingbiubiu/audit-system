from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.pdf_parser import PDFTextExtraction, extract_text_with_diagnostics
from core.rule_engine import DeterministicRuleEngine
from core.rule_model import DocumentFacts


@dataclass
class ParsedPdf:
    path: Path
    extraction: PDFTextExtraction
    facts: DocumentFacts


class AuditExtractionAdapter:
    """Thin adapter over existing audit PDF/OCR and fact extraction utilities."""

    def __init__(self):
        self.rule_engine = DeterministicRuleEngine()

    def parse_pdf(self, pdf_path: str | Path, *, enable_ocr: bool = True) -> ParsedPdf:
        path = Path(pdf_path)
        extraction = extract_text_with_diagnostics(path, enable_ocr=enable_ocr)
        facts = self.rule_engine.extract_facts(extraction.page_texts)
        return ParsedPdf(path=path, extraction=extraction, facts=facts)

