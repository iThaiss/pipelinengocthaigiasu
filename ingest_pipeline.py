import argparse
import base64
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
import fitz  # PyMuPDF
from supabase import Client, create_client

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

# ============================================================
# CONFIG
# ============================================================
CLAUDE_API_KEY = os.getenv("NINEROUTER_API_KEY") or os.getenv("CLAUDE_API_KEY", "")
CLAUDE_BASE_URL = os.getenv("CLAUDE_BASE_URL", "http://localhost:20128")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "cc/claude-sonnet-4-6")
CLAUDE_FAST_MODEL = os.getenv("CLAUDE_FAST_MODEL", "cc/claude-haiku-4-5-20251001")
CLAUDE_VISION_MODEL = os.getenv("CLAUDE_VISION_MODEL", CLAUDE_MODEL)

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://eqrrjarsnrtvlsdfjhph.supabase.co")
SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVxcnJqYXJzbnJ0dmxzZGZqaHBoIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODIxNDAzOSwiZXhwIjoyMDkzNzkwMDM5fQ.4fxCziaZpYcqMlsDm8BxPgCdMFX7mbIoene1XI2z7Yw",
)

DATABASE_FOLDER = os.getenv("DATABASE_FOLDER", r"D:\Database")
LOG_FILE = os.getenv("PIPELINE_LOG_FILE", "logs/pipeline.log")
PROCESSED_FILE = os.getenv("PROCESSED_FILE", "logs/processed.log")
ERROR_FILE = os.getenv("ERROR_FILE", "logs/error.log")
RETRY_FILE = os.getenv("RETRY_FILE", "logs/retry_pages.log")
CACHE_DIR = Path(os.getenv("INGEST_CACHE_DIR", ".ingest_cache"))

MAX_PAGE_WORKERS = int(os.getenv("MAX_PAGE_WORKERS", "1"))
BATCH_SIZE = int(os.getenv("SUPABASE_BATCH_SIZE", "100"))
RETRY_COUNT = int(os.getenv("CLAUDE_RETRY_COUNT", "3"))
RENDER_ZOOM = float(os.getenv("PDF_RENDER_ZOOM", "2.0"))
FALLBACK_RENDER_ZOOMS = [
    float(value)
    for value in os.getenv("PDF_FALLBACK_RENDER_ZOOMS", "1.5,1.0").split(",")
    if value.strip()
]
SEND_IMAGE_FOR_ALL_PAGES = os.getenv("SEND_IMAGE_FOR_ALL_PAGES", "0") != "0"
SMART_IMAGE_PAGES = os.getenv("SMART_IMAGE_PAGES", "1") != "0"
MIN_TEXT_CHARS_FOR_TEXT_ONLY = int(os.getenv("MIN_TEXT_CHARS_FOR_TEXT_ONLY", "800"))
UPLOAD_VISUALS = os.getenv("UPLOAD_VISUALS", "1") != "0"
VISUAL_BUCKET = os.getenv("VISUAL_BUCKET", "question-visuals")
VISUAL_CROP_MODE = os.getenv("VISUAL_CROP_MODE", "question")
INGEST_EXPLANATION_MODE = os.getenv("INGEST_EXPLANATION_MODE", os.getenv("INGEST_EXPLANATION", "auto")).lower()
AUTO_SELECT_MODEL = os.getenv("AUTO_SELECT_MODEL", "1") != "0"
INGEST_USE_CACHE = os.getenv("INGEST_USE_CACHE", "1") != "0"

# ============================================================
# LOGGING
# ============================================================
for log_path in (LOG_FILE, PROCESSED_FILE, ERROR_FILE, RETRY_FILE):
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


def normalize_anthropic_base_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1"):
        return base_url[:-3]
    return base_url


# ============================================================
# PROMPT
# ============================================================
SYSTEM_PROMPT = """Bạn là chuyên gia trích xuất đề thi Toán THPT Việt Nam.
Nhiệm vụ: đọc nội dung trang PDF từ text và/hoặc ảnh, trích xuất tất cả câu hỏi, phân loại đúng, trả về JSON thuần túy.
Không dùng markdown. Không giải thêm nếu PDF không có lời giải. Không bỏ sót câu vì trang có tiêu đề, watermark, hình, bảng hoặc scan."""

USER_INSTRUCTIONS = """
Trích xuất TẤT CẢ câu hỏi có trên trang PDF. Nếu ảnh trang có nội dung mà text OCR bị thiếu, ưu tiên ảnh.

Quy tắc:
1. Bỏ qua watermark, footer, quảng cáo, số trang, tên fanpage nếu không phải câu hỏi.
2. Không trả items rỗng khi trang có câu hỏi.
3. Phân loại:
   - multiple_choice: có 4 lựa chọn A/B/C/D, chọn 1 đáp án.
   - true_false: có 4 mệnh đề a/b/c/d đúng-sai; giữ trong mảng statements, không tách thành 4 câu.
   - short_answer: câu trả lời ngắn/điền số/kết quả.
   - theory: lý thuyết, định nghĩa, định lý.
   - example: ví dụ minh họa có lời giải.
4. Nếu câu cần hình, bảng, đồ thị hoặc dữ liệu từ ảnh, đặt needs_visual=true và chọn visual_type phù hợp.
5. Không giải thêm. Không tự tạo lời giải nếu PDF không có sẵn.
6. Chuẩn hóa toán bằng LaTeX: inline $...$, display $$...$$.
7. Nếu không chắc đáp án đúng, để correct_answer/null hoặc numeric_answer/null, không đoán bừa.
8. Chỉ trả về JSON object hợp lệ đúng schema sau:

{
  "items": [
    {
      "type": "multiple_choice | true_false | short_answer | theory | example",
      "part": "I | II | III | null",
      "topic": "string | null",
      "subtopic": "string | null",
      "chapter": "string | null",
      "grade": 10,
      "difficulty": "Nhận biết | Thông hiểu | Vận dụng | Vận dụng cao | null",
      "question_text": "string",
      "option_a": "string | null",
      "option_b": "string | null",
      "option_c": "string | null",
      "option_d": "string | null",
      "correct_answer": "A | B | C | D | null",
      "statements": [
        {"label": "a", "text": "string", "answer": true}
      ],
      "numeric_answer": "string | number | null",
      "needs_visual": false,
      "visual_type": "bang_bien_thien | do_thi | hinh_khong_gian | bang_so_lieu | so_do_cay | hinh_hoc_phang | khac | null",
      "visual_bbox": [0.10, 0.20, 0.90, 0.60],
      "visual_table": {
        "kind": "sign_table | variation_table | frequency_table | generic_table",
        "rows": [
          {"label": "x", "cells": ["-infty", "0", "2", "+infty"]},
          {"label": "f'(x)", "cells": ["-", "0", "-", "0", "+"]},
          {"label": "f(x)", "cells": ["0", "down to -infty", "+infty", "down to -2", "up to +infty"]}
        ]
      },
      "explanation": "string | null",
      "source_hint": "string | null"
    }
  ]
}

Nếu needs_visual=true, hãy trả visual_bbox là vùng chứa hình/bảng/đồ thị liên quan trên ảnh trang theo tọa độ chuẩn hóa:
[x_min, y_min, x_max, y_max], mỗi số từ 0 đến 1 tính từ góc trên trái trang.
Nếu không xác định được vùng chính xác, để visual_bbox=null.
If the visual is a sign table, variation table, frequency table, or grouped-data table, also fill visual_table with clean structured rows. Ignore watermark text. Keep arrows as words such as "up to", "down to", or symbols "↑", "↓", "↗", "↘" if visible.
"""

EXPLANATION_INSTRUCTION = """
Nếu PDF có lời giải/hướng dẫn giải trên trang, đưa vào explanation.
Nếu không có lời giải trong PDF, explanation=null.
"""

NO_EXPLANATION_INSTRUCTION = """
Luôn đặt explanation=null để tiết kiệm token.
Không trích lời giải, không tóm tắt lời giải, không tự giải.
"""

SOLUTION_HINTS = (
    "đáp án",
    "dap an",
    "da ",
    " da",
    "đa ",
    " đa",
    "lời giải",
    "loi giai",
    "hướng dẫn giải",
    "huong dan giai",
    "chữa đề",
    "chua de",
    "đã giải",
    "da giai",
    "đáp số",
    "dap so",
)

ALLOWED_TYPES = {"multiple_choice", "true_false", "short_answer", "theory", "example"}
ALLOWED_DIFFICULTIES = {"Nhận biết", "Thông hiểu", "Vận dụng", "Vận dụng cao"}
ALLOWED_VISUAL_TYPES = {
    "bang_bien_thien",
    "do_thi",
    "hinh_khong_gian",
    "bang_so_lieu",
    "so_do_cay",
    "hinh_hoc_phang",
    "khac",
}

QUESTION_LIKE_STATEMENT_PREFIXES = (
    "tinh",
    "hay tinh",
    "hay uoc luong",
    "uoc luong",
    "tim",
    "xac dinh",
    "cho biet",
    "viet",
    "giai",
)
TRUE_FALSE_CONTEXT_MARKERS = (
    "dung sai",
    "dung/sai",
    "dung hay sai",
    "xet tinh dung sai",
    "moi menh de",
    "cac menh de sau",
)

GROUPED_DATA_KEYWORDS = (
    "mẫu số liệu ghép nhóm",
    "mau so lieu ghep nhom",
    "số liệu ghép nhóm",
    "so lieu ghep nhom",
    "bảng tần số ghép nhóm",
    "bang tan so ghep nhom",
)

VISUAL_TABLE_KEYWORDS = (
    "bảng xét dấu",
    "bang xet dau",
    "bảng biến thiên",
    "bang bien thien",
    "bảng giá trị",
    "bang gia tri",
    "bảng sau",
    "bang sau",
    "bảng như sau",
    "bang nhu sau",
    "bảng tần số",
    "bang tan so",
)

PAGE_VISUAL_KEYWORDS = (
    "hình vẽ",
    "hinh ve",
    "hình bên",
    "hinh ben",
    "hình dưới",
    "hinh duoi",
    "như hình",
    "nhu hinh",
    "đồ thị",
    "do thi",
    "biểu đồ",
    "bieu do",
    "sơ đồ",
    "so do",
    "bảng xét dấu",
    "bang xet dau",
    "bảng biến thiên",
    "bang bien thien",
    "bảng tần số",
    "bang tan so",
    "bảng số liệu",
    "bang so lieu",
    "mẫu số liệu ghép nhóm",
    "mau so lieu ghep nhom",
)

PUA_MAP = {
    "\uf0e9": "[",
    "\uf0eb": "[",
    "\uf0f9": "]",
    "\uf0fb": "]",
    "\uf028": "(",
    "\uf029": ")",
    "\uf05b": "[",
    "\uf05d": "]",
    "\uf0b1": "±",
    "\uf0b4": "×",
    "\uf0b8": "÷",
    "\uf0d7": "×",
    "\uf070": "π",
    "\uf071": "θ",
    "\uf061": "α",
    "\uf062": "β",
    "\uf067": "γ",
    "\uf064": "δ",
    "\uf06c": "λ",
    "\uf06d": "μ",
    "\uf073": "σ",
    "\uf077": "ω",
    "\uf066": "φ",
    "\uf059": "Ψ",
    "\uf0a3": "≤",
    "\uf0b3": "≥",
    "\uf0b9": "≠",
    "\uf0cc": "∞",
    "\uf0de": "↑",
    "\uf0df": "↓",
    "\uf0ae": "→",
    "\uf0ac": "←",
    "\uf0d1": "√",
    "\uf0f2": "∫",
    "\uf0e5": "∑",
}


@dataclass(frozen=True)
class PagePayload:
    filepath: str
    page_index: int
    page_number: int
    text: str
    used_image: bool


def load_processed() -> set[str]:
    path = Path(PROCESSED_FILE)
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def mark_processed(filepath: str) -> None:
    with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
        f.write(filepath + "\n")


def mark_error(filepath: str, error: str) -> None:
    with open(ERROR_FILE, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} | {filepath} | {error}\n")


def mark_retry(filepath: str, page_number: int, reason: str) -> None:
    with open(RETRY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{filepath}|{page_number}|{reason}\n")


def get_source_name(filepath: str) -> str:
    parts = Path(filepath).parts
    db_idx = next((i for i, part in enumerate(parts) if part.lower() == "database"), None)
    if db_idx is not None and db_idx + 1 < len(parts):
        return parts[db_idx + 1]
    return Path(filepath).parent.name


def file_sha1(filepath: str) -> str:
    digest = hashlib.sha1()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_path(file_hash: str, page_number: int) -> Path:
    return CACHE_DIR / file_hash / f"page_{page_number:04d}.json"


def load_page_cache(file_hash: str, page_number: int) -> dict[str, Any] | None:
    path = cache_path(file_hash, page_number)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_page_cache(file_hash: str, page_number: int, result: dict[str, Any]) -> None:
    path = cache_path(file_hash, page_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def clean_page_text(text: str) -> str:
    text = "".join(PUA_MAP.get(ch, ch) if 0xF000 <= ord(ch) <= 0xF8FF else ch for ch in text)
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{4,}", "\n\n", text)
    return text.strip()


def render_page_png_base64(page: fitz.Page, zoom: float = RENDER_ZOOM) -> str:
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False, colorspace=fitz.csRGB)
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


def render_page_from_file_png_base64(filepath: str, page_index: int, zoom: float = RENDER_ZOOM) -> str:
    with fitz.open(filepath) as pdf:
        return render_page_png_base64(pdf[page_index], zoom=zoom)


def select_model(payload: PagePayload, zoom: float | None) -> str:
    if not AUTO_SELECT_MODEL:
        return CLAUDE_MODEL
    if zoom is not None:
        return CLAUDE_VISION_MODEL
    return CLAUDE_FAST_MODEL


def normalize_for_match(value: str) -> str:
    value = value.lower().replace("đ", "d")
    return "".join(
        ch for ch in unicodedata.normalize("NFD", value) if unicodedata.category(ch) != "Mn"
    )


def is_grouped_data_question(*values: Any) -> bool:
    haystack = " ".join(str(value or "") for value in values)
    normalized = normalize_for_match(haystack)
    return any(keyword in normalized for keyword in GROUPED_DATA_KEYWORDS)


def is_visual_table_question(*values: Any) -> bool:
    haystack = " ".join(str(value or "") for value in values)
    normalized = normalize_for_match(haystack)
    return any(keyword in normalized for keyword in VISUAL_TABLE_KEYWORDS)


def page_text_needs_image(text: str) -> bool:
    normalized = normalize_for_match(text)
    return any(keyword in normalized for keyword in PAGE_VISUAL_KEYWORDS)


def should_extract_explanation(payload: PagePayload) -> bool:
    mode = INGEST_EXPLANATION_MODE
    if mode in {"1", "true", "yes", "always", "on"}:
        return True
    if mode in {"0", "false", "no", "never", "off"}:
        return False

    filename = Path(payload.filepath).name
    raw_haystack = f" {filename}\n{payload.text[:3000]} ".lower()
    normalized_haystack = normalize_for_match(raw_haystack)
    return any(
        hint in raw_haystack or normalize_for_match(hint) in normalized_haystack
        for hint in SOLUTION_HINTS
    )


def extract_question_number(source_hint: str | None) -> int | None:
    if not source_hint:
        return None
    match = re.search(r"c[âa]u\s*(\d+)", source_hint, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def block_text(block: dict[str, Any]) -> str:
    parts: list[str] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text = span.get("text")
            if text:
                parts.append(text)
    return " ".join(parts)


def find_question_block_clip(page: fitz.Page, question_number: int | None) -> fitz.Rect | None:
    if question_number is None:
        return None

    rect = page.rect
    question_markers: list[tuple[int, float]] = []
    for block in page.get_text("dict").get("blocks", []):
        if "lines" not in block:
            continue
        text = block_text(block)
        for match in re.finditer(r"c[âa]u\s*(\d+)", text, re.IGNORECASE):
            question_markers.append((int(match.group(1)), float(block["bbox"][1])))

    if not question_markers:
        return None

    starts = [y for number, y in question_markers if number == question_number]
    if not starts:
        return None

    start_y = min(starts)
    next_candidates = [
        y
        for number, y in question_markers
        if number > question_number and y > start_y + rect.height * 0.03
    ]
    end_y = min(next_candidates) if next_candidates else rect.y1

    top_margin = rect.height * 0.025
    bottom_margin = rect.height * 0.015
    return fitz.Rect(
        rect.x0,
        max(rect.y0, start_y - top_margin),
        rect.x1,
        min(rect.y1, end_y - bottom_margin),
    )


def bbox_to_clip(page: fitz.Page, bbox: list[float] | None, generous: bool = False) -> fitz.Rect | None:
    if not bbox or len(bbox) != 4:
        return None
    rect = page.rect
    x1, y1, x2, y2 = bbox
    if generous:
        margin_x = 1.0
        margin_top = max(0.16, (y2 - y1) * 0.9)
        margin_bottom = max(0.12, (y2 - y1) * 0.9)
    else:
        margin_x = max(0.035, (x2 - x1) * 0.15)
        margin_top = max(0.035, (y2 - y1) * 0.15)
        margin_bottom = margin_top
    x1 = max(0.0, x1 - margin_x)
    y1 = max(0.0, y1 - margin_top)
    x2 = min(1.0, x2 + margin_x)
    y2 = min(1.0, y2 + margin_bottom)
    if x2 <= x1 or y2 <= y1:
        return None
    return fitz.Rect(
        rect.x0 + rect.width * x1,
        rect.y0 + rect.height * y1,
        rect.x0 + rect.width * x2,
        rect.y0 + rect.height * y2,
    )


def render_visual_png_bytes(
    filepath: str,
    page_number: int,
    bbox: list[float] | None,
    source_hint: str | None = None,
) -> bytes:
    with fitz.open(filepath) as pdf:
        page = pdf[page_number - 1]
        question_clip = find_question_block_clip(page, extract_question_number(source_hint))
        bbox_clip = bbox_to_clip(page, bbox)
        wide_bbox_clip = bbox_to_clip(page, bbox, generous=True)
        if VISUAL_CROP_MODE == "bbox":
            clip = bbox_clip or question_clip
        elif VISUAL_CROP_MODE == "page":
            clip = None
        else:
            clip = wide_bbox_clip or question_clip or bbox_clip

        matrix = fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM)
        pix = page.get_pixmap(matrix=matrix, alpha=False, colorspace=fitz.csRGB, clip=clip)
        return pix.tobytes("png")


def build_page_payloads(filepath: str) -> list[PagePayload]:
    payloads: list[PagePayload] = []
    with fitz.open(filepath) as pdf:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            text = clean_page_text(page.get_text("text") or "")
            used_image = (
                SEND_IMAGE_FOR_ALL_PAGES
                or len(text) < MIN_TEXT_CHARS_FOR_TEXT_ONLY
                or (SMART_IMAGE_PAGES and page_text_needs_image(text))
            )
            payloads.append(
                PagePayload(
                    filepath=filepath,
                    page_index=page_index,
                    page_number=page_index + 1,
                    text=text,
                    used_image=used_image,
                )
            )
    return payloads


def extract_json_object(raw_text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    starts = [match.start() for match in re.finditer(r"\{", raw_text)]
    last_error: Exception | None = None
    for start in starts:
        candidate = raw_text[start:].strip()
        try:
            data, _ = decoder.raw_decode(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as exc:
            last_error = exc

    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if match:
        json_str = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r"\\\\", match.group(0))
        try:
            data = json.loads(json_str)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as exc:
            last_error = exc

    raise ValueError(f"Cannot parse JSON response: {last_error or raw_text[:200]!r}")


def get_response_text(response: Any) -> str:
    if not getattr(response, "content", None):
        raise ValueError("Claude response has no text content")

    raw_text = ""
    for block in response.content:
        if getattr(block, "type", "") == "text":
            raw_text += block.text
    raw_text = raw_text.strip()
    if not raw_text:
        raise ValueError("Claude response text is empty")
    return raw_text


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "đúng", "co", "có"}
    return bool(value)


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return str(value)
    value = str(value).strip()
    if not value or value.lower() in {"null", "none", "n/a"}:
        return None
    return value


def fold_vietnamese_text(text: str) -> str:
    folded = unicodedata.normalize("NFD", text.lower())
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
    return folded.replace("đ", "d").replace("ð", "d")


def is_question_like_statement(text: str) -> bool:
    folded = fold_vietnamese_text(text).strip()
    folded = re.sub(r"^[a-d][\).:\-]\s*", "", folded)
    return folded.endswith("?") or any(
        folded.startswith(prefix) for prefix in QUESTION_LIKE_STATEMENT_PREFIXES
    )


def has_true_false_context(question_text: str) -> bool:
    folded = fold_vietnamese_text(question_text)
    return any(marker in folded for marker in TRUE_FALSE_CONTEXT_MARKERS)


def merge_subquestions_into_text(question_text: str, statements: list[dict[str, Any]]) -> str:
    lines = [question_text.rstrip()]
    for index, stmt in enumerate(statements):
        label = normalize_optional_text(stmt.get("label")) or chr(97 + index)
        text = normalize_optional_text(stmt.get("text"))
        if text:
            lines.append(f"{label}) {text}")
    return "\n".join(lines)


def normalize_numeric_answer(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value

    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a"}:
        return None

    # Supabase column is double precision, while Vietnamese answers often use a decimal comma.
    compact = text.replace(" ", "").replace(",", ".")
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", compact):
        number = float(compact)
        return int(number) if number.is_integer() else number

    fraction_match = re.fullmatch(r"([-+]?\d+)/(\d+)", compact)
    if fraction_match:
        numerator = int(fraction_match.group(1))
        denominator = int(fraction_match.group(2))
        if denominator:
            number = numerator / denominator
            return int(number) if number.is_integer() else number

    single_number = re.fullmatch(r".*?([-+]?\d+(?:[\.,]\d+)?).*", text)
    if single_number:
        number = float(single_number.group(1).replace(",", "."))
        return int(number) if number.is_integer() else number

    return None


def normalize_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        bbox = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if any(v < 0 or v > 1 for v in bbox):
        return None
    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        return None
    return bbox


def normalize_visual_table(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    rows = value.get("rows")
    if not isinstance(rows, list) or not rows:
        return None
    clean_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cells = row.get("cells")
        if not isinstance(cells, list):
            continue
        clean_cells = [str(cell).strip() for cell in cells if cell is not None and str(cell).strip()]
        if clean_cells:
            clean_rows.append({"label": normalize_optional_text(row.get("label")), "cells": clean_cells})
    if not clean_rows:
        return None
    return {
        "kind": normalize_optional_text(value.get("kind")) or "generic_table",
        "rows": clean_rows,
    }


def normalize_item(item: dict[str, Any], page_number: int, item_index: int) -> dict[str, Any] | None:
    q_type = normalize_optional_text(item.get("type")) or "multiple_choice"
    if q_type not in ALLOWED_TYPES:
        q_type = "multiple_choice"

    question_text = normalize_optional_text(item.get("question_text") or item.get("content"))
    if not question_text:
        return None

    difficulty = normalize_optional_text(item.get("difficulty"))
    if difficulty not in ALLOWED_DIFFICULTIES:
        difficulty = None

    grade = item.get("grade")
    try:
        grade = int(grade) if grade is not None else None
    except (TypeError, ValueError):
        grade = None
    if grade not in {10, 11, 12}:
        grade = None

    visual_type = normalize_optional_text(item.get("visual_type"))
    if visual_type not in ALLOWED_VISUAL_TYPES:
        visual_type = None

    correct_answer = normalize_optional_text(item.get("correct_answer"))
    if correct_answer:
        correct_answer = correct_answer.upper()
    if correct_answer not in {"A", "B", "C", "D"}:
        correct_answer = None

    statements = item.get("statements") if isinstance(item.get("statements"), list) else []
    clean_statements = []
    for stmt in statements:
        if not isinstance(stmt, dict):
            continue
        text = normalize_optional_text(stmt.get("text"))
        if not text:
            continue
        answer = stmt.get("answer")
        clean_statements.append(
            {
                "label": normalize_optional_text(stmt.get("label")) or chr(97 + len(clean_statements)),
                "text": text,
                "answer": answer if isinstance(answer, bool) or answer is None else normalize_bool(answer),
            }
        )

    if q_type == "true_false" and clean_statements:
        statement_answers = [stmt.get("answer") for stmt in clean_statements]
        looks_like_subquestions = any(is_question_like_statement(stmt["text"]) for stmt in clean_statements)
        lacks_true_false_shape = len(clean_statements) != 4 and not has_true_false_context(question_text)
        has_no_boolean_answers = all(answer is None for answer in statement_answers)
        if has_no_boolean_answers and (looks_like_subquestions or lacks_true_false_shape):
            question_text = merge_subquestions_into_text(question_text, clean_statements)
            clean_statements = []
            q_type = "short_answer"

    normalized = {
        "type": q_type,
        "part": normalize_optional_text(item.get("part")),
        "topic": normalize_optional_text(item.get("topic")) or "Chưa phân loại",
        "subtopic": normalize_optional_text(item.get("subtopic")),
        "chapter": normalize_optional_text(item.get("chapter")),
        "grade": grade,
        "difficulty": difficulty,
        "question_text": question_text,
        "option_a": normalize_optional_text(item.get("option_a")),
        "option_b": normalize_optional_text(item.get("option_b")),
        "option_c": normalize_optional_text(item.get("option_c")),
        "option_d": normalize_optional_text(item.get("option_d")),
        "correct_answer": correct_answer,
        "statements": clean_statements,
        "numeric_answer": normalize_numeric_answer(item.get("numeric_answer")),
        "needs_visual": normalize_bool(item.get("needs_visual", False)),
        "visual_type": visual_type,
        "visual_bbox": normalize_bbox(item.get("visual_bbox")),
        "visual_table": normalize_visual_table(item.get("visual_table")),
        "explanation": normalize_optional_text(item.get("explanation")),
        "source_hint": normalize_optional_text(item.get("source_hint")) or f"Trang {page_number}, mục {item_index}",
    }

    if q_type == "true_false" and clean_statements:
        normalized["needs_visual"] = normalized["needs_visual"] or any(
            keyword in question_text.lower()
            for keyword in ["hình", "bảng", "đồ thị", "biểu đồ", "sơ đồ"]
        )

    if is_grouped_data_question(
        question_text,
        normalized.get("topic"),
        normalized.get("subtopic"),
        normalized.get("chapter"),
    ):
        normalized["needs_visual"] = True
        normalized["visual_type"] = "bang_so_lieu"

    if is_visual_table_question(question_text):
        normalized["needs_visual"] = True
        normalized["visual_type"] = normalized.get("visual_type") or "bang_bien_thien"

    return normalized


def normalize_result(result: dict[str, Any], page_number: int) -> dict[str, Any]:
    items = result.get("items", [])
    if not isinstance(items, list):
        items = []

    normalized_items = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            normalized = normalize_item(item, page_number, index)
            if normalized:
                normalized_items.append(normalized)

    return {"items": normalized_items}


def parse_page_with_claude(client: anthropic.Anthropic, payload: PagePayload) -> dict[str, Any]:
    text_block = payload.text if payload.text else "[Không có text extract được; hãy OCR từ ảnh trang.]"
    extract_explanation = should_extract_explanation(payload)
    instructions = USER_INSTRUCTIONS + (
        EXPLANATION_INSTRUCTION if extract_explanation else NO_EXPLANATION_INSTRUCTION
    )
    user_text = (
        f"{instructions}\n\n"
        f"Trang PDF số {payload.page_number}.\n"
        f"Text extract từ PDF, có thể thiếu hoặc sai thứ tự:\n"
        f"<pdf_text>\n{text_block}\n</pdf_text>"
    )

    def build_content(zoom: float | None) -> list[dict[str, Any]] | str:
        if zoom is None:
            return user_text

        image_b64 = render_page_from_file_png_base64(payload.filepath, payload.page_index, zoom=zoom)
        return [
            {"type": "text", "text": user_text},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_b64,
                },
            },
        ]

    zoom_plan: list[float | None] = [None]
    if payload.used_image:
        zoom_plan = [RENDER_ZOOM, *FALLBACK_RENDER_ZOOMS, None]

    last_error: Exception | None = None
    max_attempts = max(RETRY_COUNT, len(zoom_plan))
    for attempt in range(1, max_attempts + 1):
        zoom = zoom_plan[min(attempt - 1, len(zoom_plan) - 1)]
        try:
            content = build_content(zoom)
            model_name = select_model(payload, zoom)
            response = client.messages.create(
                model=model_name,
                max_tokens=6000,
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
                extra_headers={"User-Agent": "curl/8.7.1"},
            )
            raw_text = get_response_text(response)
            data = extract_json_object(raw_text)
            return normalize_result(data, payload.page_number)
        except Exception as exc:
            last_error = exc
            mode = "text-only" if zoom is None else f"image zoom={zoom}"
            model_name = select_model(payload, zoom)
            log.warning(
                "Trang %s parse lỗi lần %s/%s (%s, model=%s): %s",
                payload.page_number,
                attempt,
                max_attempts,
                mode,
                model_name,
                exc,
            )
            if attempt < max_attempts:
                time.sleep(min(30, 2**attempt + random.random()))

    raise RuntimeError(f"Page {payload.page_number} failed after {max_attempts} attempts: {last_error}")


def parse_page_cached(client: anthropic.Anthropic, file_hash: str, payload: PagePayload) -> tuple[int, dict[str, Any], bool]:
    if INGEST_USE_CACHE:
        cached = load_page_cache(file_hash, payload.page_number)
        if cached is not None:
            normalized_cached = normalize_result(cached, payload.page_number)
            if normalized_cached != cached:
                save_page_cache(file_hash, payload.page_number, normalized_cached)
            return payload.page_number, normalized_cached, True

    result = parse_page_with_claude(client, payload)
    save_page_cache(file_hash, payload.page_number, result)
    return payload.page_number, result, False


def upload_question_visual(
    supabase: Client,
    filepath: str,
    file_hash: str,
    page_number: int,
    item_index: int,
    bbox: list[float] | None,
    source_hint: str | None,
) -> str | None:
    if not UPLOAD_VISUALS:
        return None

    image_bytes = render_visual_png_bytes(filepath, page_number, bbox, source_hint)
    object_path = f"{file_hash}/page_{page_number:04d}_item_{item_index:02d}_{VISUAL_CROP_MODE}.png"
    try:
        supabase.storage.from_(VISUAL_BUCKET).upload(
            object_path,
            image_bytes,
            file_options={"content-type": "image/png", "upsert": "true"},
        )
    except Exception as exc:
        message = str(exc).lower()
        if "already exists" not in message and "duplicate" not in message:
            raise

    public_url = supabase.storage.from_(VISUAL_BUCKET).get_public_url(object_path)
    if isinstance(public_url, str):
        return public_url
    if isinstance(public_url, dict):
        return public_url.get("publicUrl") or public_url.get("public_url") or object_path
    return object_path


def normalized_question_signature(item: dict[str, Any]) -> str:
    parts = [
        item.get("type"),
        item.get("question_text"),
        item.get("option_a"),
        item.get("option_b"),
        item.get("option_c"),
        item.get("option_d"),
    ]
    text = "\n".join(str(part or "") for part in parts)
    text = normalize_for_match(text)
    text = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def question_quality_flags(item: dict[str, Any]) -> tuple[bool, bool]:
    needs_review = bool(item.get("needs_visual", False))

    if item["type"] == "multiple_choice":
        missing_options = any(not item.get(key) for key in ("option_a", "option_b", "option_c", "option_d"))
        missing_answer = not item.get("correct_answer")
        needs_review = needs_review or missing_options or missing_answer
    elif item["type"] == "true_false":
        statements = item.get("statements") if isinstance(item.get("statements"), list) else []
        has_missing_statement_answer = any(statement.get("answer") is None for statement in statements)
        needs_review = needs_review or len(statements) != 4 or has_missing_statement_answer

    return needs_review, not needs_review


def question_row(
    item: dict[str, Any],
    doc_id: str,
    source_file: str,
    page_number: int,
    image_url: str | None = None,
) -> dict[str, Any]:
    needs_review, is_published = question_quality_flags(item)
    data = {
        "raw_document_id": doc_id,
        "source_code": normalized_question_signature(item),
        "question_type": item["type"],
        "topic": item.get("topic") or "Chưa phân loại",
        "subtopic": item.get("subtopic"),
        "chapter": item.get("chapter"),
        "part": item.get("part"),
        "question_text": item.get("question_text", ""),
        "difficulty": item.get("difficulty"),
        "needs_visual": item.get("needs_visual", False),
        "visual_type": item.get("visual_type"),
        "source_hint": item.get("source_hint"),
        "source_file": source_file,
        "page_number": page_number,
        "explanation": item.get("explanation"),
        "answer_source": "original",
        "needs_review": needs_review,
        "has_image": bool(image_url),
        "image_url": image_url,
        "visual_image_url": image_url,
        "raw_text": json.dumps(item, ensure_ascii=False),
        "is_published": is_published,
    }

    if item["type"] == "multiple_choice":
        data.update(
            {
                "option_a": item.get("option_a"),
                "option_b": item.get("option_b"),
                "option_c": item.get("option_c"),
                "option_d": item.get("option_d"),
                "correct_answer": item.get("correct_answer"),
            }
        )
    elif item["type"] == "true_false":
        data["statements"] = json.dumps(item.get("statements", []), ensure_ascii=False)
    elif item["type"] == "short_answer":
        data["numeric_answer"] = item.get("numeric_answer")

    return data


def theory_row(item: dict[str, Any], doc_id: str) -> dict[str, Any]:
    return {
        "raw_document_id": doc_id,
        "topic": item.get("topic") or "Chưa phân loại",
        "content": item.get("question_text", ""),
        "content_type": item.get("type", "theory"),
        "raw_text": json.dumps(item, ensure_ascii=False),
        "is_published": True,
    }


def insert_batches(supabase: Client, table: str, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        if batch:
            supabase.table(table).insert(batch).execute()


def filter_duplicate_question_rows(supabase: Client, rows: list[dict[str, Any]], dry_run: bool) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique_rows: list[dict[str, Any]] = []
    for row in rows:
        source_code = row.get("source_code")
        if source_code and source_code in seen:
            row["needs_review"] = True
            row["is_published"] = False
            continue
        if source_code:
            seen.add(source_code)
        unique_rows.append(row)

    if dry_run or not unique_rows:
        return unique_rows

    codes = [row["source_code"] for row in unique_rows if row.get("source_code")]
    existing: set[str] = set()
    for start in range(0, len(codes), 100):
        chunk = codes[start : start + 100]
        if not chunk:
            continue
        result = supabase.table("questions").select("source_code").in_("source_code", chunk).execute()
        existing.update(row["source_code"] for row in (result.data or []) if row.get("source_code"))

    filtered = [row for row in unique_rows if row.get("source_code") not in existing]
    skipped = len(unique_rows) - len(filtered)
    if skipped:
        log.info("   Bỏ qua %s câu trùng source_code đã có trong DB.", skipped)
    return filtered


def process_pdf(
    filepath: str,
    supabase: Client,
    client: anthropic.Anthropic,
    force: bool = False,
    dry_run: bool = False,
    max_questions: int = 0,
    max_pages: int = 0,
    page_from: int = 0,
    page_to: int = 0,
) -> dict[str, int]:
    filename = Path(filepath).name
    source = get_source_name(filepath)
    digest = file_sha1(filepath)
    log.info("📄 Đang xử lý: %s (nguồn: %s, sha1: %s)", filename, source, digest[:10])

    payloads = build_page_payloads(filepath)
    if page_from > 0:
        payloads = [payload for payload in payloads if payload.page_number >= page_from]
    if page_to > 0:
        payloads = [payload for payload in payloads if payload.page_number <= page_to]
    if max_pages > 0:
        payloads = payloads[:max_pages]
    stats = {
        "questions": 0,
        "theory": 0,
        "errors": 0,
        "pages": len(payloads),
        "pages_from_cache": 0,
        "pages_with_image": sum(1 for payload in payloads if payload.used_image),
    }
    log.info("   → %s trang | gửi ảnh: %s trang", stats["pages"], stats["pages_with_image"])

    page_results: dict[int, dict[str, Any]] = {}
    failures: list[tuple[int, str]] = []
    workers = max(1, MAX_PAGE_WORKERS)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(parse_page_cached, client, digest, payload): payload.page_number
            for payload in payloads
        }
        for future in as_completed(futures):
            page_number = futures[future]
            try:
                result_page, result, from_cache = future.result()
                page_results[result_page] = result
                stats["pages_from_cache"] += int(from_cache)
                log.info(
                    "   ✓ Trang %s/%s: %s items%s",
                    result_page,
                    stats["pages"],
                    len(result.get("items", [])),
                    " (cache)" if from_cache else "",
                )
            except Exception as exc:
                reason = str(exc)
                failures.append((page_number, reason))
                mark_retry(filepath, page_number, reason)
                log.error("   ✗ Trang %s lỗi: %s", page_number, reason)

    if failures and not force:
        stats["errors"] = len(failures)
        log.warning("   Chưa ghi DB vì còn %s trang lỗi. Chạy lại để retry các trang này.", len(failures))
        return stats

    doc_id = "dry-run" if dry_run else "pending"
    question_rows: list[dict[str, Any]] = []
    theory_rows: list[dict[str, Any]] = []
    for page_number in sorted(page_results):
        for item_index, item in enumerate(page_results[page_number].get("items", []), start=1):
            if item.get("type") in {"theory", "example"}:
                theory_rows.append(theory_row(item, doc_id))
            else:
                image_url = None
                if item.get("needs_visual") and not dry_run:
                    try:
                        image_url = upload_question_visual(
                            supabase,
                            filepath,
                            digest,
                            page_number,
                            item_index,
                            item.get("visual_bbox"),
                            item.get("source_hint"),
                        )
                    except Exception as exc:
                        log.warning("   ⚠️ Upload ảnh trang %s mục %s lỗi: %s", page_number, item_index, exc)
                        item["needs_visual"] = True
                        item["visual_upload_error"] = str(exc)
                question_rows.append(question_row(item, doc_id, filename, page_number, image_url))
                if max_questions > 0 and len(question_rows) >= max_questions:
                    break
        if max_questions > 0 and len(question_rows) >= max_questions:
            break

    if not question_rows and not theory_rows and failures:
        stats["errors"] = len(failures)
        log.warning("   Bỏ qua ghi DB vì không trích xuất được câu/LT nào và có %s trang lỗi.", len(failures))
        return stats

    if dry_run:
        doc_id = "dry-run"
    else:
        doc_record = supabase.table("raw_documents").insert(
            {
                "filename": filename,
                "source": source,
                "status": "processing",
                "total_pages": len(payloads),
            }
        ).execute()
        doc_id = doc_record.data[0]["id"]

    if doc_id != "dry-run":
        for row in question_rows:
            row["raw_document_id"] = doc_id
        for row in theory_rows:
            row["raw_document_id"] = doc_id

    try:
        if dry_run:
            preview_path = Path(f"ingest_dry_run_{Path(filepath).stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
            preview_path.write_text(
                json.dumps(
                    {
                        "filename": filename,
                        "source": source,
                        "sha1": digest,
                        "questions": question_rows,
                        "theory": theory_rows,
                        "failures": failures,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            log.info("   DRY RUN preview JSON: %s", preview_path.resolve())
        else:
            question_rows = filter_duplicate_question_rows(supabase, question_rows, dry_run=False)
            insert_batches(supabase, "questions", question_rows)
            insert_batches(supabase, "theory_content", theory_rows)
            final_status = "completed_with_errors" if failures else "completed"
            supabase.table("raw_documents").update({"status": final_status}).eq("id", doc_id).execute()
    except Exception:
        if not dry_run:
            supabase.table("raw_documents").update({"status": "failed"}).eq("id", doc_id).execute()
        raise

    stats["questions"] = len(question_rows)
    stats["theory"] = len(theory_rows)
    stats["errors"] = len(failures)
    log.info(
        "   ✅ Xong: %s câu, %s LT/VD, %s lỗi, %s trang cache",
        stats["questions"],
        stats["theory"],
        stats["errors"],
        stats["pages_from_cache"],
    )
    return stats


def iter_pdfs(folder: str, only_file: str | None = None) -> list[Path]:
    if only_file:
        path = Path(only_file)
        return [path] if path.exists() else []
    return sorted(Path(folder).rglob("*.pdf"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest PDF Toán vào Supabase bằng Claude text+vision.")
    parser.add_argument("--folder", default=DATABASE_FOLDER, help="Thư mục chứa PDF.")
    parser.add_argument("--file", help="Chỉ xử lý một file PDF cụ thể.")
    parser.add_argument("--force", action="store_true", help="Ghi DB cả khi còn trang lỗi.")
    parser.add_argument("--limit", type=int, default=0, help="Giới hạn số file xử lý trong lần chạy.")
    parser.add_argument("--reprocess", action="store_true", help="Bỏ qua processed.log và xử lý lại.")
    parser.add_argument("--dry-run", action="store_true", help="Trich xuat va ghi file preview JSON, khong ghi Supabase.")
    parser.add_argument("--max-questions", type=int, default=0, help="Gioi han so cau hoi ghi cho moi PDF.")
    parser.add_argument("--max-pages", type=int, default=0, help="Gioi han so trang doc cho moi PDF.")
    parser.add_argument("--page-from", type=int, default=0, help="Chi doc tu trang nay tro di.")
    parser.add_argument("--page-to", type=int, default=0, help="Chi doc den trang nay.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    log.info("=" * 60)
    log.info(
        "🚀 BẮT ĐẦU PIPELINE INGESTION (fast=%s, vision=%s, explanation_mode=%s)",
        CLAUDE_FAST_MODEL if AUTO_SELECT_MODEL else CLAUDE_MODEL,
        CLAUDE_VISION_MODEL if AUTO_SELECT_MODEL else CLAUDE_MODEL,
        INGEST_EXPLANATION_MODE,
    )
    log.info("📁 Thư mục: %s", args.folder)
    log.info(
        "⚙️ workers=%s, render_zoom=%s, image_all=%s, smart_image=%s, upload_visuals=%s",
        MAX_PAGE_WORKERS,
        RENDER_ZOOM,
        SEND_IMAGE_FOR_ALL_PAGES,
        SMART_IMAGE_PAGES,
        UPLOAD_VISUALS,
    )
    log.info("=" * 60)

    base_url = normalize_anthropic_base_url(CLAUDE_BASE_URL)
    log.info("Router endpoint: %s", base_url)
    claude_client = anthropic.Anthropic(api_key=CLAUDE_API_KEY or "local-9router", base_url=base_url)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    processed = set() if args.reprocess else load_processed()
    all_pdfs = iter_pdfs(args.folder, args.file)
    remaining = [path for path in all_pdfs if str(path) not in processed]
    if args.limit > 0:
        remaining = remaining[: args.limit]

    log.info("📚 Tổng PDF: %s | Còn lại: %s | Đã xử lý: %s", len(all_pdfs), len(remaining), len(processed))
    if not remaining:
        log.info("✅ Không còn file cần xử lý.")
        return

    total_stats = {"questions": 0, "theory": 0, "errors": 0, "pages": 0, "pages_from_cache": 0}
    for index, pdf_path in enumerate(remaining, start=1):
        log.info("[%s/%s] %s", index, len(remaining), pdf_path.name)
        try:
            stats = process_pdf(
                str(pdf_path),
                supabase,
                claude_client,
                force=args.force,
                dry_run=args.dry_run,
                max_questions=args.max_questions,
                max_pages=args.max_pages,
                page_from=args.page_from,
                page_to=args.page_to,
            )
            for key in total_stats:
                total_stats[key] += stats.get(key, 0)

            if stats.get("errors", 0) == 0 and not args.dry_run:
                mark_processed(str(pdf_path))
            else:
                mark_error(str(pdf_path), f"{stats['errors']} page errors; not marked processed")
        except KeyboardInterrupt:
            log.info("Dừng bởi người dùng. Tiến độ cache trang đã được lưu.")
            break
        except Exception as exc:
            log.exception("❌ Lỗi file %s: %s", pdf_path.name, exc)
            mark_error(str(pdf_path), str(exc))

    log.info("=" * 60)
    log.info("🎉 HOÀN THÀNH LẦN CHẠY")
    log.info("   Câu hỏi:      %s", total_stats["questions"])
    log.info("   Lý thuyết/VD: %s", total_stats["theory"])
    log.info("   Trang:        %s", total_stats["pages"])
    log.info("   Từ cache:     %s", total_stats["pages_from_cache"])
    log.info("   Lỗi:          %s", total_stats["errors"])
    log.info("=" * 60)


if __name__ == "__main__":
    main()
