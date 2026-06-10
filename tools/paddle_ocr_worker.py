from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

from paddleocr import PaddleOCR


def main() -> int:
    image_paths = [Path(item) for item in sys.argv[1:]]
    if not image_paths:
        print(json.dumps({"ok": False, "error": "No image paths provided"}))
        return 2

    ocr = PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="PP-OCRv5_mobile_rec",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    pages = []
    for image_path in image_paths:
        results = ocr.predict(input=str(image_path))
        result = results[0].json.get("res", {}) if results else {}
        texts = result.get("rec_texts") or []
        scores = result.get("rec_scores") or []
        lines = [
            str(text).strip()
            for text, score in zip(texts, scores)
            if str(text).strip() and float(score) >= 0.45
        ]
        pages.append({"path": str(image_path), "text": "\n".join(lines)})

    print(json.dumps({"ok": True, "pages": pages}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
