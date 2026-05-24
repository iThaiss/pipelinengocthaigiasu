import argparse
import ast
import hashlib
import json
import logging
import os
import re
import sys
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
import fitz
from supabase import Client, create_client

import ingest_pipeline as base_ingest
import standard_exam_preview_html
from ingest_pipeline import (
    AUTO_SELECT_MODEL,
    CLAUDE_API_KEY,
    CLAUDE_BASE_URL,
    CLAUDE_FAST_MODEL,
    CLAUDE_MODEL,
    CLAUDE_VISION_MODEL,
    FALLBACK_RENDER_ZOOMS,
    PagePayload,
    RETRY_COUNT,
    RENDER_ZOOM,
    SUPABASE_KEY as DEFAULT_SUPABASE_KEY,
    SUPABASE_URL as DEFAULT_SUPABASE_URL,
    file_sha1,
    get_response_text,
    normalize_anthropic_base_url,
    normalize_bbox,
    normalize_bool,
    normalize_item,
    normalize_optional_text,
    normalize_visual_table,
    render_page_from_file_png_base64,
    select_model,
    upload_question_visual,
)
from solve_answers import build_update, has_answerable_content, solve_with_claude


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")


SUPABASE_URL = os.getenv("SUPABASE_URL", DEFAULT_SUPABASE_URL)
SUPABASE_KEY = os.getenv("SUPABASE_KEY", DEFAULT_SUPABASE_KEY)

LOG_FILE = os.getenv("STANDARD_EXAM_LOG_FILE", "logs/standard_exam_pipeline.log")
RUN_DIR = Path(os.getenv("STANDARD_EXAM_RUN_DIR", "artifacts/standard_exam_runs"))
PREVIEW_DIR = Path(os.getenv("STANDARD_EXAM_PREVIEW_DIR", "artifacts/standard_exam_previews"))
CACHE_DIR = Path(os.getenv("STANDARD_EXAM_CACHE_DIR", ".ingest_cache/standard_exam"))
TAXONOMY_PATH = Path(os.getenv("STANDARD_EXAM_TAXONOMY_PATH", "local_curriculum/output_json/taxonomy_v2.json"))
ENRICH_RETRY_COUNT = int(os.getenv("STANDARD_EXAM_ENRICH_RETRY_COUNT", str(max(RETRY_COUNT, 5))))
ENRICH_DELAY_SECONDS = float(os.getenv("STANDARD_EXAM_ENRICH_DELAY_SECONDS", "2.0"))
DEFAULT_FALLBACK_MODELS = "gwai/claude-sonnet-4-6"
FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv("STANDARD_EXAM_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS).split(",")
    if model.strip()
]

SCORING = {
    "part_1": {"count": 12, "per_question": 0.25, "max_score": 3.0},
    "part_2": {
        "count": 4,
        "per_question_max": 1.0,
        "score_by_correct_statements": {"0": 0, "1": 0.1, "2": 0.25, "3": 0.5, "4": 1.0},
        "max_score": 4.0,
    },
    "part_3": {"count": 6, "per_question": 0.5, "max_score": 3.0},
}

SECTION_DEFS = [
    {
        "section_code": "part_1",
        "title": "Phan I. Trac nghiem nhieu lua chon",
        "question_type": "multiple_choice",
        "section_order": 1,
        "expected_count": 12,
        "max_score": 3.0,
        "scoring_rule": {"type": "single_choice", "per_question": 0.25},
    },
    {
        "section_code": "part_2",
        "title": "Phan II. Trac nghiem dung sai",
        "question_type": "true_false",
        "section_order": 2,
        "expected_count": 4,
        "max_score": 4.0,
        "scoring_rule": {
            "type": "true_false_partial",
            "per_question_max": 1.0,
            "score_by_correct_statements": SCORING["part_2"]["score_by_correct_statements"],
        },
    },
    {
        "section_code": "part_3",
        "title": "Phan III. Tra loi ngan",
        "question_type": "short_answer",
        "section_order": 3,
        "expected_count": 6,
        "max_score": 3.0,
        "scoring_rule": {"type": "short_answer", "per_question": 0.5},
    },
]

TAXONOMY: dict[str, Any] | None = None


def load_taxonomy() -> dict[str, Any]:
    global TAXONOMY
    if TAXONOMY is not None:
        return TAXONOMY
    data = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    topics = {int(item["id"]): item for item in data.get("topics", [])}
    subtopics = data.get("subtopics", [])
    for subtopic in subtopics:
        topic = topics.get(int(subtopic["topic_id"]))
        subtopic["topic_title"] = topic.get("topic_title") if topic else None
        subtopic["topic_code"] = topic.get("topic_code") if topic else None
    TAXONOMY = {"topics": topics, "subtopics": subtopics}
    return TAXONOMY


def taxonomy_prompt_catalog() -> str:
    taxonomy = load_taxonomy()
    lines = []
    for subtopic in taxonomy["subtopics"]:
        lines.append(
            f'{subtopic["id"]} | {subtopic["subtopic_code"]} | {subtopic.get("topic_title")} | {subtopic.get("display_title") or subtopic.get("subtopic_title")}'
        )
    return "\n".join(lines)

SYSTEM_PROMPT = """Ban la chuyen gia trich xuat de thi Toan tot nghiep THPT Viet Nam.
Nhiem vu: doc tung trang PDF bang text va/hoac anh, trich xuat cau hoi theo dung cau truc de thi tot nghiep.
Chi tra ve JSON hop le. Khong markdown. Khong bo sot cau hoi vi watermark, header, footer, hinh ve, bang so lieu hoac cong thuc."""

USER_INSTRUCTIONS = """
Trich xuat tat ca cau hoi tren trang PDF thuoc de thi tot nghiep THPT mon Toan.

Cau truc can giu nguyen:
- part_1: Phan I, 12 cau trac nghiem nhieu lua chon A/B/C/D.
- part_2: Phan II, 4 cau dung/sai; moi cau co dung 4 y a/b/c/d.
- part_3: Phan III, 6 cau tra loi ngan.

Quy tac:
1. Phai dien section_code la part_1, part_2 hoac part_3.
2. Phai dien question_number la so cau trong phan hien tai.
3. Dung/Sai khong tach thanh 4 cau rieng; giu 4 y trong statements.
4. Neu trang co dap an/loi giai san, trich vao correct_answer/numeric_answer/statements.answer va explanation.
5. Neu khong co dap an/loi giai tren trang, de dap an null; pipeline se tu giai sau.
6. Chuan hoa cong thuc bang LaTeX inline $...$ khi co the.
7. Neu cau can hinh/bang/do thi, dat needs_visual=true va tra visual_bbox theo toa do chuan hoa [x_min,y_min,x_max,y_max].
8. question_text chi duoc chua de bai goc cua cau hoi. Tuyet doi khong dua cac phan sau vao question_text:
   - watermark/header/footer/quang cao nhu Shared By, Fanpage, PAGE, Classin, UniMap, SSLive, sstudy.vn.
   - Loi giai, dap an, huong dan giai, "Tra loi", bang dap an.
   - Text cua cau truoc/cau sau neu PDF extract bi sai thu tu.
9. Neu cau co loi giai trong file, dua loi giai vao explanation, khong tron vao question_text.
10. Neu PDF text co ky tu OCR loi nhu , , e, ??, hay cong thuc vo nghia, hay uu tien doc tu anh trang de viet lai cong thuc LaTeX sach.
11. Chi tra ve JSON theo schema:

{
  "items": [
    {
      "section_code": "part_1 | part_2 | part_3",
      "question_number": 1,
      "type": "multiple_choice | true_false | short_answer",
      "topic": "string | null",
      "subtopic": "string | null",
      "chapter": "string | null",
      "difficulty": "Nhan biet | Thong hieu | Van dung | Van dung cao | null",
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
          {"label": "x", "cells": ["-infty", "0", "+infty"]}
        ]
      },
      "explanation": "string | null",
      "source_hint": "string | null"
    }
  ]
}
"""


Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


@dataclass
class ExamRange:
    exam_index: int
    title: str
    source_id: str | None
    start_page: int
    end_page: int


def extract_json_object(raw_text: str) -> dict[str, Any]:
    raw_text = re.sub(r"```(?:json)?|```", "", raw_text or "").strip()
    if raw_text.startswith("<user_info>") or raw_text.startswith("<environment_context>"):
        raise ValueError("Router/model returned context XML instead of JSON")
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw_text):
        try:
            data, _ = decoder.raw_decode(raw_text[match.start() :].strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if match:
        candidate = match.group(0)
        for json_str in repaired_json_candidates(candidate):
            try:
                data = json.loads(json_str)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
        try:
            data = ast.literal_eval(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    raise ValueError(f"Cannot parse JSON response: {raw_text[:300]!r}")


def repaired_json_candidates(candidate: str) -> list[str]:
    candidate = candidate.strip()
    base = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r"\\\\", candidate)
    key_quoted = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', base)
    py_literals = (
        key_quoted.replace(": True", ": true")
        .replace(": False", ": false")
        .replace(": None", ": null")
    )
    no_trailing_commas = re.sub(r",\s*([}\]])", r"\1", py_literals)
    return [candidate, base, key_quoted, py_literals, no_trailing_commas]


def router_response_text(response: Any) -> str:
    try:
        return get_response_text(response)
    except Exception:
        pass
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if choices:
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
    raise ValueError("Router response has no readable text content")


def cache_path(file_hash: str, page_number: int) -> Path:
    return CACHE_DIR / file_hash / f"page_{page_number:04d}.json"


def load_cache(file_hash: str, page_number: int) -> dict[str, Any] | None:
    path = cache_path(file_hash, page_number)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_cache(file_hash: str, page_number: int, data: dict[str, Any]) -> None:
    path = cache_path(file_hash, page_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def is_usage_or_quota_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "quota",
            "usage",
            "limit",
            "rate_limit",
            "rate limit",
            "too many requests",
            "429",
            "insufficient",
            "exceeded",
        )
    )


def is_retryable_router_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return is_usage_or_quota_error(exc) or "context xml" in message or "instead of json" in message


def create_message_with_model_fallback(client: anthropic.Anthropic, primary_model: str, **kwargs: Any) -> Any:
    models = [primary_model, *[model for model in FALLBACK_MODELS if model != primary_model]]
    last_error: Exception | None = None
    for index, model in enumerate(models):
        try:
            if index > 0:
                log.warning("Primary model hit usage/quota; falling back to %s", model)
            response = client.messages.create(model=model, **kwargs)
            text = router_response_text(response).lstrip()
            if text.startswith("<user_info>") or text.startswith("<environment_context>"):
                raise ValueError("Router/model returned context XML instead of JSON")
            return response
        except Exception as exc:
            last_error = exc
            if index == len(models) - 1 or not is_retryable_router_error(exc):
                raise
    raise RuntimeError(f"All models failed: {last_error}")


def fold_text(value: Any) -> str:
    import unicodedata

    text = str(value or "").casefold()
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn").replace("đ", "d")


INTERNAL_SOLUTION_AUDIT_RE = re.compile(
    r"(?is)(?:\n\s*\n|^)\s*(?:\*\*)?"
    r"(?:Đối chiếu|Doi chieu|Kiểm tra lại|Kiem tra lai|Lưu ý|Luu y|Nguồn gốc|Nguon goc|Có thể nguồn gốc|Co the nguon goc)"
    r".*?(?=\n\s*\n|$)"
)


def strip_internal_solution_audit(value: Any) -> str | None:
    text = normalize_optional_text(value)
    if not text:
        return None
    previous = None
    while previous != text:
        previous = text
        text = INTERNAL_SOLUTION_AUDIT_RE.sub("\n\n", text)
    lines = []
    for line in text.splitlines():
        folded = fold_text(line)
        if any(
            marker in folded
            for marker in [
                "doi chieu dap an",
                "doi chieu nguon",
                "dap an pdf",
                "dap an goc",
                "nguon goc cho dap an",
                "nguon goc co the",
                "co the nguon goc",
                "trung voi ket qua tu giai",
                "trung khop",
                "source_answer",
            ]
        ):
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def clean_page_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text or "")
    text = re.sub(r"\n{4,}", "\n\n", text)
    return text.strip()


def find_exam_ranges(filepath: str) -> list[ExamRange]:
    pattern = re.compile(r"ĐỀ\s+MINH\s+H[ỌO]A\s+(\d{1,2})", re.IGNORECASE)
    ranges: list[ExamRange] = []
    with fitz.open(filepath) as pdf:
        for page_number, exam_index, title, source_id in [
            (19, -1, "De tham khao tot nghiep THPT 2025 mon Toan", "68161"),
            (26, 0, "De chinh thuc tot nghiep THPT 2025 mon Toan", "68163"),
        ]:
            if page_number <= pdf.page_count:
                text = pdf[page_number - 1].get_text("text") or ""
                folded = fold_text(text)
                if "ky thi tot nghiep" in folded and "phan i" in folded:
                    ranges.append(
                        ExamRange(
                            exam_index=exam_index,
                            title=title,
                            source_id=source_id,
                            start_page=page_number,
                            end_page=min(pdf.page_count, page_number + 4),
                        )
                    )
        for page_index, page in enumerate(pdf):
            text = page.get_text("text") or ""
            upper_text = text.upper()
            folded = fold_text(text)
            if "ĐỀ MINH HỌA" not in upper_text:
                continue
            if not (
                "ky thi tot nghiep" in folded
                and "bai thi" in folded
                and ("phan i" in folded or "thi sinh tra loi tu cau 1 den cau 12" in folded)
            ):
                continue
            match = pattern.search(text)
            if not match:
                continue
            exam_index = int(match.group(1))
            source_id_match = re.search(r"\[(\d{5,})\]", text)
            ranges.append(
                ExamRange(
                    exam_index=exam_index,
                    title=f"De minh hoa {exam_index:02d} thi TN THPT 2026 mon Toan",
                    source_id=source_id_match.group(1) if source_id_match else None,
                    start_page=page_index + 1,
                    end_page=pdf.page_count,
                )
            )
    ranges.sort(key=lambda item: item.start_page)
    deduped: list[ExamRange] = []
    seen: set[tuple[int, int]] = set()
    for item in ranges:
        key = (item.exam_index, item.start_page)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    for index, item in enumerate(deduped):
        if index + 1 < len(deduped):
            item.end_page = deduped[index + 1].start_page - 1
        if item.exam_index == -1:
            item.end_page = min(item.end_page, 23)
        elif item.exam_index == 0:
            item.end_page = min(item.end_page, 30)
    moon_ranges = [item for item in deduped if item.exam_index >= 1]
    prefix_ranges = [item for item in deduped if item.exam_index < 1]
    if len(moon_ranges) >= 2 and len(moon_ranges) < 60:
        first = moon_ranges[0]
        step = moon_ranges[1].start_page - moon_ranges[0].start_page
        if 3 <= step <= 8 and first.exam_index == 1:
            with fitz.open(filepath) as pdf:
                page_count = pdf.page_count
            by_index = {item.exam_index: item for item in moon_ranges}
            estimated: list[ExamRange] = list(prefix_ranges)
            for exam_index in range(1, 61):
                if exam_index in by_index:
                    item = by_index[exam_index]
                    item.end_page = min(page_count, item.start_page + step - 1)
                    estimated.append(item)
                    continue
                start_page = first.start_page + (exam_index - 1) * step
                if start_page > page_count:
                    break
                estimated.append(
                    ExamRange(
                        exam_index=exam_index,
                        title=f"De minh hoa {exam_index:02d} thi TN THPT 2026 mon Toan",
                        source_id=f"681{exam_index:02d}",
                        start_page=start_page,
                        end_page=min(page_count, start_page + step - 1),
                    )
                )
            return estimated
    return deduped


def resolve_exam_range(filepath: str, exam_index: int, page_from: int = 0, page_to: int = 0) -> ExamRange:
    if page_from > 0:
        with fitz.open(filepath) as pdf:
            end_page = page_to if page_to > 0 else min(pdf.page_count, page_from + 4)
        return ExamRange(
            exam_index=exam_index,
            title=f"De minh hoa {exam_index:02d} thi TN THPT 2026 mon Toan",
            source_id=None,
            start_page=page_from,
            end_page=end_page,
        )

    ranges = find_exam_ranges(filepath)
    for item in ranges:
        if item.exam_index == exam_index:
            return item
    raise ValueError(f"Cannot find exam_index={exam_index} in {filepath}")


def filename_exam_index(path: Path, fallback: int) -> int:
    match = re.search(r"(?:de|đề|so|số)\s*(?:so|số)?\s*(\d{1,3})", path.stem, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d{1,3})", path.stem)
    return int(match.group(1)) if match else fallback


def single_pdf_exam_range(filepath: str, exam_index: int | None = None) -> ExamRange:
    path = Path(filepath)
    with fitz.open(filepath) as pdf:
        page_count = pdf.page_count
    resolved_index = exam_index if exam_index is not None else filename_exam_index(path, 1)
    return ExamRange(
        exam_index=resolved_index,
        title=path.stem,
        source_id=None,
        start_page=1,
        end_page=page_count,
    )


def build_exam_payloads(filepath: str, exam_range: ExamRange, force_vision: bool) -> list[PagePayload]:
    payloads: list[PagePayload] = []
    with fitz.open(filepath) as pdf:
        start = max(1, exam_range.start_page)
        end = min(pdf.page_count, exam_range.end_page)
        for page_number in range(start, end + 1):
            page = pdf[page_number - 1]
            text = clean_page_text(page.get_text("text") or "")
            payloads.append(
                PagePayload(
                    filepath=filepath,
                    page_index=page_number - 1,
                    page_number=page_number,
                    text=text,
                    used_image=force_vision,
                )
            )
    return payloads


def text_needs_visual(text: str) -> bool:
    folded = fold_text(text)
    return any(keyword in folded for keyword in ["hinh ve", "bang bien thien", "do thi", "bang so lieu", "tham khao hinh"])


def split_labeled_parts(text: str, labels: str) -> tuple[str, dict[str, str]]:
    pattern = re.compile(rf"(?<!\w)([{labels}])[\.\)]\s+", re.IGNORECASE)
    matches = list(pattern.finditer(text))
    if not matches:
        return text.strip(), {}
    stem = text[: matches[0].start()].strip()
    parts: dict[str, str] = {}
    for index, match in enumerate(matches):
        label = match.group(1).lower()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[start:end].strip()
        if value:
            parts[label] = value
    return stem, parts


FALLBACK_TEXT_STOP_PATTERN = re.compile(
    r"(?i)(?:"
    r"\bloi\s*giai\b|\blời\s*giải\b|"
    r"\bhuong\s*dan\s*giai\b|\bhướng\s*dẫn\s*giải\b|"
    r"\btra\s*loi\s*:|\btrả\s*lời\s*:|"
    r"\bdap\s*an\s*:|\bđáp\s*án\s*:|"
    r"\bshared\s+by\b|\bfanpage\s*:|\[\[page\s+\d+\]\]|"
    r"\bkhoa\s*hoc\s*sslive\b|\bkhóa\s*học\s*sslive\b|"
    r"\bdang\s*ky\s*khoa\s*hoc\b|\bđăng\s*ký\s*khóa\s*học\b|"
    r"\bxem\s*lai\s*bai\s*giang\b|\bxem\s*lại\s*bài\s*giảng\b|"
    r"\bclassin\b|\bssstudy\.vn\b"
    r")"
)


def clean_fallback_question_text(text: str) -> str:
    """Native PDF text is a last-resort filler; strip solution/footer bleed early."""
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return ""
    match = FALLBACK_TEXT_STOP_PATTERN.search(cleaned)
    if match:
        cleaned = cleaned[: match.start()].strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" :-")
    return cleaned


def parse_question_chunk(section_code: str, question_number: int, chunk: str, page_number: int) -> dict[str, Any] | None:
    chunk = clean_fallback_question_text(chunk)
    if not chunk:
        return None
    item: dict[str, Any] = {
        "section_code": section_code,
        "question_number": question_number,
        "question_text": chunk,
        "needs_visual": text_needs_visual(chunk),
        "visual_type": "khac" if text_needs_visual(chunk) else None,
        "source_hint": f"{section_code} - Cau {question_number}",
    }
    if section_code == "part_1":
        stem, options = split_labeled_parts(chunk, "ABCD")
        item.update(
            {
                "type": "multiple_choice",
                "question_text": stem,
                "option_a": options.get("a"),
                "option_b": options.get("b"),
                "option_c": options.get("c"),
                "option_d": options.get("d"),
            }
        )
    elif section_code == "part_2":
        stem, statements = split_labeled_parts(chunk, "abcd")
        item.update(
            {
                "type": "true_false",
                "question_text": stem,
                "statements": [
                    {"label": label, "text": statements.get(label), "answer": None}
                    for label in ("a", "b", "c", "d")
                    if statements.get(label)
                ],
            }
        )
    else:
        item.update({"type": "short_answer"})
    normalized = normalize_exam_item(item, page_number, 1)
    if normalized:
        normalized["parser_source"] = "native_text_fallback"
    return normalized


def parse_exam_text_fallback(filepath: str, exam_range: ExamRange) -> list[dict[str, Any]]:
    page_texts: list[tuple[int, str]] = []
    with fitz.open(filepath) as pdf:
        for page_number in range(exam_range.start_page, exam_range.end_page + 1):
            page_texts.append((page_number, clean_page_text(pdf[page_number - 1].get_text("text") or "")))

    combined_parts: list[str] = []
    page_positions: list[tuple[int, int]] = []
    for page_number, text in page_texts:
        page_positions.append((len("".join(combined_parts)), page_number))
        combined_parts.append(f"\n\n[[PAGE {page_number}]]\n{text}")
    combined = "".join(combined_parts)

    part_pattern = re.compile(r"PHẦN\s+(I{1,3})\.", re.IGNORECASE)
    part_matches = list(part_pattern.finditer(combined))
    if not part_matches:
        return []

    def page_for_position(position: int) -> int:
        current = exam_range.start_page
        for marker_position, page_number in page_positions:
            if marker_position <= position:
                current = page_number
            else:
                break
        return current

    section_by_roman = {"I": "part_1", "II": "part_2", "III": "part_3"}
    items: list[dict[str, Any]] = []
    for part_index, part_match in enumerate(part_matches):
        roman = part_match.group(1).upper()
        section_code = section_by_roman.get(roman)
        if not section_code:
            continue
        start = part_match.end()
        end = part_matches[part_index + 1].start() if part_index + 1 < len(part_matches) else len(combined)
        section_text = combined[start:end]
        question_pattern = re.compile(r"Câu\s+(\d{1,2})\s*(?:\[[^\]]+\])?\s*:", re.IGNORECASE)
        question_matches = list(question_pattern.finditer(section_text))
        for index, question_match in enumerate(question_matches):
            question_number = int(question_match.group(1))
            chunk_start = question_match.end()
            chunk_end = question_matches[index + 1].start() if index + 1 < len(question_matches) else len(section_text)
            chunk = section_text[chunk_start:chunk_end]
            absolute_position = start + question_match.start()
            parsed = parse_question_chunk(section_code, question_number, chunk, page_for_position(absolute_position))
            if parsed:
                parsed["source_hint"] = f"Fallback text {section_code} - Cau {question_number}"
                items.append(parsed)
    return items


def normalize_section_code(value: Any, question_type: str | None = None) -> str | None:
    text = fold_text(value)
    if text in {"part_1", "phan_i", "i", "1"} or "phan i" in text:
        return "part_1"
    if text in {"part_2", "phan_ii", "ii", "2"} or "phan ii" in text:
        return "part_2"
    if text in {"part_3", "phan_iii", "iii", "3"} or "phan iii" in text:
        return "part_3"
    if question_type == "multiple_choice":
        return "part_1"
    if question_type == "true_false":
        return "part_2"
    if question_type == "short_answer":
        return "part_3"
    return None


def infer_question_number(item: dict[str, Any]) -> int | None:
    raw = item.get("question_number")
    if isinstance(raw, int):
        return raw
    if raw is not None:
        match = re.search(r"\d+", str(raw))
        if match:
            return int(match.group(0))
    for key in ("source_hint", "question_text"):
        value = item.get(key)
        if not value:
            continue
        match = re.search(r"c[âa]u\s*(\d{1,2})", fold_text(value), re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def normalize_exam_item(item: dict[str, Any], page_number: int, item_index: int) -> dict[str, Any] | None:
    normalized = normalize_item(item, page_number, item_index)
    if not normalized:
        return None
    q_type = normalized.get("type")
    section_code = normalize_section_code(item.get("section_code") or item.get("part"), q_type)
    question_number = infer_question_number(item)
    if not section_code or not question_number:
        return None
    expected_type = {
        "part_1": "multiple_choice",
        "part_2": "true_false",
        "part_3": "short_answer",
    }[section_code]
    if q_type != expected_type:
        normalized["type"] = expected_type
    normalized["section_code"] = section_code
    normalized["question_number"] = question_number
    normalized["page_number"] = page_number
    normalized["source_hint"] = normalized.get("source_hint") or f"{section_code} cau {question_number}"
    normalized["visual_table"] = normalize_visual_table(item.get("visual_table"))
    normalized["visual_bbox"] = normalize_bbox(item.get("visual_bbox"))
    return normalized


def normalize_page_result(data: dict[str, Any], page_number: int) -> dict[str, Any]:
    items = data.get("items")
    if not isinstance(items, list):
        items = []
    normalized_items = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            normalized = normalize_exam_item(item, page_number, index)
            if normalized:
                normalized["parser_source"] = "model_page_parse"
                normalized_items.append(normalized)
    return {"items": normalized_items}


def parse_page_with_claude(client: anthropic.Anthropic, payload: PagePayload) -> dict[str, Any]:
    text_block = payload.text if payload.text else "[No text extracted; OCR from page image.]"
    user_text = (
        f"{USER_INSTRUCTIONS}\n\n"
        f"Trang PDF so {payload.page_number}.\n"
        f"Text extract tu PDF co the sai thu tu hoac thieu cong thuc:\n"
        f"<pdf_text>\n{text_block}\n</pdf_text>"
    )

    def build_content(zoom: float | None) -> list[dict[str, Any]] | str:
        if zoom is None:
            return user_text
        image_b64 = render_page_from_file_png_base64(payload.filepath, payload.page_index, zoom=zoom)
        return [
            {"type": "text", "text": user_text},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
        ]

    zoom_plan: list[float | None] = [RENDER_ZOOM, *FALLBACK_RENDER_ZOOMS, None] if payload.used_image else [None]
    last_error: Exception | None = None
    max_attempts = max(RETRY_COUNT, len(zoom_plan))
    for attempt in range(1, max_attempts + 1):
        zoom = zoom_plan[min(attempt - 1, len(zoom_plan) - 1)]
        try:
            response = create_message_with_model_fallback(
                client,
                primary_model=select_model(payload, zoom),
                max_tokens=8000,
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_content(zoom)}],
                extra_headers={"User-Agent": "curl/8.7.1"},
            )
            return normalize_page_result(extract_json_object(router_response_text(response)), payload.page_number)
        except Exception as exc:
            last_error = exc
            log.warning("Page %s failed attempt %s/%s: %s", payload.page_number, attempt, max_attempts, exc)
            if attempt < max_attempts:
                time.sleep(min(30, 2**attempt))
    raise RuntimeError(f"Page {payload.page_number} failed after {max_attempts} attempts: {last_error}")


def parse_page_cached(client: anthropic.Anthropic, file_hash: str, payload: PagePayload, use_cache: bool) -> tuple[int, dict[str, Any], bool]:
    if use_cache:
        cached = load_cache(file_hash, payload.page_number)
        if cached is not None:
            return payload.page_number, normalize_page_result(cached, payload.page_number), True
    result = parse_page_with_claude(client, payload)
    save_cache(file_hash, payload.page_number, result)
    return payload.page_number, result, False


def question_signature(item: dict[str, Any]) -> str:
    parts = [
        item.get("type"),
        item.get("question_text"),
        item.get("option_a"),
        item.get("option_b"),
        item.get("option_c"),
        item.get("option_d"),
        json.dumps(item.get("statements") or [], ensure_ascii=False, sort_keys=True),
    ]
    text = re.sub(r"\s+", " ", "\n".join(str(part or "") for part in parts)).strip().lower()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def max_score_for(section_code: str) -> float:
    if section_code == "part_1":
        return 0.25
    if section_code == "part_2":
        return 1.0
    if section_code == "part_3":
        return 0.5
    return 0.0


def display_order_for(section_code: str, question_number: int) -> int:
    offsets = {"part_1": 0, "part_2": 12, "part_3": 16}
    return offsets.get(section_code, 1000) + question_number


def question_has_answer(question: dict[str, Any]) -> bool:
    q_type = question.get("question_type")
    if q_type == "multiple_choice":
        return question.get("correct_answer") in {"A", "B", "C", "D"}
    if q_type == "true_false":
        statements = question.get("statements")
        return isinstance(statements, list) and len(statements) == 4 and all(isinstance(stmt.get("answer"), bool) for stmt in statements if isinstance(stmt, dict))
    if q_type == "short_answer":
        return question.get("numeric_answer") not in (None, "")
    return False


def answer_text_for(question: dict[str, Any]) -> str | None:
    q_type = question.get("question_type")
    if q_type == "multiple_choice":
        answer = question.get("correct_answer")
        return str(answer) if answer in {"A", "B", "C", "D"} else None
    if q_type == "true_false":
        statements = question.get("statements")
        if not isinstance(statements, list) or len(statements) != 4:
            return None
        chars = []
        for statement in statements:
            if not isinstance(statement, dict) or not isinstance(statement.get("answer"), bool):
                return None
            chars.append("D" if statement.get("answer") else "S")
        return "".join(chars)
    if q_type == "short_answer":
        answer = question.get("numeric_answer")
        return None if answer in (None, "") else str(answer)
    return None


def source_solution_text(question: dict[str, Any]) -> str | None:
    raw = question.get("raw_text") if isinstance(question.get("raw_text"), dict) else {}
    pieces = []
    for key in ("explanation", "source_solution", "solution_text"):
        value = normalize_optional_text(raw.get(key))
        if value:
            pieces.append(value)
    raw_question = normalize_optional_text(raw.get("question_text"))
    if raw_question:
        folded = fold_text(raw_question)
        markers = [marker for marker in ("loi giai", "huong dan giai", "tra loi", "dap an") if marker in folded]
        if markers:
            pieces.append(raw_question)
    if not pieces:
        return None
    text = "\n\n".join(dict.fromkeys(pieces))
    return text[:5000]


def source_answer_is_primary(question: dict[str, Any]) -> bool:
    return question.get("answer_source") in {"source_extracted", "manual_reviewed"} or bool(source_solution_text(question))


def normalize_short_answer_value(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?", text)
    if not match:
        return None
    candidate = match.group(0)
    if "/" in candidate:
        try:
            numerator, denominator = candidate.split("/", 1)
            denominator_number = float(denominator)
            if denominator_number:
                number = float(numerator) / denominator_number
                if number.is_integer():
                    return str(int(number))
                return f"{number:.10f}".rstrip("0").rstrip(".")
        except ValueError:
            return None
        return None
    try:
        number = float(candidate)
    except ValueError:
        return candidate
    if number.is_integer():
        return str(int(number))
    return f"{number:.10f}".rstrip("0").rstrip(".")


def scan_item_has_source_solution(item: dict[str, Any]) -> bool:
    """Return True only when the PDF page appears to contain a real answer/solution."""
    if normalize_optional_text(item.get("explanation")):
        return True
    hint = fold_text(item.get("source_hint"))
    return any(
        marker in hint
        for marker in (
            "dap an",
            "loi giai",
            "huong dan giai",
            "bang dap an",
            "answer key",
            "solution",
        )
    )


def strip_scan_answers(item: dict[str, Any]) -> dict[str, Any]:
    """Keep scan as extraction-only unless the source page includes answers/solutions."""
    cleaned = dict(item)
    cleaned["correct_answer"] = None
    cleaned["numeric_answer"] = None
    cleaned["explanation"] = None
    if isinstance(cleaned.get("statements"), list):
        statements = []
        for statement in cleaned["statements"]:
            if not isinstance(statement, dict):
                continue
            stmt = dict(statement)
            stmt["answer"] = None
            stmt.pop("explanation", None)
            statements.append(stmt)
        cleaned["statements"] = statements
    return cleaned


def build_question_row(item: dict[str, Any], filepath: str) -> dict[str, Any]:
    section_code = str(item["section_code"])
    question_number = int(item["question_number"])
    raw_text = dict(item)
    has_source_solution = scan_item_has_source_solution(item)
    stored_item = item if has_source_solution else strip_scan_answers(item)
    if not has_source_solution:
        raw_text["scan_answer_policy"] = "stripped_unless_source_solution"
    question = {
        "id": str(uuid.uuid4()),
        "source_code": question_signature(stored_item),
        "section_code": section_code,
        "question_number": question_number,
        "display_order": display_order_for(section_code, question_number),
        "question_type": stored_item["type"],
        "topic": stored_item.get("topic"),
        "subtopic": stored_item.get("subtopic"),
        "chapter": stored_item.get("chapter"),
        "difficulty": stored_item.get("difficulty"),
        "question_text": stored_item.get("question_text"),
        "option_a": stored_item.get("option_a"),
        "option_b": stored_item.get("option_b"),
        "option_c": stored_item.get("option_c"),
        "option_d": stored_item.get("option_d"),
        "correct_answer": stored_item.get("correct_answer"),
        "statements": stored_item.get("statements") if stored_item.get("type") == "true_false" else None,
        "numeric_answer": normalize_short_answer_value(stored_item.get("numeric_answer")) if stored_item.get("type") == "short_answer" else stored_item.get("numeric_answer"),
        "explanation": stored_item.get("explanation"),
        "needs_visual": bool(stored_item.get("needs_visual")),
        "visual_type": stored_item.get("visual_type"),
        "visual_bbox": stored_item.get("visual_bbox"),
        "visual_table": stored_item.get("visual_table"),
        "image_url": None,
        "answer_source": "source_extracted" if question_has_answer({"question_type": stored_item["type"], **stored_item}) else "missing",
        "needs_review": False,
        "is_published": False,
        "raw_text": raw_text,
        "source_file": filepath,
        "page_number": stored_item.get("page_number"),
        "source_hint": stored_item.get("source_hint"),
        "max_score": max_score_for(section_code),
        "scoring_rule_snapshot": next((section["scoring_rule"] for section in SECTION_DEFS if section["section_code"] == section_code), {}),
    }
    question["needs_review"] = not question_has_answer(question)
    return question


def dedupe_questions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def candidate_score(row: dict[str, Any]) -> float:
        raw_text = row.get("raw_text") if isinstance(row.get("raw_text"), dict) else {}
        parser_source = raw_text.get("parser_source") or row.get("parser_source")
        quality_penalty = 10000 * len(question_quality_issues(row))
        source_bonus = 5000 if parser_source == "model_page_parse" else 0
        answer_bonus = 250 if question_has_answer(row) else 0
        text = str(row.get("question_text") or "")
        length_score = min(len(text), 900) / 10
        statement_bonus = 0
        if row.get("question_type") == "true_false":
            statements = row.get("statements")
            statement_bonus = 100 * min(len(statements), 4) if isinstance(statements, list) else 0
        visual_bonus = 50 if row.get("needs_visual") else 0
        return source_bonus + answer_bonus + statement_bonus + visual_bonus + length_score - quality_penalty

    best_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["section_code"]), int(row["question_number"]))
        current = best_by_key.get(key)
        if current is None:
            best_by_key[key] = row
            continue
        if candidate_score(row) > candidate_score(current):
            best_by_key[key] = row
    return sorted(best_by_key.values(), key=lambda row: int(row["display_order"]))


def should_merge_text_fallback(questions: list[dict[str, Any]]) -> bool:
    if len(questions) < 22 or count_items(questions) < 34:
        return True
    section_counts: dict[str, int] = {}
    for question in questions:
        section_code = str(question.get("section_code"))
        section_counts[section_code] = section_counts.get(section_code, 0) + 1
    return any(section_counts.get(section["section_code"], 0) < section["expected_count"] for section in SECTION_DEFS)


def solve_missing_answers(client: anthropic.Anthropic, questions: list[dict[str, Any]], enabled: bool) -> None:
    if not enabled:
        return
    for question in questions:
        if question_has_answer(question):
            question["answer_source"] = "original"
            continue
        solve_row = {
            "id": question["id"],
            "question_type": question["question_type"],
            "question_text": question["question_text"],
            "option_a": question.get("option_a"),
            "option_b": question.get("option_b"),
            "option_c": question.get("option_c"),
            "option_d": question.get("option_d"),
            "correct_answer": question.get("correct_answer"),
            "statements": question.get("statements"),
            "numeric_answer": question.get("numeric_answer"),
            "raw_text": question.get("raw_text"),
            "needs_visual": question.get("needs_visual"),
        }
        if not has_answerable_content(solve_row):
            question["needs_review"] = True
            question["answer_source"] = "missing"
            continue
        result = solve_with_claude(client, solve_row)
        if not result:
            question["needs_review"] = True
            question["answer_source"] = "missing"
            continue
        update = build_update(solve_row, result) or {}
        for key, value in update.items():
            if key == "statements" and isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            question[key] = value
        question["answer_source"] = "ai_solved"
        question["needs_review"] = True


def enrich_system_prompt(q_type: str) -> str:
    catalog = taxonomy_prompt_catalog()
    style = (
        "\nSTYLE BẮT BUỘC - dùng giống preview đề 01 và đề 10:\n"
        "- Chỉ trả lời lời giải trong trường exp và statement_explanations; không tạo solution_steps, không tạo bố cục mới.\n"
        "- Giọng văn: ngắn gọn, mạch lạc, giúp học sinh nhớ công thức/lý thuyết cốt lõi; không viết dài kiểu bài giảng.\n"
        "- Nếu có lời giải/đáp án gốc trong PDF: AI tự giải độc lập, tự đối chiếu nội bộ, nhưng tuyệt đối không viết quá trình đối chiếu vào exp.\n"
        "- Nếu AI và nguồn gốc khác nhau: kiểm tra lại bằng đề bài/hình; nếu nguồn gốc sai thì âm thầm trả đáp án đúng của AI và đặt source_answer_status='source_wrong'; nếu chưa chắc thì giữ needs_review=true.\n"
        "- Nếu không có lời giải gốc: AI tự giải, nhưng vẫn viết theo style này.\n"
        "- exp chỉ được là lời giải cho học sinh; không nhắc PDF, đáp án gốc, nguồn gốc, đối chiếu, trùng khớp, nguồn sai, hay kết quả tự giải.\n"
        "- Với hình học không gian tính khoảng cách/góc/tọa độ, ưu tiên phương pháp gắn hệ trục tọa độ khi tự nhiên và dễ hiểu hơn.\n"
        "- Nếu có mẹo Casio 580VN X hữu ích, thêm một câu ngắn cuối exp dạng 'Mẹo Casio 580VN X: ...'; không thêm nếu không thật sự giúp nhanh.\n"
        "- Không đưa header/footer/watermark/ID/source/raw metadata vào exp.\n"
        "- Công thức phải bọc $...$ hoặc $$...$$; vector viết $\\vec{a}$ cho vector chữ thường và $\\overrightarrow{AB}$ cho vector hai điểm.\n"
        "- Nếu bài có bảng thống kê/dữ liệu ghép nhóm, exp nên tạo bảng markdown gọn để học sinh nhìn được cách tính.\n"
    )
    base = (
        "Bạn là giáo viên Toán THPT. Hãy giải chính xác, trình bày theo từng bước, dùng LaTeX chuẩn. "
        "Kiểm tra đáp án hiện có nếu có, đồng thời phân loại câu hỏi theo đúng taxonomy được cung cấp. "
        "Nếu đề bài có lời giải/đáp án gốc, hãy tự giải độc lập trước rồi đối chiếu nội bộ với nguồn gốc sau. "
        "Nếu AI và nguồn gốc khác nhau, hãy kiểm tra lại kỹ; nếu nguồn gốc sai thì trả đáp án đúng của AI và đặt source_answer_status='source_wrong', nếu chưa chắc thì đặt needs_review=true. "
        "Không được đưa bất kỳ câu chữ đối chiếu nội bộ nào vào exp. "
        "Nếu không có lời giải/đáp án gốc, hãy tự giải từ đầu và chỉ đánh dấu review khi không chắc. "
        "Mọi biểu thức toán trong exp phải bọc bằng $...$ hoặc $$...$$ để MathJax render được. "
        "Chỉ trả về JSON hợp lệ, không markdown, không thêm chữ ngoài JSON. "
        "Nếu câu cần hình/bảng/đồ thị, hãy đọc ảnh trang đính kèm và nêu rõ dữ kiện lấy từ hình trong lời giải.\n\n"
        f"{style}\n"
        "TAXONOMY SUBTOPICS - bắt buộc chọn đúng một subtopic_id trong danh sách này; không tự tạo topic/subtopic mới:\n"
        f"{catalog}"
    )
    if q_type == "multiple_choice":
        return (
            base
            + '\nVới trắc nghiệm A/B/C/D: giải ngắn gọn nhưng phải giúp học sinh nhớ công thức hoặc lý thuyết cơ bản. Nên có mạch: Ý tưởng -> Công thức/lý thuyết cần nhớ -> Áp dụng -> Kết luận. Không phân tích dài từng phương án nếu không cần.'
            + '\nSchema: {"exp":"lời giải rõ ràng theo các ý: Ý tưởng, Công thức/lý thuyết cần nhớ, Áp dụng, Kết luận","ans":"A|B|C|D|null","source_answer_status":"matches|source_wrong|ai_uncertain|null",'
            '"canonical_subtopic_id":123,"difficulty":"Nhận biết|Thông hiểu|Vận dụng|Vận dụng cao|null",'
            '"needs_review":false,"review_reason":null,"note":null}.'
        )
    if q_type == "true_false":
        return (
            base
            + '\nVới đúng/sai: viết một mạch giải chung liên kết các ý, rồi giải thích riêng từng ý a,b,c,d. Các ý sau nên tận dụng kết quả/nhận xét từ ý trước nếu có.'
            + '\nSchema: {"exp":"mạch giải chung ngắn gọn và kết luận đáp án","ans":"DSSD","source_answer_status":"matches|source_wrong|ai_uncertain|null",'
            '"statement_explanations":[{"label":"a","answer":true,"exp":"giải thích rõ vì sao đúng/sai, gắn với mạch giải chung"},'
            '{"label":"b","answer":false,"exp":"..."},{"label":"c","answer":true,"exp":"..."},{"label":"d","answer":false,"exp":"..."}],'
            '"canonical_subtopic_id":123,"difficulty":"Nhận biết|Thông hiểu|Vận dụng|Vận dụng cao|null",'
            '"needs_review":false,"review_reason":null,"note":null}. ans gồm đúng 4 ký tự D/S theo thứ tự a,b,c,d; mỗi statement_explanations phải có lời giải riêng.'
        )
    return (
        base
        + '\nVới trả lời ngắn: giải thích nền tảng cơ bản và các bước tính thật rõ. Nên có mạch: Dữ kiện -> Kiến thức/công thức cần dùng -> Tính toán từng bước -> Kết quả.'
        + '\nSchema: {"exp":"lời giải chi tiết theo các ý: Dữ kiện, Kiến thức cần dùng, Tính toán, Kết quả","ans":"đáp án cuối chỉ gồm số nguyên hoặc số thập phân; dùng dấu chấm cho thập phân; không ghi đơn vị","source_answer_status":"matches|source_wrong|ai_uncertain|null",'
        '"canonical_subtopic_id":123,"difficulty":"Nhận biết|Thông hiểu|Vận dụng|Vận dụng cao|null",'
        '"needs_review":false,"review_reason":null,"note":null}.'
    )


def enrich_user_text(question: dict[str, Any]) -> str:
    lines = [
        f"Loai cau: {question.get('question_type')}",
        f"Vi tri: {question.get('section_code')} cau {question.get('question_number')}",
        f"De bai:\n{question.get('question_text') or ''}",
    ]
    if question.get("question_type") == "multiple_choice":
        lines.append(
            "Lua chon:\n"
            f"A. {question.get('option_a') or ''}\n"
            f"B. {question.get('option_b') or ''}\n"
            f"C. {question.get('option_c') or ''}\n"
            f"D. {question.get('option_d') or ''}"
        )
    if question.get("question_type") == "true_false":
        statements = question.get("statements") if isinstance(question.get("statements"), list) else []
        lines.append("Cac menh de:")
        for index, statement in enumerate(statements):
            if isinstance(statement, dict):
                lines.append(f"{statement.get('label') or chr(97 + index)}. {statement.get('text') or ''}")
    current_answer = answer_text_for(question) if question.get("answer_source") == "source_extracted" else None
    if current_answer:
        lines.append(
            f"Dap an/loi giai goc trong file PDF de kiem tra: {current_answer}. "
            "Hay tu giai doc lap truoc, sau do doi chieu. Neu nguon goc sai, tra ve dap an dung cua AI va source_answer_status='source_wrong'. "
            "Neu chua chac AI hay nguon goc sai, dat source_answer_status='ai_uncertain' va needs_review=true. "
            "Khong nhac PDF, dap an goc, nguon goc, doi chieu, trung khop hay sai khac trong exp."
        )
    source_solution = source_solution_text(question)
    if source_solution:
        lines.append(
            "Nguon goc trong file PDF co loi giai/dap an. Hay doc de hieu nguon goc, nhung van tu giai doc lap truoc roi moi doi chieu. "
            "Neu nguon goc sai sau khi kiem tra lai, tra dap an dung cua AI, viet loi giai theo cach AI, va dat source_answer_status='source_wrong'. "
            "Neu chua chac, giu needs_review=true va noi ngan gon can admin kiem tra. "
            "Tuyet doi khong dua qua trinh doi chieu noi bo vao exp.\n"
            f"<source_solution>\n{source_solution}\n</source_solution>"
        )
    lines.append(
        "Yeu cau loi giai: voi cau trac nghiem A/B/C/D, giai ngan gon 2-3 buoc de tiet kiem token. "
        "Voi cau dung-sai, bat buoc giai thich rieng tung y a,b,c,d; khong chi ghi dap an. "
        "Voi cau tra loi ngan, truong ans chi gom so hoac phan so, dung dau cham cho thap phan, khong ghi don vi. "
        "Voi hinh hoc khong gian tinh khoang cach/goc/toa do, uu tien gan he truc toa do neu cach nay gon va de hieu. "
        "Neu co meo Casio 580VN X huu ich thi them mot cau ngan cuoi exp. "
        "Tat ca cong thuc toan trong exp phai boc bang $...$ hoac $$...$$."
    )
    if question.get("topic") or question.get("subtopic"):
        lines.append(f"Nhan dang co: topic={question.get('topic')}; subtopic={question.get('subtopic')}; difficulty={question.get('difficulty')}")
    return "\n\n".join(lines)


def enrich_with_claude(client: anthropic.Anthropic, question: dict[str, Any]) -> dict[str, Any] | None:
    content: list[dict[str, Any]] | str
    text = enrich_user_text(question)
    if question.get("needs_visual") and question.get("source_file") and question.get("page_number"):
        try:
            image_b64 = render_page_from_file_png_base64(
                str(question["source_file"]),
                int(question["page_number"]) - 1,
                zoom=1.8,
            )
            content = [
                {"type": "text", "text": text + "\n\nNeu cau hoi can hinh/bang/do thi, hay doc them anh trang dinh kem."},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
            ]
        except Exception:
            content = text
    else:
        content = text

    last_error: Exception | None = None
    for attempt in range(1, ENRICH_RETRY_COUNT + 1):
        try:
            response = create_message_with_model_fallback(
                client,
                primary_model=CLAUDE_VISION_MODEL if isinstance(content, list) else CLAUDE_MODEL,
                max_tokens=3500,
                temperature=0,
                system=enrich_system_prompt(str(question.get("question_type"))),
                messages=[{"role": "user", "content": content}],
                extra_headers={"User-Agent": "curl/8.7.1"},
            )
            return extract_json_object(router_response_text(response))
        except Exception as exc:
            last_error = exc
            log.warning("Enrich failed attempt %s/%s for %s: %s", attempt, ENRICH_RETRY_COUNT, question.get("id"), exc)
            if attempt < ENRICH_RETRY_COUNT:
                time.sleep(rate_limit_sleep_seconds(exc, attempt))
    log.error("Skip enrich %s after errors: %s", question.get("id"), last_error)
    return None


def rate_limit_sleep_seconds(exc: Exception, attempt: int) -> float:
    message = str(exc)
    reset_match = re.search(r"reset after (?:(\d+)m\s*)?(\d+)s", message)
    if reset_match:
        minutes = int(reset_match.group(1) or 0)
        seconds = int(reset_match.group(2) or 0)
        return min(300, minutes * 60 + seconds + 10)
    if "429" in message or "rate_limit" in message:
        return min(180, 20 * attempt)
    return min(30, 2**attempt)


def apply_enrichment(question: dict[str, Any], result: dict[str, Any]) -> None:
    q_type = question.get("question_type")
    source_primary = source_answer_is_primary(question)
    source_answer_status = fold_text(result.get("source_answer_status")).replace("-", "_").replace(" ", "_")
    source_wrong = source_answer_status in {"source_wrong", "nguon_goc_sai", "dap_an_goc_sai", "de_sai"}
    ai_uncertain = source_answer_status in {"ai_uncertain", "uncertain", "chua_chac", "can_review"}
    exp = strip_internal_solution_audit(result.get("exp"))
    if exp:
        question["explanation"] = exp
    difficulty = normalize_optional_text(result.get("difficulty"))
    if difficulty:
        question["difficulty"] = difficulty
    apply_taxonomy_assignment(question, result.get("canonical_subtopic_id"))
    ans = result.get("ans")
    if q_type == "multiple_choice":
        ans_text = normalize_optional_text(ans)
        if ans_text:
            ans_text = ans_text.upper()
        if ans_text in {"A", "B", "C", "D"}:
            current = question.get("correct_answer")
            if current and current != ans_text and source_primary:
                question.setdefault("raw_text", {})["source_answer_conflict"] = {"source": current, "model": ans_text}
                if source_wrong:
                    question.setdefault("raw_text", {})["source_answer_replaced"] = {"before": current, "after": ans_text, "reason": "model_verified_source_wrong"}
                    question["correct_answer"] = ans_text
                else:
                    result["needs_review"] = True
                    result["review_reason"] = result.get("review_reason") or "AI tinh khac dap an/loi giai goc; can admin kiem tra."
            else:
                if current and current != ans_text:
                    question.setdefault("raw_text", {})["scan_answer_replaced"] = {"before": current, "after": ans_text}
                question["correct_answer"] = ans_text
    elif q_type == "true_false":
        ans_text = normalize_optional_text(ans)
        if ans_text:
            ans_text = ans_text.upper().replace("?", "D").replace(" ", "")
        statements = question.get("statements") if isinstance(question.get("statements"), list) else []
        current_answer = answer_text_for(question)
        conflict_with_source = bool(source_primary and current_answer and ans_text and re.fullmatch(r"[DS]{4}", ans_text) and current_answer != ans_text)
        if conflict_with_source:
            question.setdefault("raw_text", {})["source_answer_conflict"] = {"source": current_answer, "model": ans_text}
        while len(statements) < 4:
            statements.append({"label": chr(97 + len(statements)), "text": "", "answer": None})
        if ans_text and re.fullmatch(r"[DS]{4}", ans_text) and (not conflict_with_source or source_wrong):
            if conflict_with_source and source_wrong:
                question.setdefault("raw_text", {})["source_answer_replaced"] = {"before": current_answer, "after": ans_text, "reason": "model_verified_source_wrong"}
            for index, char in enumerate(ans_text[:4]):
                if isinstance(statements[index], dict):
                    statements[index]["answer"] = char == "D"
        explanations = result.get("statement_explanations")
        if isinstance(explanations, list):
            by_label = {
                str(item.get("label") or "").strip().lower(): item
                for item in explanations
                if isinstance(item, dict)
            }
            for index, statement in enumerate(statements):
                if not isinstance(statement, dict):
                    continue
                label = str(statement.get("label") or chr(97 + index)).strip().lower()
                detail = by_label.get(label)
                if detail:
                    if isinstance(detail.get("answer"), bool) and (not conflict_with_source or source_wrong):
                        statement["answer"] = bool(detail.get("answer"))
                    detail_exp = strip_internal_solution_audit(detail.get("exp"))
                    if detail_exp:
                        statement["explanation"] = detail_exp
        tf_answer = answer_text_for({"question_type": "true_false", "statements": statements})
        if tf_answer:
            question["correct_answer"] = tf_answer
        if conflict_with_source and not source_wrong:
            result["needs_review"] = True
            result["review_reason"] = result.get("review_reason") or "AI tinh khac dap an/loi giai goc; can admin kiem tra."
        question["statements"] = statements
    elif q_type == "short_answer":
        ans_text = normalize_optional_text(ans)
        if ans_text:
            normalized_answer = normalize_short_answer_value(ans_text) or ans_text
            current = question.get("numeric_answer")
            if current not in (None, "") and str(current) != str(normalized_answer) and source_primary:
                question.setdefault("raw_text", {})["source_answer_conflict"] = {"source": current, "model": normalized_answer}
                if source_wrong:
                    question.setdefault("raw_text", {})["source_answer_replaced"] = {"before": current, "after": normalized_answer, "reason": "model_verified_source_wrong"}
                    question["numeric_answer"] = normalized_answer
                else:
                    result["needs_review"] = True
                    result["review_reason"] = result.get("review_reason") or "AI tinh khac dap an/loi giai goc; can admin kiem tra."
            else:
                question["numeric_answer"] = normalized_answer
    if source_wrong:
        question["answer_source"] = "ai_corrected_source"
        question.setdefault("raw_text", {})["source_answer_status"] = "source_wrong"
    else:
        question["answer_source"] = "source_verified" if source_primary else "ai_enriched"
    review_reason = normalize_optional_text(result.get("review_reason"))
    if review_reason:
        question.setdefault("raw_text", {})["review_reason"] = review_reason
    review_from_model = bool(result.get("needs_review", False)) or ai_uncertain
    if review_reason and any(marker in review_reason.lower() for marker in ["kh?p", "ch?nh x?c", "chinh xac", "??ng l?", "dung la"]):
        review_from_model = False
    question["needs_review"] = review_from_model or bool(question.get("taxonomy_needs_review"))

def apply_taxonomy_assignment(question: dict[str, Any], subtopic_id_value: Any) -> None:
    taxonomy = load_taxonomy()
    try:
        subtopic_id = int(subtopic_id_value)
    except (TypeError, ValueError):
        subtopic_id = None
    subtopic = next((item for item in taxonomy["subtopics"] if int(item["id"]) == subtopic_id), None)
    if not subtopic:
        question["taxonomy_needs_review"] = True
        question.setdefault("raw_text", {})["taxonomy_review_reason"] = f"Invalid canonical_subtopic_id: {subtopic_id_value}"
        return
    question["canonical_topic_id"] = int(subtopic["topic_id"])
    question["canonical_topic_code"] = subtopic.get("topic_code")
    question["canonical_topic_title"] = subtopic.get("topic_title")
    question["canonical_subtopic_id"] = int(subtopic["id"])
    question["canonical_subtopic_code"] = subtopic.get("subtopic_code")
    question["canonical_subtopic_title"] = subtopic.get("display_title") or subtopic.get("subtopic_title")
    question["topic"] = question["canonical_topic_title"]
    question["subtopic"] = question["canonical_subtopic_title"]
    question["taxonomy_needs_review"] = False


def enrich_questions(client: anthropic.Anthropic, questions: list[dict[str, Any]], mode: str) -> None:
    if mode == "none":
        return
    for question in questions:
        needs_answer = not question_has_answer(question)
        needs_explanation = not normalize_optional_text(question.get("explanation"))
        needs_taxonomy = not normalize_optional_text(question.get("topic")) or str(question.get("topic")).startswith("Ch")
        should_enrich = mode == "all" or needs_answer or needs_explanation or needs_taxonomy
        if not should_enrich:
            continue
        result = enrich_with_claude(client, question)
        if not result:
            question["needs_review"] = True
            continue
        apply_enrichment(question, result)
        if ENRICH_DELAY_SECONDS > 0:
            time.sleep(ENRICH_DELAY_SECONDS)


def build_sections(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for question in questions:
        counts[str(question.get("section_code"))] = counts.get(str(question.get("section_code")), 0) + 1
    sections = []
    for section in SECTION_DEFS:
        row = dict(section)
        row["extracted_count"] = counts.get(section["section_code"], 0)
        sections.append(row)
    return sections


def count_items(questions: list[dict[str, Any]]) -> int:
    total = 0
    for question in questions:
        if question.get("question_type") == "true_false":
            statements = question.get("statements")
            total += len(statements) if isinstance(statements, list) else 0
        else:
            total += 1
    return total


QUESTION_TEXT_BLOCKLIST = (
    "shared by",
    "fanpage",
    "[page",
    "classin",
    "tai lieu khoa hoc",
    "tài liệu khóa học",
    "dang ky khoa hoc",
    "đăng ký khóa học",
    "xem lai bai giang",
    "xem lại bài giảng",
    "khong co buoc chan",
    "khóa học sslive",
    "khoa hoc sslive",
)

QUESTION_SOLUTION_MARKERS = (
    "loi giai",
    "lời giải",
    "tra loi:",
    "trả lời:",
    "dap an:",
    "đáp án:",
)


def question_quality_issues(question: dict[str, Any]) -> list[str]:
    text = str(question.get("question_text") or "")
    folded = fold_text(text)
    issues: list[str] = []
    if any(marker in folded for marker in QUESTION_TEXT_BLOCKLIST):
        issues.append("question_text contains watermark/header/footer/promotional text")
    if any(marker in folded for marker in QUESTION_SOLUTION_MARKERS):
        issues.append("question_text appears to include answer/solution text")
    if re.search(r"[\uf000-\uf8ff\ufffd□�]", text):
        issues.append("question_text contains OCR private-use/replacement glyphs")
    if re.search(r"\b\w\?\?\w|\?\?\w|\w\?\?", text) or text.count("?") >= 5:
        issues.append("question_text contains mojibake question marks")
    # Very long short-answer rows are often caused by native text fallback swallowing
    # the solution/footer after the prompt. Keep this as review-only, not deletion.
    if question.get("question_type") == "short_answer" and len(text) > 1200:
        issues.append("question_text unusually long for short-answer item; possible merged solution/footer")
    return issues


def apply_quality_audit(questions: list[dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    for question in questions:
        issues = question_quality_issues(question)
        if not issues:
            continue
        question["needs_review"] = True
        raw_text = question.setdefault("raw_text", {})
        raw_text["quality_review_reasons"] = issues
        failures.append(
            {
                "section_code": question.get("section_code"),
                "question_number": question.get("question_number"),
                "reason": "; ".join(issues),
            }
        )


def audit_exam(questions: list[dict[str, Any]], failures: list[dict[str, Any]]) -> dict[str, Any]:
    sections = {}
    for section in build_sections(questions):
        sections[section["section_code"]] = {
            "expected": section["expected_count"],
            "actual": section["extracted_count"],
            "max_score": section["max_score"],
        }
    missing_answers = sum(1 for question in questions if not question_has_answer(question))
    ai_solved = sum(1 for question in questions if question.get("answer_source") == "ai_solved")
    needs_review = sum(1 for question in questions if question.get("needs_review"))
    section_mismatch = any(item["expected"] != item["actual"] for item in sections.values())
    extracted_item_count = count_items(questions)
    status = "ready"
    if failures or section_mismatch or extracted_item_count != 34 or missing_answers or needs_review:
        status = "needs_review"
    return {
        "expected_question_count": 22,
        "expected_item_count": 34,
        "extracted_question_count": len(questions),
        "extracted_item_count": extracted_item_count,
        "max_score": 10.0,
        "sections": sections,
        "missing_answers": missing_answers,
        "ai_solved_answers": ai_solved,
        "needs_review": needs_review,
        "failures": failures,
        "status": status,
    }


def build_exam_document(
    filepath: str,
    file_hash: str,
    exam_range: ExamRange,
    questions: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    pages_from_cache: int,
) -> dict[str, Any]:
    audit = audit_exam(questions, failures)
    exam = {
        "id": str(uuid.uuid4()),
        "title": exam_range.title,
        "subject": "math",
        "exam_type": "thpt_graduation_standard",
        "source_file": filepath,
        "source_hash": file_hash,
        "source_id": exam_range.source_id,
        "exam_index": exam_range.exam_index,
        "start_page": exam_range.start_page,
        "end_page": exam_range.end_page,
        "total_pages": exam_range.end_page - exam_range.start_page + 1,
        "expected_question_count": 22,
        "expected_item_count": 34,
        "extracted_question_count": len(questions),
        "max_score": 10.0,
        "status": audit["status"],
        "audit_json": audit,
    }
    return {
        "exam": exam,
        "sections": build_sections(questions),
        "questions": questions,
        "audit": audit,
        "run": {
            "mode": "dry_run",
            "pages_from_cache": pages_from_cache,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
    }


def schema_client(client: Client):
    if hasattr(client, "schema"):
        return client.schema("standard_exam")
    return client


def explain_supabase_schema_error(exc: Exception) -> None:
    message = str(exc)
    if "PGRST106" in message or "Invalid schema: standard_exam" in message:
        raise SystemExit(
            "Supabase Data API has not exposed schema 'standard_exam'. "
            "In Supabase Dashboard > Project Settings > Data API, add 'standard_exam' to Exposed schemas, "
            "then rerun docs/standard_exam_supabase_schema.sql grants/reload block."
        ) from exc
    if "permission denied for schema standard_exam" in message or "42501" in message:
        raise SystemExit(
            "Supabase can see schema 'standard_exam', but the API role has no schema/table grants. "
            "Run the grant/reload block at the end of docs/standard_exam_supabase_schema.sql in Supabase SQL Editor."
        ) from exc
    raise exc


def commit_to_supabase(data: dict[str, Any], upload_visuals: bool, replace_existing: bool = False) -> str:
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    db = schema_client(client)
    base_ingest.UPLOAD_VISUALS = upload_visuals
    exam = data["exam"]
    questions = data["questions"]
    sections = data["sections"]

    try:
        existing_exam = (
            db.table("exam_sets")
            .select("id,title")
            .eq("source_hash", exam["source_hash"])
            .eq("exam_index", exam["exam_index"])
            .limit(1)
            .execute()
        )
    except Exception as exc:
        explain_supabase_schema_error(exc)
    if existing_exam.data:
        existing_id = existing_exam.data[0]["id"]
        if not replace_existing:
            log.info(
                "Skip commit for exam %s because exam_set already exists: %s",
                exam["exam_index"],
                existing_id,
            )
            return "skipped_existing"
        db.table("exam_sets").delete().eq("id", existing_id).execute()

    if upload_visuals:
        file_hash = str(exam["source_hash"])
        for index, question in enumerate(questions, start=1):
            if not question.get("needs_visual"):
                continue
            try:
                question["image_url"] = upload_question_visual(
                    client,
                    exam["source_file"],
                    file_hash,
                    int(question.get("page_number") or 1),
                    index,
                    question.get("visual_bbox"),
                    question.get("source_hint"),
                )
            except Exception as exc:
                question["needs_review"] = True
                question.setdefault("raw_text", {})["visual_upload_error"] = str(exc)

    exam_payload = {
        "id": exam["id"],
        "title": exam["title"],
        "subject": exam["subject"],
        "exam_type": exam["exam_type"],
        "source_file": exam["source_file"],
        "source_hash": exam["source_hash"],
        "source_id": exam.get("source_id"),
        "exam_index": exam["exam_index"],
        "start_page": exam["start_page"],
        "end_page": exam["end_page"],
        "total_pages": exam["total_pages"],
        "expected_question_count": exam["expected_question_count"],
        "expected_item_count": exam["expected_item_count"],
        "extracted_question_count": exam["extracted_question_count"],
        "max_score": exam["max_score"],
        "status": exam["status"],
        "audit_json": exam["audit_json"],
    }
    db.table("exam_sets").insert(exam_payload).execute()

    section_id_by_code: dict[str, str] = {}
    section_payloads = []
    for section in sections:
        section_id = str(uuid.uuid4())
        section_id_by_code[section["section_code"]] = section_id
        section_payloads.append(
            {
                "id": section_id,
                "exam_set_id": exam["id"],
                "section_code": section["section_code"],
                "title": section["title"],
                "question_type": section["question_type"],
                "section_order": section["section_order"],
                "expected_count": section["expected_count"],
                "extracted_count": section["extracted_count"],
                "max_score": section["max_score"],
                "scoring_rule": section["scoring_rule"],
            }
        )
    db.table("exam_sections").insert(section_payloads).execute()

    question_payloads = []
    for question in questions:
        question_payloads.append(
            {
                "id": question["id"],
                "source_code": question["source_code"],
                "question_type": question["question_type"],
                "question_text": question["question_text"],
                "option_a": question.get("option_a"),
                "option_b": question.get("option_b"),
                "option_c": question.get("option_c"),
                "option_d": question.get("option_d"),
                "correct_answer": answer_text_for(question) if question.get("question_type") == "true_false" else question.get("correct_answer"),
                "statements": question.get("statements"),
                "numeric_answer": None if question.get("numeric_answer") is None else str(question.get("numeric_answer")),
                "explanation": question.get("explanation"),
                "topic": question.get("topic"),
                "subtopic": question.get("subtopic"),
                "chapter": question.get("chapter"),
                "difficulty": question.get("difficulty"),
                "canonical_topic_id": question.get("canonical_topic_id"),
                "canonical_topic_code": question.get("canonical_topic_code"),
                "canonical_topic_title": question.get("canonical_topic_title"),
                "canonical_subtopic_id": question.get("canonical_subtopic_id"),
                "canonical_subtopic_code": question.get("canonical_subtopic_code"),
                "canonical_subtopic_title": question.get("canonical_subtopic_title"),
                "needs_visual": question.get("needs_visual"),
                "visual_type": question.get("visual_type"),
                "visual_bbox": question.get("visual_bbox"),
                "visual_table": question.get("visual_table"),
                "image_url": question.get("image_url"),
                "raw_text": question.get("raw_text"),
                "answer_source": question.get("answer_source"),
                "needs_review": question.get("needs_review"),
                "is_published": question.get("is_published"),
            }
        )
    db.table("questions").upsert(question_payloads, on_conflict="source_code").execute()

    existing = db.table("questions").select("id,source_code").in_("source_code", [q["source_code"] for q in questions]).execute()
    id_by_source = {row["source_code"]: row["id"] for row in (existing.data or [])}
    exam_question_payloads = []
    for question in questions:
        question_id = id_by_source.get(question["source_code"], question["id"])
        exam_question_payloads.append(
            {
                "id": str(uuid.uuid4()),
                "exam_set_id": exam["id"],
                "section_id": section_id_by_code[question["section_code"]],
                "question_id": question_id,
                "section_code": question["section_code"],
                "question_number": question["question_number"],
                "display_order": question["display_order"],
                "page_number": question["page_number"],
                "source_hint": question.get("source_hint"),
                "max_score": question["max_score"],
                "scoring_rule_snapshot": question["scoring_rule_snapshot"],
            }
        )
    db.table("exam_questions").insert(exam_question_payloads).execute()
    db.table("ingest_runs").insert(
        {
            "id": str(uuid.uuid4()),
            "exam_set_id": exam["id"],
            "source_file": exam["source_file"],
            "source_hash": exam["source_hash"],
            "mode": "commit",
            "status": exam["status"],
            "stats_json": data.get("audit"),
            "failures_json": data.get("audit", {}).get("failures", []),
        }
    ).execute()
    return "committed"


def existing_ready_exam(filepath: str, exam_index: int) -> bool:
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    db = schema_client(client)
    source_hash = file_sha1(filepath)
    try:
        result = (
            db.table("exam_sets")
            .select("id,status,audit_json")
            .eq("source_hash", source_hash)
            .eq("exam_index", exam_index)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        explain_supabase_schema_error(exc)
    if not result.data:
        return False
    row = result.data[0]
    audit = row.get("audit_json") or {}
    return (
        row.get("status") == "ready"
        and audit.get("extracted_question_count") == 22
        and audit.get("extracted_item_count") == 34
        and audit.get("needs_review") == 0
    )


def output_stem(filepath: str, exam_index: int) -> str:
    name = Path(filepath).stem
    if "MOON" in name.upper() or "60" in name:
        if exam_index == -1:
            return "moon_de_tham_khao_2025"
        if exam_index == 0:
            return "moon_de_chinh_thuc_2025"
        return f"moon_de_{exam_index:02d}"
    normalized = unicodedata.normalize("NFD", name)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.replace("đ", "d").replace("Đ", "D")
    safe = re.sub(r"[^A-Za-z0-9]+", "_", normalized).strip("_").lower()
    return f"{safe}_de_{exam_index:02d}"


def folder_pdf_files(folder: str) -> list[Path]:
    return sorted(Path(folder).rglob("*.pdf"), key=lambda path: str(path).casefold())


def process_exam(args: argparse.Namespace, exam_index: int, client: anthropic.Anthropic, filepath_override: str | None = None) -> dict[str, Any]:
    filepath = str(Path(filepath_override or args.file))
    file_hash = file_sha1(filepath)
    if args.single_pdf_exam:
        exam_range = single_pdf_exam_range(filepath, exam_index)
        if args.page_from > 0:
            exam_range.start_page = args.page_from
            exam_range.end_page = args.page_to if args.page_to > 0 else exam_range.end_page
    else:
        exam_range = resolve_exam_range(filepath, exam_index, args.page_from, args.page_to)
    log.info("Exam %s: pages %s-%s", exam_index, exam_range.start_page, exam_range.end_page)

    payloads = build_exam_payloads(filepath, exam_range, force_vision=not args.text_only)
    page_results: dict[int, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    pages_from_cache = 0
    if not args.fallback_only:
        for payload in payloads:
            try:
                page_number, result, from_cache = parse_page_cached(client, file_hash, payload, use_cache=not args.no_cache)
                page_results[page_number] = result
                pages_from_cache += int(from_cache)
                log.info("Page %s: %s items%s", page_number, len(result.get("items", [])), " (cache)" if from_cache else "")
            except Exception as exc:
                failures.append({"page_number": payload.page_number, "reason": str(exc)})
                log.error("Page %s failed: %s", payload.page_number, exc)
                if not args.force:
                    break

    rows = []
    for page_number in sorted(page_results):
        for item in page_results[page_number].get("items", []):
            rows.append(build_question_row(item, filepath))
    if not rows and not args.no_text_fallback:
        log.warning("No model-parsed rows; using native PDF text fallback parser.")
        fallback_items = parse_exam_text_fallback(filepath, exam_range)
        for item in fallback_items:
            rows.append(build_question_row(item, filepath))
    questions = dedupe_questions(rows)
    if questions and should_merge_text_fallback(questions) and not args.no_text_fallback:
        log.warning(
            "Model parse is incomplete (%s questions, %s items); merging native PDF text fallback parser.",
            len(questions),
            count_items(questions),
        )
        fallback_items = parse_exam_text_fallback(filepath, exam_range)
        for item in fallback_items:
            rows.append(build_question_row(item, filepath))
        questions = dedupe_questions(rows)
    if args.enrich_all:
        enrich_questions(client, questions, mode="all")
    else:
        solve_missing_answers(client, questions, enabled=not args.no_solve)
    apply_quality_audit(questions, failures)
    return build_exam_document(filepath, file_hash, exam_range, questions, failures, pages_from_cache)


def write_outputs(data: dict[str, Any], filepath: str, preview: bool) -> tuple[Path, Path | None]:
    exam_index = int(data["exam"]["exam_index"])
    stem = output_stem(filepath, exam_index)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    json_path = PREVIEW_DIR / f"{stem}.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = None
    if preview:
        html_path = PREVIEW_DIR / f"{stem}.html"
        standard_exam_preview_html.render_html(data, html_path)
    run_path = RUN_DIR / f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    run_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path, html_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan standard THPT graduation math exams from a large PDF.")
    parser.add_argument("--file", help="PDF file containing standard exams.")
    parser.add_argument("--folder", help="Folder containing one-standard-exam-per-PDF files.")
    parser.add_argument("--single-pdf-exam", action="store_true", help="Treat each input PDF as one complete exam.")
    parser.add_argument("--exam-index", type=int, default=1, help="Exam index to scan.")
    parser.add_argument("--all-exams", action="store_true", help="Scan all detected exams.")
    parser.add_argument("--exam-from", type=int, default=None, help="Only process exams with index >= this value.")
    parser.add_argument("--exam-to", type=int, default=None, help="Only process exams with index <= this value.")
    parser.add_argument("--limit-exams", type=int, default=0, help="Process at most this many exams after filtering.")
    parser.add_argument("--commit-json", help="Commit an existing standard exam JSON file without rescanning.")
    parser.add_argument("--dry-run", action="store_true", help="Write JSON/HTML only; do not write Supabase.")
    parser.add_argument("--commit", action="store_true", help="Write parsed exam to Supabase standard_exam schema.")
    parser.add_argument("--replace-existing", action="store_true", help="Replace an existing exam_set with the same source_hash and exam_index.")
    parser.add_argument("--skip-existing-ready", action="store_true", help="Skip folder PDFs that already have a ready exam_set.")
    parser.add_argument("--preview", action="store_true", help="Render HTML preview.")
    parser.add_argument("--force", action="store_true", help="Continue even if a page fails.")
    parser.add_argument("--no-cache", action="store_true", help="Ignore standard exam cache.")
    parser.add_argument("--no-solve", action="store_true", help="Do not solve missing answers with Claude.")
    parser.add_argument("--enrich-all", action="store_true", help="Solve/verify all answers and add explanations/taxonomy.")
    parser.add_argument("--text-only", action="store_true", help="Do not send page images.")
    parser.add_argument("--no-text-fallback", action="store_true", help="Disable native PDF text fallback if model parsing fails.")
    parser.add_argument("--fallback-only", action="store_true", help="Skip Claude and parse native PDF text only.")
    parser.add_argument("--page-from", type=int, default=0, help="Override first PDF page to scan.")
    parser.add_argument("--page-to", type=int, default=0, help="Override last PDF page to scan.")
    parser.add_argument("--upload-visuals", dest="upload_visuals", action="store_true", default=True, help="Upload visual crops on commit.")
    parser.add_argument("--no-upload-visuals", dest="upload_visuals", action="store_false", help="Do not upload visual crops on commit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.dry_run and not args.commit:
        args.dry_run = True
    if args.dry_run and args.commit:
        raise SystemExit("Choose only one of --dry-run or --commit.")

    if args.commit_json:
        data = json.loads(Path(args.commit_json).read_text(encoding="utf-8"))
        source_file = str(data.get("exam", {}).get("source_file") or args.file or "")
        if args.preview:
            _, html_path = write_outputs(data, source_file, True)
            if html_path:
                log.info("Wrote HTML: %s", html_path.resolve())
        if args.commit:
            result = commit_to_supabase(data, upload_visuals=args.upload_visuals, replace_existing=args.replace_existing)
            log.info("Supabase commit-json result: %s", result)
        return

    if args.folder:
        args.single_pdf_exam = True
        folder = Path(args.folder)
        if not folder.exists():
            raise SystemExit(f"Missing folder: {folder}")
        files = folder_pdf_files(str(folder))
        if not files:
            raise SystemExit(f"No PDF files found in folder: {folder}")

        base_url = normalize_anthropic_base_url(CLAUDE_BASE_URL)
        log.info("Standard exam folder pipeline: files=%s model=%s fast=%s vision=%s", len(files), CLAUDE_MODEL, CLAUDE_FAST_MODEL, CLAUDE_VISION_MODEL)
        log.info("Router endpoint: %s", base_url)
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY or "local-9router", base_url=base_url)

        for ordinal, pdf_path in enumerate(files, start=1):
            exam_index = filename_exam_index(pdf_path, ordinal)
            log.info("Folder file %s/%s: %s -> exam_index=%s", ordinal, len(files), pdf_path, exam_index)
            if args.skip_existing_ready and existing_ready_exam(str(pdf_path), exam_index):
                log.info("Skip folder file %s/%s because exam_index=%s is already ready", ordinal, len(files), exam_index)
                continue
            data = process_exam(args, exam_index, client, filepath_override=str(pdf_path))
            json_path, html_path = write_outputs(data, str(pdf_path), args.preview or args.dry_run)
            log.info("Wrote JSON: %s", json_path.resolve())
            if html_path:
                log.info("Wrote HTML: %s", html_path.resolve())
            if args.commit:
                result = commit_to_supabase(data, upload_visuals=args.upload_visuals, replace_existing=args.replace_existing)
                log.info("Supabase result for %s: %s", pdf_path.name, result)
        return

    if not args.file:
        raise SystemExit("Missing --file.")

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Missing file: {path}")

    base_url = normalize_anthropic_base_url(CLAUDE_BASE_URL)
    log.info("Standard exam pipeline: model=%s fast=%s vision=%s auto=%s", CLAUDE_MODEL, CLAUDE_FAST_MODEL, CLAUDE_VISION_MODEL, AUTO_SELECT_MODEL)
    log.info("Router endpoint: %s", base_url)
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY or "local-9router", base_url=base_url)

    if args.single_pdf_exam:
        exam_indexes = [filename_exam_index(path, args.exam_index)]
    elif args.all_exams:
        exam_indexes = [item.exam_index for item in find_exam_ranges(str(path))]
    else:
        exam_indexes = [args.exam_index]
    if args.exam_from is not None:
        exam_indexes = [index for index in exam_indexes if index >= args.exam_from]
    if args.exam_to is not None:
        exam_indexes = [index for index in exam_indexes if index <= args.exam_to]
    if args.limit_exams:
        exam_indexes = exam_indexes[: args.limit_exams]
    if not exam_indexes:
        raise SystemExit("No exam boundaries found.")

    for exam_index in exam_indexes:
        data = process_exam(args, exam_index, client)
        json_path, html_path = write_outputs(data, str(path), args.preview or args.dry_run)
        log.info("Wrote JSON: %s", json_path.resolve())
        if html_path:
            log.info("Wrote HTML: %s", html_path.resolve())
        if args.commit:
            result = commit_to_supabase(data, upload_visuals=args.upload_visuals, replace_existing=args.replace_existing)
            log.info("Supabase result for exam %s: %s", exam_index, result)


if __name__ == "__main__":
    main()
