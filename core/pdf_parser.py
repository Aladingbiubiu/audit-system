import pdfplumber
import json
import os
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass

_OCR_ENGINE = None
ROOT = Path(__file__).resolve().parents[1]
PADDLE_PYTHON = ROOT / ".paddle-ocr-venv" / "Scripts" / "python.exe"
PADDLE_WORKER = ROOT / "tools" / "paddle_ocr_worker.py"


@dataclass
class PDFMetadata:
    filename: str
    page_count: int
    has_tables: bool
    file_size_kb: float
    text_page_count: int = 0
    image_only_pages: list[int] | None = None


@dataclass
class PDFTextExtraction:
    text: str
    page_count: int
    text_page_count: int
    image_only_pages: list[int]
    ocr_pages: list[int]
    paddle_ocr_pages: list[int]
    unreadable_pages: list[int]
    page_texts: list[tuple[int, str]]

    @property
    def has_unreadable_pages(self) -> bool:
        return bool(self.unreadable_pages)

    def warning_text(self) -> str:
        messages = []

        if self.ocr_pages:
            pages = "、".join(str(page) for page in self.ocr_pages)
            messages.append(f"注意：第 {pages} 页为扫描图片页，系统已通过 OCR 识别其文字。")
        if self.paddle_ocr_pages:
            pages = "、".join(str(page) for page in self.paddle_ocr_pages)
            messages.append(f"其中第 {pages} 页已使用 PaddleOCR 进行增强识别。")

        if self.unreadable_pages:
            pages = "、".join(str(page) for page in self.unreadable_pages)
            messages.append(
                f"注意：第 {pages} 页疑似扫描图片页，但 OCR 未能识别出文字。"
                "审核时不要仅因为这些页面未出现在文本中，就认定相关材料或字段缺失；"
                "应提示需要人工核对扫描页。"
            )

        return "\n".join(messages)


def _analyze_page(page) -> tuple[str, bool]:
    page_text = page.extract_text() or ""
    has_images = bool(page.images)
    is_image_only = not page_text.strip() and has_images
    return page_text, is_image_only


def _get_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _ocr_page(pdf_path: Path, page_index: int) -> str:
    import fitz
    import numpy as np

    ocr = _get_ocr_engine()
    with fitz.open(pdf_path) as doc:
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height,
            pix.width,
            pix.n
        )

    result, _ = ocr(image)
    if not result:
        return ""

    lines = []
    for item in result:
        if len(item) < 2:
            continue
        text = str(item[1]).strip()
        confidence = float(item[2]) if len(item) > 2 else 1.0
        if text and confidence >= 0.45:
            lines.append(text)

    return "\n".join(lines)


def _render_page_image(pdf_path: Path, page_index: int, output_path: Path) -> None:
    import fitz

    with fitz.open(pdf_path) as doc:
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        pix.save(output_path)


def _paddle_ocr_pages(pdf_path: Path, page_numbers: list[int]) -> dict[int, str]:
    if (
        os.environ.get("PADDLE_OCR_ENABLED", "1").lower() in {"0", "false", "no"}
        or not page_numbers
        or not PADDLE_PYTHON.exists()
        or not PADDLE_WORKER.exists()
    ):
        return {}

    with tempfile.TemporaryDirectory(prefix="audit-paddle-ocr-") as temp_dir:
        temp_path = Path(temp_dir)
        images = []
        for page_no in page_numbers:
            image_path = temp_path / f"page-{page_no}.png"
            _render_page_image(pdf_path, page_no - 1, image_path)
            images.append(image_path)

        env = os.environ.copy()
        env.setdefault("FLAGS_use_mkldnn", "0")
        env.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        try:
            completed = subprocess.run(
                [str(PADDLE_PYTHON), str(PADDLE_WORKER), *(str(path) for path in images)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                timeout=360,
                check=True,
            )
            payload = json.loads(completed.stdout.strip().splitlines()[-1].decode("utf-8"))
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, IndexError):
            return {}

        if not payload.get("ok"):
            return {}
        return {
            page_no: str(item.get("text") or "").strip()
            for page_no, item in zip(page_numbers, payload.get("pages") or [])
            if str(item.get("text") or "").strip()
        }


def _ocr_text_quality(text: str) -> float:
    compact = "".join((text or "").split())
    if not compact:
        return 0.0
    marker_bonus = sum(
        40
        for marker in ["呈报表", "人员名单", "邀请单位", "预算审批意见表", "身份证号码", "出访时间"]
        if marker in compact
    )
    return len(compact) + marker_bonus


def extract_text_with_diagnostics(pdf_path: str | Path, enable_ocr: bool = True) -> PDFTextExtraction:
    """提取PDF文本，并标记无法直接读取文字的扫描页。"""
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF文件不存在: {pdf_path}")

    text_parts = []
    image_only_pages = []
    ocr_pages = []
    paddle_ocr_pages = []
    unreadable_pages = []
    direct_texts: dict[int, str] = {}
    rapid_texts: dict[int, str] = {}
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            page_text, is_image_only = _analyze_page(page)
            if page_text:
                direct_texts[i] = page_text
            if is_image_only:
                image_only_pages.append(i)
                if enable_ocr:
                    ocr_text = _ocr_page(path, i - 1)
                    if ocr_text:
                        rapid_texts[i] = ocr_text
                    else:
                        unreadable_pages.append(i)
                else:
                    unreadable_pages.append(i)

        paddle_texts = _paddle_ocr_pages(path, image_only_pages) if enable_ocr else {}
        for page_no in image_only_pages:
            rapid_text = rapid_texts.get(page_no, "")
            paddle_text = paddle_texts.get(page_no, "")
            if paddle_text and _ocr_text_quality(paddle_text) >= _ocr_text_quality(rapid_text) * 0.85:
                rapid_texts[page_no] = paddle_text
                paddle_ocr_pages.append(page_no)
            if rapid_texts.get(page_no):
                ocr_pages.append(page_no)
                if page_no in unreadable_pages:
                    unreadable_pages.remove(page_no)

        for page_no in range(1, len(pdf.pages) + 1):
            if page_no in direct_texts:
                text_parts.append(f"--- 第 {page_no} 页 ---\n{direct_texts[page_no]}")
            elif page_no in rapid_texts:
                text_parts.append(f"--- 第 {page_no} 页（OCR识别）---\n{rapid_texts[page_no]}")

        return PDFTextExtraction(
            text="\n\n".join(text_parts),
            page_count=len(pdf.pages),
            text_page_count=len(direct_texts),
            image_only_pages=image_only_pages,
            ocr_pages=ocr_pages,
            paddle_ocr_pages=paddle_ocr_pages,
            unreadable_pages=unreadable_pages,
            page_texts=_build_page_texts(text_parts)
        )


def _build_page_texts(text_parts: list[str]) -> list[tuple[int, str]]:
    page_texts = []
    for item in text_parts:
        if not item.startswith("--- 第 "):
            continue
        prefix, _, body = item.partition("---\n")
        page_label = prefix.replace("--- 第 ", "").replace(" 页（OCR识别）", "").replace(" 页", "")
        try:
            page_no = int(page_label)
        except ValueError:
            continue
        page_texts.append((page_no, body))
    return page_texts


def extract_text(pdf_path: str | Path) -> str:
    """提取PDF全部文本内容"""
    return extract_text_with_diagnostics(pdf_path).text


def extract_tables(pdf_path: str | Path) -> list[list[list[str]]]:
    """提取PDF中的所有表格"""
    path = Path(pdf_path)
    tables = []

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_tables = page.extract_tables()
            tables.extend(page_tables or [])

    return tables


def extract_metadata(pdf_path: str | Path) -> PDFMetadata:
    """提取PDF元数据"""
    path = Path(pdf_path)
    file_size = path.stat().st_size / 1024

    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        has_tables = any(page.extract_tables() for page in pdf.pages)
        text_page_count = 0
        image_only_pages = []
        for i, page in enumerate(pdf.pages, 1):
            page_text, is_image_only = _analyze_page(page)
            if page_text.strip():
                text_page_count += 1
            if is_image_only:
                image_only_pages.append(i)

    return PDFMetadata(
        filename=path.name,
        page_count=page_count,
        has_tables=has_tables,
        file_size_kb=round(file_size, 2),
        text_page_count=text_page_count,
        image_only_pages=image_only_pages
    )


def validate_pdf(pdf_path: str | Path, max_size_mb: float = 20) -> tuple[bool, str]:
    """验证PDF文件是否有效"""
    path = Path(pdf_path)

    if not path.exists():
        return False, "文件不存在"

    if not path.suffix.lower() == ".pdf":
        return False, "文件格式不是PDF"

    file_size_mb = path.stat().st_size / (1024 * 1024)
    if file_size_mb > max_size_mb:
        return False, f"文件大小超过限制 ({file_size_mb:.1f}MB > {max_size_mb}MB)"

    try:
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) == 0:
                return False, "PDF文件无内容"
    except Exception as e:
        return False, f"PDF文件损坏或无法读取: {str(e)}"

    return True, "验证通过"
