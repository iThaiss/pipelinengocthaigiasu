"""Extract English practice questions with 9Router AI.

Local-only outputs:
- local_curriculum_english/output_json/practice_questions.json
- local_curriculum_english/output_json/practice_scan_coverage.json
- local_curriculum_english/previews/practice_questions_preview.html
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
import fitz

fitz.TOOLS.mupdf_display_errors(False)

DEFAULT_ROOT = Path("local_curriculum_english")
MODEL = os.getenv("PRACTICE_AI_MODEL", "gz-prod/claude-sonnet-4-6")
FALLBACK_MODEL = os.getenv("PRACTICE_AI_FALLBACK_MODEL", "gz-prod/1m-claude-sonnet-4-6-max")
BASE_URL = os.getenv("CLAUDE_BASE_URL", "http://localhost:20128").rstrip("/")
API_KEY = os.getenv("NINEROUTER_API_KEY") or os.getenv("CLAUDE_API_KEY") or "local-9router"
AI_RETRIES = int(os.getenv("PRACTICE_AI_RETRIES", "3"))
AI_RETRY_BASE_SECONDS = float(os.getenv("PRACTICE_AI_RETRY_BASE_SECONDS", "8"))
AI_TIMEOUT_SECONDS = float(os.getenv("PRACTICE_AI_TIMEOUT_SECONDS", "180"))
AI_REQUEST_DELAY_SECONDS = float(os.getenv("PRACTICE_AI_REQUEST_DELAY_SECONDS", "0.15"))
MAX_PAGES = int(os.getenv("PRACTICE_MAX_PAGES", "60"))
MAX_CHARS = int(os.getenv("PRACTICE_MAX_CHARS", "45000"))
VIP90_BUNDLE_MAX_CHARS = int(os.getenv("PRACTICE_VIP90_BUNDLE_MAX_CHARS", "220000"))
ANSWER_SOURCES = {"pdf_key", "ai_solved", "missing"}
CONFIDENCES = {"high", "medium", "low"}
DIFFICULTIES = {"foundation", "basic", "intermediate", "advanced", "exam"}

SYSTEM_PROMPT = """Bạn là chuyên gia trích xuất câu hỏi luyện thi tiếng Anh THPT/HSA/SPT từ PDF.
Trả JSON duy nhất, không markdown. Chỉ dùng taxonomy và question_format được cung cấp.
Nếu PDF có đáp án/key, dùng answer_source=\"pdf_key\". Nếu không có đáp án và allow_ai_solve=true, tự giải ngắn gọn, dùng answer_source=\"ai_solved\", có explanation, needs_review=true, review_reason chứa ai_solved_answer. Nếu không chắc, answer_source=\"missing\" và needs_review=true.
Với reading/cloze, copy passage_text vào từng câu liên quan và dùng cùng passage_id.
Schema: {\"file_summary\":{\"detected_formats\":[],\"detected_subtopics\":[],\"exam_profiles\":[],\"confidence\":\"high|medium|low\",\"review_reason\":\"\"},\"questions\":[{\"question_number\":1,\"page_start\":1,\"page_end\":1,\"question_text\":\"...\",\"options\":{\"A\":\"...\",\"B\":\"...\",\"C\":\"...\",\"D\":\"...\"},\"correct_answer\":\"A|null|string\",\"answer_source\":\"pdf_key|ai_solved|missing\",\"explanation\":\"\",\"passage_id\":\"p1|null\",\"passage_text\":\"...|null\",\"question_format\":\"...\",\"knowledge_subtopic_code_v2\":\"...\",\"exam_profiles\":[\"...\"],\"difficulty\":\"foundation|basic|intermediate|advanced|exam\",\"confidence\":\"high|medium|low\",\"needs_review\":true,\"review_reason\":\"\"}]}"""

FORMAT_RULES = [
    (r"PRESS RELEASE", "thpt_press_release_cloze", "E2C.04"),
    (r"ADVERTISEMENT|QUẢNG CÁO|THÔNG BÁO|TỜ RƠI", "thpt_advertisement_cloze", "E2C.03"),
    (r"ĐỌC ĐIỀN|ĐIỀN KHUYẾT|CLOZE|NUMBERED BLANKS", "hsa_cloze_text", "E2C.05"),
    (r"TEXT COMPLETION|ĐIỀN CÂU|ĐIỀN ĐOẠN", "thpt_text_completion", "E2C.06"),
    (r"SẮP XẾP.*HỘI THOẠI|DIALOGUE.*ARRANG", "hsa_dialogue_arrangement", "E2O.01"),
    (r"SẮP XẾP|ARRANGEMENT|ORDERING|LÁ THƯ|ĐOẠN VĂN", "thpt_arrangement_text", "E2O.02"),
    (r"DIALOGUE COMPLETION|HOÀN THÀNH HỘI THOẠI", "hsa_dialogue_completion", "E2F.01"),
    (r"PARAPHRA", "thpt_reading_passage", "E2R.07"),
    (r"SUMMARY|TÓM TẮT", "thpt_reading_passage", "E2R.08"),
    (r"SUY LUẬN|INFERENCE|LINEAR THINKING", "thpt_reading_passage", "E2R.05"),
    (r"QUY CHIẾU|REFERENCE", "thpt_reading_passage", "E2R.03"),
    (r"ĐỌC HIỂU|READING|PASSAGE", "thpt_reading_passage", "E2R.02"),
    (r"ĐỒNG NGHĨA|SYNONYM", "hsa_synonym", "E2X.05"),
    (r"TRÁI NGHĨA|ANTONYM|OPPOSITE", "hsa_antonym", "E2X.05"),
    (r"WORD FORMATION|CẤU TẠO TỪ|TỪ LOẠI|DANH TỪ|TÍNH TỪ|TRẠNG TỪ", "spt_word_formation", "E2X.01"),
    (r"COLLOCATION", "hsa_sentence_completion", "E2X.03"),
    (r"VIẾT LẠI CÂU|SENTENCE REWRITING", "hsa_sentence_rewriting", "E2W.01"),
    (r"KẾT HỢP CÂU|SENTENCE COMBINATION", "hsa_sentence_combination", "E2W.02"),
    (r"ĐOẠN VĂN|PARAGRAPH WRITING|WRITING", "spt_paragraph_writing", "E2W.03"),
]

PASSAGE_FORMATS = {
    "thpt_reading_passage", "hsa_reading_comprehension", "spt_reading",
    "hsa_cloze_text", "spt_cloze", "thpt_advertisement_cloze",
    "thpt_press_release_cloze", "thpt_text_completion",
}
OPTIONAL_CONTEXT_SUBTOPICS = {"E2R.07"}
NO_OPTION_FORMATS = {
    "spt_paragraph_writing",
    "hsa_sentence_rewriting",
    "hsa_sentence_combination",
    "spt_word_formation",
}
NO_OPTION_ITEM_TYPES = {
    "fill_blank",
    "transform_sentence",
    "open_response",
    "error_correction",
    "true_false",
}

PASSAGE_START_RE = re.compile(
    r"(?is)(read the following (?:passage|text|advertisement|announcement|notice)|"
    r"questions?\s+from\s+\d+\s+to\s+\d+)"
)
NUMBERED_PASSAGE_START_RE = re.compile(r"(?ms)(?:^|\n)\s*(\d{1,2})\s*[\.)]\s+([A-Z][^\n]{30,})")

KNOWLEDGE_RULES = [
    (r"TỪ LOẠI|DANH TỪ|TÍNH TỪ|TRẠNG TỪ|PARTS? OF SPEECH", "E2G.01"),
    (r"MẠO TỪ|TỪ HẠN ĐỊNH|ARTICLE|DETERMINER", "E2G.02"),
    (r"ĐẠI TỪ|LƯỢNG TỪ|QUANTIFIER|PRONOUN", "E2G.03"),
    (r"GIỚI TỪ|PREPOSITION", "E2G.04"),
    (r"CỤM ĐỘNG TỪ|PHRASAL", "E2G.05"),
    (r"CẤP SO SÁNH|COMPAR", "E2G.06"),
    (r"HIỆN TẠI HOÀN THÀNH|HIỆN TẠI TIẾP DIỄN|HIỆN TẠI ĐƠN|PRESENT", "E2V.01"),
    (r"QUÁ KHỨ HOÀN THÀNH|QUÁ KHỨ ĐƠN|QUÁ KHỨ TIẾP DIỄN|PAST", "E2V.02"),
    (r"TƯƠNG LAI|FUTURE", "E2V.03"),
    (r"HÒA HỢP GIỮA CÁC THÌ|PHỐI THÌ|SEQUENCE", "E2V.04"),
    (r"HÒA HỢP GIỮA\s+S\s*[-–/]?\s*V|SỰ HÒA HỢP.*CHỦ NGỮ.*ĐỘNG TỪ|SUBJECT", "E2V.05"),
    (r"ĐỘNG TỪ KHUYẾT THIẾU|KHUYẾT THIẾU|MODAL", "E2V.06"),
    (r"DANH ĐỘNG TỪ|ĐỘNG TỪ NGUYÊN MẪU|V-ING|TO V|GERUND|INFINITIVE", "E2V.07"),
    (r"PHÂN TỪ|PARTICIPLE", "E2V.08"),
    (r"CÂU BỊ ĐỘNG|BỊ ĐỘNG|PASSIVE", "E2S.01"),
    (r"CÂU GIÁN TIẾP|GIÁN TIẾP|REPORTED", "E2S.02"),
    (r"CÂU ĐIỀU KIỆN|ĐIỀU ƯỚC|GIẢ ĐỊNH|CONDITIONAL|WISH", "E2S.03"),
    (r"MỆNH ĐỀ QUAN HỆ|MENHDEQUANHE|RELATIVE", "E2S.04"),
    (r"RÚT GỌN MỆNH ĐỀ QUAN HỆ", "E2S.05"),
    (r"LIÊN TỪ|MỆNH ĐỀ TRẠNG NGỮ|TRẠNG TỪ LIÊN KẾT|ADVERBIAL|CONJUNCTION", "E2S.06"),
    (r"CÂU HỎI ĐUÔI|QUESTION TAG", "E2S.07"),
    (r"CÂU CHẺ|CLEFT", "E2S.08"),
    (r"ĐẢO NGỮ|INVERSION", "E2S.09"),
    (r"CÁC LOẠI CÂU|SENTENCE TYPES", "E2S.10"),
    (r"NGỮ ĐỒNG VỊ|YẾU TỐ CHÈN|PHÉP SONG HÀNH|APPOSITION|PARALLEL", "E2S.11"),
    (r"WORD FORMATION|CẤU TẠO TỪ", "E2X.01"),
    (r"TRẬT TỰ TỪ|WORD ORDER", "E2X.02"),
    (r"COLLOCATION", "E2X.03"),
    (r"CẤU TRÚC|FIXED EXPRESSION|PHRASAL PATTERN", "E2X.04"),
    (r"ĐỒNG NGHĨA|TRÁI NGHĨA|SYNONYM|ANTONYM", "E2X.05"),
    (r"TỪ VỰNG TRỌNG ĐIỂM|TOPIC VOCAB|CHỦ ĐỀ", "E2X.07"),
    (r"TRƯỜNG NGHĨA|SEMANTIC", "E2X.08"),
    (r"MAIN IDEA|TITLE|TIÊU ĐỀ|Ý CHÍNH", "E2R.01"),
    (r"THÔNG TIN CHI TIẾT|DETAIL|NOT MENTIONED|EXCEPT|TRUE", "E2R.02"),
    (r"QUY CHIẾU|REFERENCE", "E2R.03"),
    (r"TỪ.*TRONG.*BÀI ĐỌC|VOCABULARY IN CONTEXT", "E2R.04"),
    (r"SUY LUẬN|INFERENCE|LINEAR THINKING", "E2R.05"),
    (r"ĐOẠN CHỨA|SENTENCE INSERTION|PARAGRAPH LOCATION", "E2R.06"),
    (r"PARAPHRA", "E2R.07"),
    (r"SUMMARY|TÓM TẮT", "E2R.08"),
    (r"ĐỌC ĐIỀN|ĐIỀN KHUYẾT|CLOZE", "E2C.05"),
    (r"QUẢNG CÁO|THÔNG BÁO|TỜ RƠI|ADVERTISEMENT|NOTICE", "E2C.03"),
    (r"SẮP XẾP.*HỘI THOẠI|DIALOGUE.*ARRANG", "E2O.01"),
    (r"SẮP XẾP.*ĐOẠN|PARAGRAPH ORDER", "E2O.02"),
    (r"LÁ THƯ|EMAIL|LETTER", "E2O.03"),
    (r"VIẾT LẠI CÂU|SENTENCE REWRITING", "E2W.01"),
    (r"GHÉP CÂU|KẾT HỢP CÂU|SENTENCE COMBINATION", "E2W.02"),
    (r"ĐOẠN VĂN|PARAGRAPH WRITING", "E2W.03"),
    (r"HOÀN THÀNH.*HỘI THOẠI|DIALOGUE COMPLETION", "E2F.01"),
    (r"TƯ DUY LOGIC|GIẢI QUYẾT VẤN ĐỀ|PROBLEM SOLVING|LOGICAL", "E2M.01"),
    (r"VIP90|TUẦN", "E2M.03"),
]




def ordered_unique(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


def topic_key(file_info: dict[str, Any]) -> str:
    folder = file_info.get("folder_path") or ""
    parts = folder.split("/")
    if len(parts) >= 3:
        return "/".join(parts[:3])
    return folder or file_info.get("subsection") or file_info.get("section") or "unknown"


def sample_per_topic(files: list[dict[str, Any]], per_topic: int) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    selected = []
    for item in files:
        key = topic_key(item)
        count = counts.get(key, 0)
        if count < per_topic:
            selected.append(item)
            counts[key] = count + 1
    return selected


def ready_for_ai_solve(question: dict[str, Any]) -> bool:
    text = (question.get("question_text") or "").strip()
    options = question.get("options") or {}
    if len(text) < 20:
        return False
    fmt = question.get("question_format") or ""
    item_type = question.get("practice_item_type") or (question.get("raw_extract") or {}).get("practice_item_type")
    if fmt in NO_OPTION_FORMATS or item_type in NO_OPTION_ITEM_TYPES:
        return True
    return len(options) >= 2


def rejection_reasons(question: dict[str, Any]) -> list[str]:
    reasons = []
    text = (question.get("question_text") or "").strip()
    options = question.get("options") or {}
    fmt = question.get("question_format") or ""
    if len(text) < 20:
        reasons.append("missing_or_short_question_text")
    code = question.get("knowledge_subtopic_code_v2") or ""
    if fmt in PASSAGE_FORMATS and code not in OPTIONAL_CONTEXT_SUBTOPICS and not question.get("passage_text"):
        reasons.append("missing_required_context")
    item_type = question.get("practice_item_type") or (question.get("raw_extract") or {}).get("practice_item_type")
    if fmt not in NO_OPTION_FORMATS and item_type not in NO_OPTION_ITEM_TYPES and len(options) < 2:
        reasons.append("missing_options")
    if not question.get("knowledge_subtopic_code_v2"):
        reasons.append("missing_taxonomy")
    if not question.get("question_format"):
        reasons.append("missing_question_format")
    return reasons

def is_accepted_question(question: dict[str, Any]) -> bool:
    return not rejection_reasons(question)

def split_questions(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted = []
    rejected = []
    for result in results:
        for q in result.get("questions", []):
            reasons = rejection_reasons(q)
            if reasons:
                rejected_item = dict(q)
                rejected_item["rejected_reason"] = "; ".join(reasons)
                rejected.append(rejected_item)
            else:
                accepted.append(q)
    return accepted, rejected

def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def normalize_base_url(value: str) -> str:
    value = value.rstrip("/")
    return value[:-3] if value.endswith("/v1") else value


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return {}


def is_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    return status in {408, 409, 429, 500, 502, 503, 504} or any(
        token in text for token in ["429", "rate_limit", "rate limit", "timeout", "overloaded", "temporarily"]
    )


def reset_after_seconds(exc: Exception) -> float | None:
    text = str(exc).lower()
    match = re.search(r"reset after\s+(?:(\d+)m\s*)?(\d+)?s?", text)
    if not match:
        return None
    total = int(match.group(1) or 0) * 60 + int(match.group(2) or 0)
    return float(total + 10) if total else None


def ai_call_with_retry(label: str, fn):
    last_exc: Exception | None = None
    for attempt in range(1, AI_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= AI_RETRIES or not is_retryable(exc):
                raise
            wait = reset_after_seconds(exc) or (AI_RETRY_BASE_SECONDS * attempt)
            print(f"  -> {label} retry {attempt}/{AI_RETRIES} after {wait:.0f}s: {exc}", flush=True)
            time.sleep(wait)
    if last_exc:
        raise last_exc
    raise RuntimeError(f"{label} failed without exception")


def read_pdf_text(path: Path) -> tuple[str, int, int]:
    try:
        with fitz.open(path) as doc:
            pages = []
            for index, page in enumerate(doc):
                if index >= MAX_PAGES:
                    break
                text = page.get_text("text", sort=True).strip()
                if text:
                    pages.append(f"[Page {index + 1}]\n{text}")
            full = "\n\n".join(pages)
            limit = VIP90_BUNDLE_MAX_CHARS if "Tài liệu đầy đủ Tuần" in path.name else MAX_CHARS
            return full[:limit], len(doc), len(full)
    except Exception as exc:
        return f"[PDF_READ_ERROR: {exc}]", 0, 0


def infer_exam_profiles(text: str, formats: list[str]) -> list[str]:
    profiles = set()
    up = text.upper()
    if "HSA" in up or "ĐÁNH GIÁ NĂNG LỰC" in up or "DGNL" in up:
        profiles.add("HSA_ENGLISH")
    if "SPT" in up or "SƯ PHẠM" in up:
        profiles.add("SPT_ENGLISH")
    for fmt in formats:
        if fmt.startswith("hsa_"):
            profiles.add("HSA_ENGLISH")
        elif fmt.startswith("spt_"):
            profiles.add("SPT_ENGLISH")
        elif fmt.startswith("thpt_"):
            profiles.add("THPT_2025_CORE")
    return sorted(profiles) or ["THPT_2025_CORE"]


def make_hints(text: str, file_info: dict[str, Any], page_count: int, char_count: int) -> dict[str, Any]:
    path_context = " ".join([file_info.get("relative_path", ""), file_info.get("folder_path", ""), file_info.get("file_name", "")]).upper()
    full_context = f"{path_context} {text[:5000]}".upper()
    likely_formats = []
    likely_subtopics = []
    for context in (path_context, full_context):
        for pattern, fmt, code in FORMAT_RULES:
            if re.search(pattern, context):
                likely_formats.append(fmt)
                likely_subtopics.append(code)
        if likely_formats:
            break
    for context in (path_context, full_context):
        for pattern, code in KNOWLEDGE_RULES:
            if re.search(pattern, context):
                likely_subtopics.insert(0, code)
        if likely_subtopics:
            break
    likely_formats = ordered_unique(likely_formats)
    likely_subtopics = ordered_unique(likely_subtopics)
    return {
        "page_count": page_count,
        "char_count": char_count,
        "question_marker_count": len(re.findall(r"(?im)^\s*(?:Question|Câu)\s*\d+\s*[\.:)]", text)),
        "numbered_item_count": len(re.findall(r"(?m)^\s*\d{1,3}\s*[\.)]", text)),
        "option_a_count": len(re.findall(r"(?im)(?:^|\n|\s)A\s*[\.)]\s+", text)),
        "blank_count": len(re.findall(r"\(\s*\d{1,3}\s*\)|_{3,}", text)),
        "answer_key_hits": len(re.findall(r"(?i)(đáp án|answer\s*key|\bkey\b|answers?)", text)),
        "likely_formats": likely_formats,
        "likely_subtopics": likely_subtopics,
        "likely_exam_profiles": infer_exam_profiles(full_context, likely_formats),
        "text_quality": "low" if char_count < 800 or "PDF_READ_ERROR" in text else "ok",
    }


def build_node_index(nodes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_code: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        code = node.get("knowledge_subtopic_code_v2")
        if code:
            by_code.setdefault(code, []).append(node)
    return by_code


def linked_nodes(question: dict[str, Any], file_info: dict[str, Any], node_index: dict[str, list[dict[str, Any]]]) -> list[str]:
    code = question.get("knowledge_subtopic_code_v2")
    candidates = list(node_index.get(code, []))
    fmt = question.get("question_format")
    if fmt:
        candidates = [n for n in candidates if fmt in (n.get("question_formats") or [])] or candidates
    same_folder = [n for n in candidates if n.get("subsection") and n.get("subsection") == file_info.get("subsection")]
    candidates = same_folder or candidates
    return sorted({n.get("node_code_v2") or n.get("node_code") for n in candidates if n.get("node_code_v2") or n.get("node_code")})[:5]




BOILERPLATE_LINE_RE = re.compile(
    r"(?ix)^\s*(?:"
    r"\[?page\s*\d+\]?|"
    r"shared\s+by\b.*|"
    r"fanpage\b.*|"
    r"đăng\s+k[ýy]\b.*|"
    r"v[ìi]\s+quyền\s+lợi\b.*|"
    r"(?:tài\s+)?liệu\s+độc\s+quyền\b.*|"
    r"độc\s+quyền\b.*|"
    r"biên\s+soạn\b.*|"
    r"cô\s+vũ\s+thị\s+mai\s+phương\b.*|"
    r"cô\s+mai\s+phương\b.*|"
    r"mai\s+phương\b.*|"
    r".*\bmai\s+phương\b.*|"
    r"ngoaingu24h\.vn\b.*|"
    r".*\bngoaingu24h\.vn\b.*|"
    r"tienganhcomaiphuong\.vn\b.*|"
    r".*\btienganhcomaiphuong\.vn\b.*|"
    r"chinh\s+phục\s+k[ìi]\s+thi\b.*|"
    r"pro\s*3m\b.*|pro3m\b.*|pro\s*3mplus\b.*|"
    r"dành\s+riêng\s+cho\s+khóa\s+học\b.*|"
    r"độc\s+quyền\s+và\s+duy\s+nhất\b.*|"
    r"nền\s+tảng\s+từ\s+vựng\b.*|"
    r"ôn\s+luyện\s+các\s+dạng\s+câu\s+hỏi\b.*"
    r")\s*$"
)

def strip_boilerplate_text(value: str) -> str:
    kept = []
    for raw_line in value.splitlines():
        line = re.sub(r"^\s*\[Page\s+\d+\]\s*", "", raw_line).strip()
        if not line:
            continue
        if BOILERPLATE_LINE_RE.match(line):
            continue
        if re.search(r"(?i)questions?\s+from\s+\d+\s+to\s+\d+", line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()

def clean_passage_text(value: str) -> str:
    text = re.sub(r"\s+", " ", strip_boilerplate_text(value)).strip()
    text = re.sub(r"(?is)^.*?(From a poor British colony|Ben Silbermann was|Artificial intelligence prompting|Improve Your Writing Skills|Discover the Magic|School Announcement|Information about|Charity Concert)", r"\1", text)
    return text[:12000]


def passage_id_for(sha1: str, passage_text: str, index: int) -> str | None:
    if not passage_text or len(passage_text) < 120:
        return None
    digest = hashlib.sha1(passage_text[:1000].encode("utf-8")).hexdigest()[:10]
    return f"passage-{sha1[:10]}-{index:02d}-{digest}"

QUESTION_MARKER_RE = re.compile(r"(?im)^\s*(?:Question|Câu)\s+(\d+)\s*[\.:)]?\s*")
NUMBERED_CONTEXT_RE = re.compile(r"(?m)^\s*(\d{1,3})\s*[\.)]\s*(?![A-D]\s*[\.)])")
OPTION_RE = re.compile(r"(?ms)(?:^|\n|\s)([A-D])\s*[\.)]\s+(.+?)(?=(?:\n|\s)[A-D]\s*[\.)]\s+|$)")
SECTION_NOISE_RE = re.compile(
    r"(?im)^(?:Shared By|PRO\s*3M|PRO3M|Biên soạn|Độc quyền|Đăng Ký|Vì quyền lợi|TÀI LIỆU|THI ONLINE|CHỦ ĐỀ|Nền tảng|Cô Vũ|Ngoaingu24h|Tienganhcomaiphuong).*$"
)

SEGMENT_START_RE = re.compile(
    r"(?im)^\s*(?:"
    r"(?:Read the following (?:passage|text|texts|advertisement|announcement|notice).*)|"
    r"(?:Choose .*?(?:CLOSEST|OPPOSITE|SYNONYM|ANTONYM).*)|"
    r"(?:Chọn đáp án đúng để hoàn thành đoạn văn.*)|"
    r"(?:Mark the letter .*? numbered blanks.*)|"
    r"(?:Choose A, B, C or D .*?(?:underlined|meaning).*)|"
    r"(?:Write (?:a|an|the).*)"
    r")$"
)

def clean_segment_text(value: str) -> str:
    value = strip_boilerplate_text(SECTION_NOISE_RE.sub("", value))
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()

def page_span(value: str) -> tuple[int | None, int | None]:
    pages = [int(p) for p in re.findall(r"\[Page\s+(\d+)\]", value)]
    if not pages:
        return None, None
    return min(pages), max(pages)

def page_at_offset(text: str, offset: int) -> int | None:
    pages = list(re.finditer(r"\[Page\s+(\d+)\]", text[: max(0, offset) + 1]))
    if not pages:
        return None
    return int(pages[-1].group(1))

def looks_like_question_block(value: str) -> bool:
    return bool(QUESTION_MARKER_RE.search(value) and len(OPTION_RE.findall(value)) >= 2)

def split_question_block_tail(block: str) -> tuple[str, str]:
    options = list(OPTION_RE.finditer(block))
    if not options:
        return block.strip(), ""
    last = options[-1]
    trailing = block[last.end():].strip()
    if re.match(r"(?is)^(?:\[Page\s+\d+\]\s*)?(?:\d{1,3}\s*[\.)]\s+|Read the following|Choose A, B, C or D|Chọn đáp án đúng|Mark the letter)", trailing):
        return block[: last.end()].strip(), trailing
    option_value = last.group(2)
    tail_match = re.search(
        r"(?ms)(\n\s*(?:\[Page\s+\d+\]\s*)?(?:\d{1,3}\s*[\.)]\s+|Read the following|Choose A, B, C or D|Chọn đáp án đúng|Mark the letter).*)$",
        option_value,
    )
    if not tail_match:
        return block.strip(), ""
    tail_in_option = tail_match.group(1).strip()
    split_at = last.start(2) + tail_match.start(1)
    return block[:split_at].strip(), block[split_at:].strip()

def blank_context_for_question(text: str, marker_start: int, qnum: int) -> str:
    """Return the numbered passage/sentence containing blank (qnum), if present.

    Some collocation/cloze PDFs put the real stem in a numbered paragraph above,
    while each ``Question N`` block contains only A-D options. In that layout the
    question text must be recovered from the paragraph that owns blank ``(N)``.
    """
    before = text[:marker_start]
    blank_matches = list(re.finditer(rf"\(\s*{qnum}\s*\)", before))
    if not blank_matches:
        return ""
    blank = blank_matches[-1]
    window_start = max(0, blank.start() - 2600)
    window = before[window_start:]
    paragraph_starts = list(re.finditer(r"(?m)^\s*(?:\[Page\s+\d+\]\s*)?\d{1,3}\s*[\.)]\s+", window))
    if paragraph_starts:
        start = window_start + paragraph_starts[-1].start()
    else:
        double_break = before.rfind("\n\n", 0, blank.start())
        start = double_break + 2 if double_break >= 0 else window_start
    candidate = before[start:marker_start]
    cloze_starts = list(re.finditer(r"(?is)Read the following (?:passage|text).*?numbered blanks? from\s+\d+\s+to\s+\d+\s*\.", candidate))
    if cloze_starts:
        candidate = candidate[cloze_starts[-1].start():]
    first_question = re.search(r"(?im)^\s*(?:Question|Câu)\s+\d+\s*[\.:)]", candidate)
    if first_question:
        candidate = candidate[: first_question.start()]
    next_numbered = re.search(r"(?m)\n\s*\d{1,3}\s*[\.)]\s+", candidate)
    if next_numbered and next_numbered.start() > 80 and f"({qnum})" not in candidate[: next_numbered.start()]:
        candidate = candidate[next_numbered.start():]
    candidate = clean_question_text(candidate)
    if re.search(rf"\(\s*{qnum}\s*\)", candidate) and len(candidate) >= 40:
        return candidate
    return ""

def classify_segment(context_text: str, question_blocks: list[str], file_info: dict[str, Any], hints: dict[str, Any]) -> tuple[str, str]:
    context_up = f"{file_info.get('relative_path','')}\n{context_text}".upper()
    q_join = "\n".join(question_blocks[:5]).upper()
    blank_count = len(re.findall(r"\(\s*\d{1,3}\s*\)|_{3,}", context_text))
    if re.search(r"WRITE\s+(?:A|AN|THE)|PARAGRAPH WRITING|VIẾT.*ĐOẠN", context_up):
        return "writing_prompt", "rule_high"
    if re.search(r"SẮP XẾP|ARRANG|ORDER", context_up + q_join):
        return "arrangement_group", "rule_high"
    if blank_count >= 2 and question_blocks:
        return "cloze_group", "rule_high"
    if re.search(r"CLOSEST|OPPOSITE|SYNONYM|ANTONYM|ĐỒNG NGHĨA|TRÁI NGHĨA", context_up + q_join):
        if len(context_text) >= 120 and len(question_blocks) >= 2:
            return "vocab_context_group", "rule_high"
        return "standalone_mcq", "rule_high"
    if re.search(r"READ THE FOLLOWING (?:PASSAGE|TEXT|TEXTS)|QUESTIONS?\s+FROM\s+\d+\s+TO\s+\d+", context_up):
        return "reading_group", "rule_high" if len(context_text) >= 180 else "rule_low"
    if len(question_blocks) == 1 and not context_text:
        return "standalone_mcq", "rule_high"
    if question_blocks:
        return "standalone_mcq" if len(context_text) < 80 else "reading_group", "rule_low"
    return "unknown", "rule_low"

def make_segment(
    file_info: dict[str, Any],
    sha1: str,
    index: int,
    context_text: str,
    question_blocks: list[str],
    hints: dict[str, Any],
) -> dict[str, Any]:
    context_text = clean_segment_text(context_text)
    question_blocks = [clean_segment_text(block) for block in question_blocks if clean_segment_text(block)]
    seg_type, confidence = classify_segment(context_text, question_blocks, file_info, hints)
    page_start, page_end = page_span("\n".join([context_text, *question_blocks]))
    basis = f"{sha1}:{index}:{seg_type}:{context_text[:80]}:{len(question_blocks)}"
    segment_id = f"seg-{sha1[:10]}-{index:03d}-{hashlib.sha1(basis.encode('utf-8')).hexdigest()[:8]}"
    question_numbers = []
    for block in question_blocks:
        match = QUESTION_MARKER_RE.search(block)
        if match:
            question_numbers.append(int(match.group(1)))
    return {
        "segment_id": segment_id,
        "source_file": file_info.get("file_name"),
        "relative_path": file_info.get("relative_path"),
        "file_sha1": sha1,
        "segment_index": index,
        "segment_type": seg_type,
        "context_text": context_text,
        "question_blocks": question_blocks,
        "question_numbers": question_numbers,
        "question_count": len(question_blocks),
        "page_start": page_start,
        "page_end": page_end,
        "confidence": confidence,
    }

def segment_practice_text(text: str, file_info: dict[str, Any], sha1: str, hints: dict[str, Any]) -> list[dict[str, Any]]:
    markers = list(QUESTION_MARKER_RE.finditer(text))
    if not markers:
        context = clean_segment_text(text)
        return [make_segment(file_info, sha1, 1, context, [], hints)] if context else []
    segments: list[dict[str, Any]] = []
    pending_context = text[: markers[0].start()].strip()
    current_questions: list[str] = []
    seg_index = 1
    for idx, marker in enumerate(markers):
        start = marker.start()
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(text)
        raw_block = text[start:end].strip()
        block, tail = split_question_block_tail(raw_block)
        before_marker = text[markers[idx - 1].end():start] if idx > 0 else text[:start]
        starts_new_context = bool(SEGMENT_START_RE.search(before_marker))
        if starts_new_context and current_questions:
            segments.append(make_segment(file_info, sha1, seg_index, pending_context, current_questions, hints))
            seg_index += 1
            pending_context = ""
            current_questions = []
        current_questions.append(block)
        if tail:
            segments.append(make_segment(file_info, sha1, seg_index, pending_context, current_questions, hints))
            seg_index += 1
            pending_context = tail
            current_questions = []
    if current_questions or pending_context:
        segments.append(make_segment(file_info, sha1, seg_index, pending_context, current_questions, hints))
    return [seg for seg in segments if seg.get("context_text") or seg.get("question_blocks")]


def extract_passage_candidate(segment: str) -> str:
    matches = list(PASSAGE_START_RE.finditer(segment))
    if matches:
        segment = segment[matches[-1].start():]
    numbered_matches = list(NUMBERED_PASSAGE_START_RE.finditer(segment))
    if numbered_matches:
        segment = segment[numbered_matches[-1].start():]
    passage = clean_passage_text(segment)
    if re.search(r"(?i)which of the following options best paraphrases the original sentences", passage):
        return ""
    return passage

SECTION_HEADING_RE = re.compile(
    r"(?im)^\s*(?:(?:[IVX]{1,6})\s*[\.)]\s+[^\n]{4,}|EXERCISE\s+\d+\s*:?[^\n]*)"
)
NUMBERED_ITEM_RE = re.compile(r"(?m)^\s*(\d{1,3})\s*[\.)]\s+(.+?)(?=\n\s*\d{1,3}\s*[\.)]\s+|\n\s*(?:[IVX]{1,6}\s*[\.)]|EXERCISE\s+\d+)|\Z)", re.S)

def clean_question_text(value: str) -> str:
    value = strip_boilerplate_text(SECTION_NOISE_RE.sub("", value))
    value = re.sub(r"\[Page\s+\d+\]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value

def truncate_at_boilerplate(value: str) -> str:
    lines = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if line and (BOILERPLATE_LINE_RE.match(line) or SECTION_NOISE_RE.match(line)):
            break
        lines.append(raw_line)
    return "\n".join(lines).strip()

def section_ranges(text: str) -> list[tuple[str, int, int]]:
    matches = list(SECTION_HEADING_RE.finditer(text))
    ranges = []
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        title = clean_question_text(match.group(0))
        ranges.append((title, match.end(), end))
    return ranges

def infer_item_type(section_title: str, block: str, options: dict[str, str]) -> str:
    up = f"{section_title}\n{block}".upper()
    if re.search(r"TRUE\s*\(T\)|FALSE\s*\(F\)|ĐÚNG\s*/\s*SAI|ĐÚNG\s+HAY\s+SAI", up):
        return "true_false"
    if re.search(r"CHỈ RA LỖI|LỖI SAI|ERROR", up):
        return "error_correction"
    if re.search(r"CHO DẠNG|DẠNG.*ĐỘNG TỪ|WORD FORM|TỪ LOẠI", up):
        return "fill_blank"
    if not options and re.search(r"_{3,}.*\([A-Za-z][A-Za-z\s/-]{1,40}\)|\([A-Za-z][A-Za-z\s/-]{1,40}\).*_{3,}", block):
        return "fill_blank"
    if re.search(r"CÂU BỊ ĐỘNG|BỊ ĐỘNG|VIẾT LẠI|CHUYỂN|REWRITE|SENTENCE TRANSFORMATION", up):
        return "transform_sentence"
    if options:
        return "mcq"
    return "open_response"

def refine_subtopic_by_source(code: str, file_info: dict[str, Any], qtext: str) -> str:
    context = " ".join([
        file_info.get("relative_path", ""),
        file_info.get("folder_path", ""),
        file_info.get("file_name", ""),
        qtext[:240],
    ]).upper()
    if "THÌ HIỆN TẠI" in context:
        return "E2V.01"
    if "THÌ QUÁ KHỨ" in context:
        return "E2V.02"
    if "THÌ TƯƠNG LAI" in context or "TƯƠNG LAI ĐƠN" in context or "TƯƠNG LAI GẦN" in context or "TƯƠNG LAI TIẾP DIỄN" in context or "TƯƠNG LAI HOÀN THÀNH" in context:
        return "E2V.03"
    if "TRẬT TỰ TỪ" in context or "WORD ORDER" in context:
        return "E2X.02"
    if "TỪ, CỤM TỪ VẬN DỤNG CAO" in context or "TỪ - CỤM TỪ VẬN DỤNG CAO" in context:
        return "E2X.04"
    if "ĐỒNG NGHĨA - TRÁI NGHĨA THEO NGỮ CẢNH" in context:
        return "E2X.06"
    return code

def refine_subtopic_by_question_text(code: str, fmt: str, qtext: str) -> str:
    text = qtext.lower()
    if fmt == "thpt_reading_passage" or code.startswith("E2R"):
        if re.search(r"main idea|mainly about|best title|central idea|primary purpose|đầu đề|tiêu đề|ý chính", text):
            return "E2R.01"
        if re.search(r"closest in meaning|opposite in meaning|word .{0,40} means|synonym|antonym|từ .{0,40} nghĩa", text):
            return "E2R.04"
        if re.search(r"refers to|reference|the word .{0,40} refers|quy chiếu", text):
            return "E2R.03"
        if re.search(r"which paragraph|where .{0,80} sentence|best fits|insert|đoạn nào", text):
            return "E2R.06"
        if re.search(r"paraphrase|closest in meaning to the sentence|best paraphrases|restatement", text):
            return "E2R.07"
        if re.search(r"summari[sz]e|summary|tóm tắt", text):
            return "E2R.08"
        if re.search(r"tone|attitude|author'?s purpose|author .{0,40} feel|giọng điệu|thái độ", text):
            return "E2R.09"
        if re.search(r"infer|imply|suggest|probably|can be inferred|suy luận", text):
            return "E2R.05"
    return code

def format_for_item_type(item_type: str, default_format: str, default_code: str) -> str:
    if item_type == "mcq" and default_format in PASSAGE_FORMATS and not default_code.startswith(("E2R", "E2C")):
        return "hsa_sentence_completion"
    if item_type == "transform_sentence":
        return "hsa_sentence_rewriting"
    if item_type == "fill_blank":
        return "spt_word_formation" if default_code == "E2X.01" else "hsa_sentence_completion"
    if item_type in {"open_response", "error_correction"} and default_format in PASSAGE_FORMATS:
        return "hsa_sentence_completion"
    return default_format

def make_regex_item(
    file_info: dict[str, Any],
    sha1: str,
    index: int,
    qnum: int,
    qtext: str,
    options: dict[str, str],
    answer: str | None,
    default_format: str,
    default_code: str,
    hints: dict[str, Any],
    taxonomy: dict[str, Any],
    node_index: dict[str, list[dict[str, Any]]],
    raw_extract: dict[str, Any],
    passage_text: str = "",
    passage_id: str | None = None,
) -> dict[str, Any]:
    valid_formats = {f["format_code"] for f in taxonomy["question_formats"]}
    valid_subtopics = {st["subtopic_code"] for st in taxonomy["knowledge_subtopics"]}
    item_type = raw_extract.get("practice_item_type") or "mcq"
    fmt = format_for_item_type(item_type, default_format, default_code)
    if fmt not in valid_formats:
        fmt = "hsa_sentence_completion"
    code = default_code if default_code in valid_subtopics else "E2M.99"
    code = refine_subtopic_by_source(code, file_info, qtext)
    code = refine_subtopic_by_question_text(code, fmt, qtext)
    if code not in valid_subtopics:
        code = default_code if default_code in valid_subtopics else "E2M.99"
    review_bits = []
    if not answer:
        review_bits.append("regex_fallback_missing_answer")
    if not qtext:
        review_bits.append("regex_fallback_missing_question_text")
    if item_type != "mcq":
        review_bits.append(f"practice_item_type:{item_type}")
    raw_extract = dict(raw_extract)
    if raw_extract.get("block"):
        raw_extract["block"] = truncate_at_boilerplate(str(raw_extract.get("block") or ""))[:3000]
    if raw_extract.get("section_title"):
        raw_extract["section_title"] = clean_question_text(str(raw_extract.get("section_title") or ""))
    page_start, page_end = page_span("\n".join(str(part or "") for part in [raw_extract.get("block"), passage_text]))
    if page_start is None:
        page_start = raw_extract.get("page_start")
    if page_end is None:
        page_end = raw_extract.get("page_end") or page_start
    item = {
        "question_id": stable_question_id(sha1, index, {"question_number": qnum, "question_text": qtext}, file_info.get("relative_path", "")),
        "source_file": file_info.get("file_name"),
        "relative_path": file_info.get("relative_path"),
        "file_sha1": sha1,
        "question_number": qnum,
        "page_start": page_start,
        "page_end": page_end,
        "question_text": qtext[:3000],
        "options": options,
        "correct_answer": answer,
        "answer_source": "pdf_key" if answer else "missing",
        "explanation": "",
        "passage_id": passage_id if fmt in PASSAGE_FORMATS else None,
        "passage_text": passage_text if fmt in PASSAGE_FORMATS and passage_id else None,
        "question_format": fmt,
        "knowledge_subtopic_code_v2": code,
        "exam_profiles": hints.get("likely_exam_profiles") or ["THPT_2025_CORE"],
        "linked_node_codes_v2": [],
        "difficulty": "basic",
        "confidence": "medium" if (options or item_type != "mcq") and qtext else "low",
        "needs_review": bool(review_bits),
        "review_reason": "; ".join(review_bits) if review_bits else "regex_fallback",
        "ready_for_ai_solve": False,
        "ai_model": "regex_fallback",
        "practice_item_type": item_type,
        "raw_extract": raw_extract,
    }
    item["ready_for_ai_solve"] = ready_for_ai_solve(item)
    item["linked_node_codes_v2"] = linked_nodes(item, file_info, node_index)
    return item

def extract_numbered_layout_questions(
    text: str,
    file_info: dict[str, Any],
    sha1: str,
    hints: dict[str, Any],
    taxonomy: dict[str, Any],
    node_index: dict[str, list[dict[str, Any]]],
    default_format: str,
    default_code: str,
    answer_map: dict[int, str],
) -> list[dict[str, Any]]:
    ranges = section_ranges(text) or [("", 0, len(text))]
    questions = []
    running_index = 1
    current_passage = ""
    current_passage_id = None
    passage_index = 0
    for section_title, start, end in ranges:
        body = text[start:end]
        section_up = section_title.upper()
        first_item = NUMBERED_ITEM_RE.search(body)
        preamble = body[: first_item.start()] if first_item else ""
        preamble_up = preamble.upper()
        if first_item and (
            default_format in PASSAGE_FORMATS
            or "READ THE FOLLOWING" in section_up + preamble_up
            or re.search(r"TRUE\s*\(T\)|FALSE\s*\(F\)|SUY LUẬN|INFER", section_up + preamble_up)
        ):
            candidate = extract_passage_candidate(preamble)
            if len(candidate) >= 120:
                passage_index += 1
                current_passage = candidate
                current_passage_id = passage_id_for(sha1, current_passage, passage_index)
        for match in NUMBERED_ITEM_RE.finditer(body):
            qnum = int(match.group(1))
            block = truncate_at_boilerplate(match.group(2).strip())
            if len(clean_question_text(block)) < 8:
                continue
            option_matches = list(OPTION_RE.finditer(block))
            options: dict[str, str] = {}
            for om in option_matches:
                value = clean_question_text(om.group(2))
                if value:
                    options[om.group(1).upper()] = value[:800]
            qtext_raw = block
            if option_matches:
                qtext_raw = block[: option_matches[0].start()]
            qtext = clean_question_text(qtext_raw)
            if not qtext or re.fullmatch(r"[A-D]", qtext):
                continue
            item_type = infer_item_type(f"{section_title}\n{preamble}", block, options)
            passage_text = current_passage if item_type == "true_false" or default_format in PASSAGE_FORMATS else ""
            pid = current_passage_id if passage_text else None
            absolute_start = start + match.start()
            absolute_end = start + match.end()
            raw_extract = {
                "block": block[:3000],
                "section_title": section_title,
                "practice_item_type": item_type,
                "page_start": page_at_offset(text, absolute_start),
                "page_end": page_at_offset(text, absolute_end),
            }
            questions.append(make_regex_item(
                file_info, sha1, running_index, qnum, qtext, options, answer_map.get(qnum),
                default_format, default_code, hints, taxonomy, node_index, raw_extract, passage_text, pid,
            ))
            running_index += 1
    return questions

def regex_extract_questions(
    text: str,
    file_info: dict[str, Any],
    sha1: str,
    hints: dict[str, Any],
    taxonomy: dict[str, Any],
    node_index: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    valid_formats = {f["format_code"] for f in taxonomy["question_formats"]}
    valid_subtopics = {st["subtopic_code"] for st in taxonomy["knowledge_subtopics"]}
    default_format = (hints.get("likely_formats") or ["hsa_sentence_completion"])[0]
    default_code = (hints.get("likely_subtopics") or ["E2M.99"])[0]
    if default_format not in valid_formats:
        default_format = "hsa_sentence_completion"
    if default_code not in valid_subtopics:
        default_code = "E2M.99"
    answer_map: dict[int, str] = {}
    key_match = re.search(r"(?is)(?:^|\n)\s*(?:ĐÁP ÁN|ANSWER\s*KEY|KEY)\s*(?:\n|:)(.{0,5000})", text)
    if key_match:
        key_text = key_match.group(1)
        pairs = re.findall(r"(?i)(?:Question|Câu)?\s*(\d{1,3})\s*[:.)-]\s*([A-D])\b", key_text)
        if len(pairs) >= 5:
            for num, ans in pairs:
                answer_map[int(num)] = ans.upper()
    marker = re.compile(r"(?im)^\s*(?:Question|Câu)\s+(\d+)\s*[\.:)]?\s*")
    matches = list(marker.finditer(text))
    if not matches:
        return extract_numbered_layout_questions(
            text, file_info, sha1, hints, taxonomy, node_index, default_format, default_code, answer_map
        )
    questions = []
    allow_passage = default_format in PASSAGE_FORMATS
    current_passage = extract_passage_candidate(text[:matches[0].start()]) if matches and allow_passage else ""
    current_passage_index = 1
    current_passage_id = passage_id_for(sha1, current_passage, current_passage_index) if allow_passage else None
    for idx, match in enumerate(matches, start=1):
        if idx > 1 and allow_passage:
            gap = text[matches[idx - 2].end():match.start()]
            if PASSAGE_START_RE.search(gap) or NUMBERED_PASSAGE_START_RE.search(gap):
                candidate = extract_passage_candidate(gap)
                if len(candidate) >= 120 and not re.match(r"(?is)^[A-D]\s*[.)]", candidate):
                    current_passage_index += 1
                    current_passage = candidate
                    current_passage_id = passage_id_for(sha1, current_passage, current_passage_index)
        start = match.start()
        end = matches[idx].start() if idx < len(matches) else len(text)
        block = truncate_at_boilerplate(text[start:end].strip())
        if len(block) < 20:
            continue
        qnum = int(match.group(1))
        option_matches = list(re.finditer(r"(?ms)(?:^|\n|\s)([A-D])\s*[\.)]\s+(.+?)(?=(?:\n|\s)[A-D]\s*[\.)]\s+|$)", block))
        options = {}
        for om in option_matches:
            value = re.sub(r"\s+", " ", om.group(2)).strip()
            value = re.sub(r"\s*(?:Question|Câu)\s+\d+.*$", "", value, flags=re.I).strip()
            if value:
                options[om.group(1).upper()] = value[:800]
        qtext = marker.sub("", block, count=1)
        qtext = re.split(r"(?ms)(?:^|\n|\s)A\s*[\.)]\s+", qtext, maxsplit=1)[0]
        qtext = re.sub(r"\s+", " ", qtext).strip()
        blank_context = ""
        if len(qtext) < 20:
            blank_context = blank_context_for_question(text, start, qnum)
            if blank_context:
                qtext = blank_context
        answer = answer_map.get(qnum)
        item_type = infer_item_type("", block, options)
        item = make_regex_item(
            file_info=file_info,
            sha1=sha1,
            index=idx,
            qnum=qnum,
            qtext=qtext,
            options=options,
            answer=answer,
            default_format=default_format,
            default_code=default_code,
            hints=hints,
            taxonomy=taxonomy,
            node_index=node_index,
            raw_extract={
                "block": block[:3000],
                "practice_item_type": item_type,
                "page_start": page_at_offset(text, start),
                "page_end": page_at_offset(text, end),
            },
            passage_text=blank_context or (current_passage if allow_passage and current_passage_id else ""),
            passage_id=passage_id_for(sha1, blank_context, idx) if blank_context else (current_passage_id if allow_passage else None),
        )
        questions.append(item)
    return questions

def call_ai_extract(
    client: anthropic.Anthropic,
    file_info: dict[str, Any],
    text: str,
    hints: dict[str, Any],
    taxonomy: dict[str, Any],
    allow_ai_solve: bool,
    model: str,
) -> dict[str, Any]:
    subtopics = [{"code": s["subtopic_code"], "title": s["subtopic_title"], "topic": s["topic_title"]} for s in taxonomy["knowledge_subtopics"]]
    formats = [{"code": f["format_code"], "title": f["format_title"], "defaults": f.get("default_knowledge_subtopics", [])} for f in taxonomy["question_formats"]]
    payload = {
        "file": {k: file_info.get(k) for k in ["relative_path", "folder_path", "section", "subsection", "file_name"]},
        "allow_ai_solve": allow_ai_solve,
        "hints": hints,
        "exam_profiles": [b["exam_profile"] for b in taxonomy["exam_blueprints"]],
        "question_formats": formats,
        "knowledge_subtopics": subtopics,
        "pdf_text": text,
    }
    response = ai_call_with_retry(
        f"practice extract {model}",
        lambda: client.messages.create(
            model=model,
            max_tokens=12000,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        ),
    )
    content = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    return extract_json(content)


def extract_with_fallback(
    client: anthropic.Anthropic,
    file_info: dict[str, Any],
    text: str,
    hints: dict[str, Any],
    taxonomy: dict[str, Any],
    allow_ai_solve: bool,
) -> tuple[dict[str, Any], str]:
    try:
        return call_ai_extract(client, file_info, text, hints, taxonomy, allow_ai_solve, MODEL), MODEL
    except Exception as primary_exc:
        print(f"  primary failed: {primary_exc}", flush=True)
        try:
            return call_ai_extract(client, file_info, text, hints, taxonomy, allow_ai_solve, FALLBACK_MODEL), FALLBACK_MODEL
        except Exception as fallback_exc:
            raise RuntimeError(f"primary={primary_exc}; fallback={fallback_exc}") from fallback_exc


def stable_question_id(sha1: str, index: int, question: dict[str, Any], relative_path: str = "") -> str:
    number = str(question.get("question_number") or index)
    basis = f"{sha1}:{relative_path}:{index}:{number}:{question.get('question_text', '')[:80]}"
    suffix = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
    return f"en-practice-{sha1[:12]}-{index:03d}-{suffix}"


def normalize_options(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out = {}
    for key, val in value.items():
        label = str(key).strip().upper()[:1]
        if label in {"A", "B", "C", "D", "E", "F"} and val is not None:
            out[label] = str(val).strip()
    return out


def validate_and_normalize(
    data: dict[str, Any],
    file_info: dict[str, Any],
    sha1: str,
    hints: dict[str, Any],
    taxonomy: dict[str, Any],
    node_index: dict[str, list[dict[str, Any]]],
    model_used: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    valid_formats = {f["format_code"] for f in taxonomy["question_formats"]}
    valid_subtopics = {s["subtopic_code"] for s in taxonomy["knowledge_subtopics"]}
    valid_profiles = {b["exam_profile"] for b in taxonomy["exam_blueprints"]}
    default_format = (hints.get("likely_formats") or ["hsa_sentence_completion"])[0]
    default_code = (hints.get("likely_subtopics") or ["E2M.99"])[0]
    questions = data.get("questions") if isinstance(data, dict) else []
    if not isinstance(questions, list):
        questions = []
    if not questions and max(hints.get("question_marker_count", 0), hints.get("numbered_item_count", 0), hints.get("blank_count", 0)) > 0:
        summary = data.get("file_summary") if isinstance(data.get("file_summary"), dict) else {}
        summary["confidence"] = "low"
        summary["review_reason"] = "ai_returned_zero_questions_despite_regex_hints"
        return [], summary
    normalized = []
    for index, q in enumerate(questions, start=1):
        if not isinstance(q, dict):
            continue
        fmt = q.get("question_format") if q.get("question_format") in valid_formats else default_format
        code = q.get("knowledge_subtopic_code_v2") if q.get("knowledge_subtopic_code_v2") in valid_subtopics else default_code
        if code not in valid_subtopics:
            code = "E2M.99"
        profiles = [p for p in (q.get("exam_profiles") or []) if p in valid_profiles] or hints.get("likely_exam_profiles") or ["THPT_2025_CORE"]
        answer_source = q.get("answer_source") if q.get("answer_source") in ANSWER_SOURCES else "missing"
        confidence = q.get("confidence") if q.get("confidence") in CONFIDENCES else "low"
        difficulty = q.get("difficulty") if q.get("difficulty") in DIFFICULTIES else "basic"
        options = normalize_options(q.get("options"))
        review_reasons = []
        if q.get("review_reason"):
            review_reasons.append(str(q.get("review_reason")))
        if answer_source == "ai_solved":
            review_reasons.append("ai_solved_answer")
        if answer_source == "missing":
            review_reasons.append("missing_answer")
        if fmt not in {"spt_paragraph_writing", "hsa_sentence_rewriting", "hsa_sentence_combination"} and len(options) < 2:
            review_reasons.append("few_options_detected")
        if hints.get("text_quality") == "low":
            review_reasons.append("low_text_quality")
        needs_review = bool(q.get("needs_review")) or bool(review_reasons) or confidence == "low"
        item = {
            "question_id": stable_question_id(sha1, index, q, file_info.get("relative_path", "")),
            "source_file": file_info.get("file_name"),
            "relative_path": file_info.get("relative_path"),
            "file_sha1": sha1,
            "question_number": q.get("question_number") or index,
            "page_start": q.get("page_start"),
            "page_end": q.get("page_end"),
            "question_text": str(q.get("question_text") or "").strip(),
            "options": options,
            "correct_answer": q.get("correct_answer"),
            "answer_source": answer_source,
            "explanation": str(q.get("explanation") or "").strip(),
            "passage_id": q.get("passage_id"),
            "passage_text": q.get("passage_text"),
            "question_format": fmt,
            "knowledge_subtopic_code_v2": code,
            "exam_profiles": sorted(set(profiles)),
            "linked_node_codes_v2": [],
            "difficulty": difficulty,
            "confidence": confidence,
            "needs_review": needs_review,
            "review_reason": "; ".join(sorted(set(r for r in review_reasons if r))),
            "ready_for_ai_solve": False,
            "ai_model": model_used,
            "raw_extract": q,
        }
        if item.get("question_format") not in PASSAGE_FORMATS:
            item["passage_id"] = None
            item["passage_text"] = None
        item["ready_for_ai_solve"] = ready_for_ai_solve(item)
        item["linked_node_codes_v2"] = linked_nodes(item, file_info, node_index)
        if item["question_text"]:
            normalized.append(item)
    summary = data.get("file_summary") if isinstance(data.get("file_summary"), dict) else {}
    return normalized, summary

def error_result(file_info: dict[str, Any], sha1: str, error: str, hints: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "file": file_info,
        "file_sha1": sha1,
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "status": "failed",
        "needs_ai_review": True,
        "error": error,
        "hints": hints or {},
        "questions": [],
        "file_summary": {"confidence": "low", "review_reason": error},
    }

def extraction_failure_reason(hints: dict[str, Any]) -> str:
    if (
        hints.get("char_count", 0) < 1800
        and hints.get("question_marker_count", 0) == 0
        and hints.get("numbered_item_count", 0) == 0
        and hints.get("option_a_count", 0) == 0
        and hints.get("blank_count", 0) == 0
    ):
        return "ocr_required_or_image_only_pdf"
    if hints.get("numbered_item_count", 0) or hints.get("option_a_count", 0) or hints.get("blank_count", 0):
        return "unsupported_text_layout"
    return "regex_returned_zero_questions"


def process_file(
    file_info: dict[str, Any],
    root: Path,
    client: anthropic.Anthropic,
    taxonomy: dict[str, Any],
    node_index: dict[str, list[dict[str, Any]]],
    cache_dir: Path,
    force: bool,
    allow_ai_solve: bool,
    regex_only: bool = False,
) -> dict[str, Any]:
    input_dir = root / "input_sources"
    path = input_dir / file_info["relative_path"]
    if not path.exists():
        return error_result(file_info, "", f"missing_pdf: {path}")
    sha1 = file_sha1(path)
    cache_path = cache_dir / f"{sha1}.json"
    if cache_path.exists() and not force:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        cached["from_cache"] = True
        return cached
    text, page_count, char_count = read_pdf_text(path)
    hints = make_hints(text, file_info, page_count, char_count)
    if regex_only:
        questions = regex_extract_questions(text, file_info, sha1, hints, taxonomy, node_index)
        result = {
            "file": file_info,
            "file_sha1": sha1,
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "status": "ok" if questions else "failed",
            "needs_ai_review": not bool(questions),
            "error": "" if questions else extraction_failure_reason(hints),
            "hints": hints,
            "file_summary": {"confidence": "medium" if questions else "low", "review_reason": "regex_only"},
            "questions": questions,
            "from_cache": False,
        }
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    try:
        data, model_used = extract_with_fallback(client, file_info, text, hints, taxonomy, allow_ai_solve)
        questions, summary = validate_and_normalize(data, file_info, sha1, hints, taxonomy, node_index, model_used)
        if not questions:
            questions = regex_extract_questions(text, file_info, sha1, hints, taxonomy, node_index)
            if questions:
                summary = {"confidence": "medium", "review_reason": "regex_fallback_after_ai_zero_questions"}
        result = {
            "file": file_info,
            "file_sha1": sha1,
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "status": "ok" if questions else "failed",
            "needs_ai_review": not bool(questions),
            "error": "" if questions else "ai_returned_zero_questions",
            "hints": hints,
            "file_summary": summary,
            "questions": questions,
            "from_cache": False,
        }
    except Exception as exc:
        result = error_result(file_info, sha1, str(exc), hints)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    time.sleep(AI_REQUEST_DELAY_SECONDS)
    return result


def load_existing_results(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {item.get("file_sha1") or item.get("file", {}).get("relative_path"): item for item in payload.get("files", [])}




def build_passages(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for result in results:
        file_info = result.get("file", {})
        for q in result.get("questions", []):
            if not is_accepted_question(q):
                continue
            passage_id = q.get("passage_id")
            passage_text = q.get("passage_text")
            if not passage_id or not passage_text:
                continue
            if passage_id not in grouped:
                grouped[passage_id] = {
                    "passage_id": passage_id,
                    "relative_path": q.get("relative_path") or file_info.get("relative_path"),
                    "source_file": q.get("source_file") or file_info.get("file_name"),
                    "file_sha1": q.get("file_sha1") or result.get("file_sha1"),
                    "passage_text": passage_text,
                    "question_ids": [],
                    "question_numbers": [],
                    "question_count": 0,
                }
            grouped[passage_id]["question_ids"].append(q.get("question_id"))
            grouped[passage_id]["question_numbers"].append(q.get("question_number"))
    passages = list(grouped.values())
    for passage in passages:
        passage["question_count"] = len(passage["question_ids"])
    passages.sort(key=lambda item: (item.get("relative_path") or "", str(item.get("passage_id") or "")))
    return passages

def write_questions_json(results: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    questions, rejected_questions = split_questions(results)
    passages = build_passages(results)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "taxonomy_version": "english_taxonomy_v2",
        "total_files": len(results),
        "total_passages": len(passages),
        "total_questions": len(questions),
        "total_rejected_questions": len(rejected_questions),
        "files": results,
        "passages": passages,
        "questions": questions,
        "rejected_questions": rejected_questions,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {path}")


def build_coverage(results: list[dict[str, Any]]) -> dict[str, Any]:
    raw_questions = [q for result in results for q in result.get("questions", [])]
    questions, rejected_questions = split_questions(results)
    ids = [q.get("question_id") for q in questions]
    failed = [r for r in results if r.get("status") != "ok"]
    passages = build_passages(results)
    format_default_subtopic_hits = Counter()
    taxonomy_path = DEFAULT_ROOT / "output_json" / "english_taxonomy_v2.json"
    if taxonomy_path.exists():
        try:
            taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
            defaults_by_format = {f["format_code"]: f.get("default_knowledge_subtopics", []) for f in taxonomy.get("question_formats", [])}
            for q in questions:
                for code in defaults_by_format.get(q.get("question_format"), []):
                    format_default_subtopic_hits[code] += 1
        except Exception:
            pass
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_practice_files": len(results),
        "total_passages": len(passages),
        "scanned_files": sum(1 for r in results if r.get("status") == "ok"),
        "failed_files": len(failed),
        "failed_file_details": [
            {"relative_path": r.get("file", {}).get("relative_path"), "error": r.get("error"), "needs_ai_review": True}
            for r in failed
        ],
        "raw_extracted_questions": len(raw_questions),
        "total_questions": len(questions),
        "rejected_questions": len(rejected_questions),
        "duplicate_question_ids": len(ids) - len(set(ids)),
        "by_format": Counter(q.get("question_format") for q in questions),
        "by_subtopic": Counter(q.get("knowledge_subtopic_code_v2") for q in questions),
        "by_answer_source": Counter(q.get("answer_source") for q in questions),
        "by_confidence": Counter(q.get("confidence") for q in questions),
        "by_format_default_subtopic": format_default_subtopic_hits,
        "needs_review_count": sum(1 for q in questions if q.get("needs_review")),
        "ready_for_ai_solve_count": sum(1 for q in questions if q.get("ready_for_ai_solve")),
        "rejected_by_reason": Counter(reason for q in rejected_questions for reason in (q.get("rejected_reason") or "").split("; ") if reason),
        "files_zero_questions": [r.get("file", {}).get("relative_path") for r in results if r.get("status") == "ok" and not r.get("questions")],
    }


def write_coverage(results: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    coverage = build_coverage(results)
    serializable = json.loads(json.dumps(coverage, ensure_ascii=False, default=dict))
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {path}")


def write_preview(results: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    coverage = build_coverage(results)
    rows = []
    for result in results:
        file_info = result.get("file", {})
        raw_questions = result.get("questions", [])
        questions = [q for q in raw_questions if is_accepted_question(q)]
        rejected = []
        for q in raw_questions:
            reasons = rejection_reasons(q)
            if reasons:
                item = dict(q)
                item["rejected_reason"] = "; ".join(reasons)
                rejected.append(item)
        answer_counts = Counter(q.get("answer_source") for q in questions)
        format_counts = Counter(q.get("question_format") for q in questions)
        passage_groups: dict[str, dict[str, Any]] = {}
        standalone = []
        for q in questions:
            passage_id = q.get("passage_id")
            if passage_id and q.get("passage_text"):
                passage_groups.setdefault(passage_id, {"text": q.get("passage_text") or "", "questions": []})["questions"].append(q)
            else:
                standalone.append(q)

        grouped_html = ""
        for passage_id, group in passage_groups.items():
            grouped_html += (
                f"<details open><summary><b>Passage</b> {html.escape(passage_id)} "
                f"({len(group['questions'])} questions)</summary>"
                f"<div style='margin:6px 0;padding:8px;background:#f8f8f8;border-left:3px solid #999'>"
                f"{html.escape(group['text'][:1800])}"
                f"{'...' if len(group['text']) > 1800 else ''}</div>"
            )
            for q in group["questions"][:12]:
                opts = " | ".join(f"{k}. {v}" for k, v in (q.get("options") or {}).items())
                grouped_html += (
                    f"<div style='margin:8px 0'><b>Q{html.escape(str(q.get('question_number')))}</b> "
                    f"{html.escape((q.get('question_text') or '')[:360])}<br>"
                    f"<span style='color:#555'>{html.escape(opts[:420])}</span><br>"
                    f"<span>Ans: {html.escape(str(q.get('correct_answer')))} "
                    f"({html.escape(str(q.get('answer_source')))}), ready={html.escape(str(q.get('ready_for_ai_solve')))}, "
                    f"{html.escape(str(q.get('knowledge_subtopic_code_v2')))}, {html.escape(str(q.get('question_format')))}</span></div>"
                )
            if len(group["questions"]) > 12:
                grouped_html += f"<div style='color:#777'>... {len(group['questions']) - 12} more questions</div>"
            grouped_html += "</details>"

        if standalone:
            grouped_html += "<details open><summary><b>Standalone questions</b></summary>"
            for q in standalone[:12]:
                opts = " | ".join(f"{k}. {v}" for k, v in (q.get("options") or {}).items())
                grouped_html += (
                    f"<div style='margin:8px 0'><b>Q{html.escape(str(q.get('question_number')))}</b> "
                    f"{html.escape((q.get('question_text') or '')[:360])}<br>"
                    f"<span style='color:#555'>{html.escape(opts[:420])}</span><br>"
                    f"<span>Ans: {html.escape(str(q.get('correct_answer')))} "
                    f"({html.escape(str(q.get('answer_source')))}), ready={html.escape(str(q.get('ready_for_ai_solve')))}, "
                    f"{html.escape(str(q.get('knowledge_subtopic_code_v2')))}, {html.escape(str(q.get('question_format')))}</span></div>"
                )
            if len(standalone) > 12:
                grouped_html += f"<div style='color:#777'>... {len(standalone) - 12} more questions</div>"
            grouped_html += "</details>"

        if rejected:
            grouped_html += "<details><summary><b>Rejected questions</b> " + str(len(rejected)) + "</summary>"
            for q in rejected[:10]:
                opts = " | ".join(f"{k}. {v}" for k, v in (q.get("options") or {}).items())
                grouped_html += (
                    f"<div style='margin:8px 0;color:#8a4b00'><b>Q{html.escape(str(q.get('question_number')))}</b> "
                    f"reason={html.escape(q.get('rejected_reason',''))}<br>"
                    f"{html.escape((q.get('question_text') or '')[:240])}<br>"
                    f"<span style='color:#777'>{html.escape(opts[:260])}</span></div>"
                )
            if len(rejected) > 10:
                grouped_html += f"<div style='color:#777'>... {len(rejected) - 10} more rejected</div>"
            grouped_html += "</details>"

        rows.append(
            "<tr>"
            f"<td>{html.escape(str(file_info.get('order', '')))}</td>"
            f"<td>{html.escape(file_info.get('relative_path', ''))}</td>"
            f"<td>{html.escape(result.get('status', ''))}</td>"
            f"<td>{len(passage_groups)}</td>"
            f"<td>{len(questions)} / {len(rejected)} / {len(raw_questions)}</td>"
            f"<td>{result.get('hints', {}).get('question_marker_count', 0)} / {result.get('hints', {}).get('option_a_count', 0)}</td>"
            f"<td>{html.escape(', '.join(f'{k}:{v}' for k, v in format_counts.items()))}</td>"
            f"<td>{html.escape(', '.join(f'{k}:{v}' for k, v in answer_counts.items()))}</td>"
            f"<td>{html.escape(result.get('error', '')[:180])}</td>"
            f"<td>{grouped_html}</td>"
            "</tr>"
        )
    content = f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Practice Questions Preview</title>
<style>body{{font-family:sans-serif;font-size:12px;padding:20px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:6px;vertical-align:top}}th{{background:#333;color:white;position:sticky;top:0}}.sum{{background:#f2f2f2;padding:12px;margin-bottom:16px}}details{{margin-bottom:10px}}summary{{cursor:pointer}}</style></head><body>
<h2>English Practice Questions Preview</h2>
<div class="sum">Files: <b>{coverage['total_practice_files']}</b> | Passages: <b>{coverage['total_passages']}</b> | OK: <b>{coverage['scanned_files']}</b> | Failed: <b>{coverage['failed_files']}</b> | Accepted: <b>{coverage['total_questions']}</b> | Rejected: <b>{coverage['rejected_questions']}</b> | Raw: <b>{coverage['raw_extracted_questions']}</b> | Ready solve: <b>{coverage['ready_for_ai_solve_count']}</b> | Needs review: <b>{coverage['needs_review_count']}</b></div>
<table><tr><th>#</th><th>File</th><th>Status</th><th>Passages</th><th>Accepted / Rejected / Raw</th><th>Regex Q/A</th><th>Formats</th><th>Answers</th><th>Error</th><th>Grouped Preview</th></tr>{''.join(rows)}</table>
</body></html>"""
    path.write_text(content, encoding="utf-8")
    print(f"Saved: {path}")

def process_file_segments(file_info: dict[str, Any], root: Path) -> dict[str, Any]:
    input_dir = root / "input_sources"
    path = input_dir / file_info["relative_path"]
    if not path.exists():
        return {
            "file": file_info,
            "file_sha1": "",
            "status": "failed",
            "error": f"missing_pdf: {path}",
            "hints": {},
            "segments": [],
        }
    sha1 = file_sha1(path)
    text, page_count, char_count = read_pdf_text(path)
    hints = make_hints(text, file_info, page_count, char_count)
    segments = segment_practice_text(text, file_info, sha1, hints)
    return {
        "file": file_info,
        "file_sha1": sha1,
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "status": "ok" if segments else "failed",
        "error": "" if segments else "segmenter_returned_zero_segments",
        "hints": hints,
        "segments": segments,
    }

def write_segments_json(results: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    segments = [seg for result in results for seg in result.get("segments", [])]
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_files": len(results),
        "total_segments": len(segments),
        "total_question_blocks": sum(seg.get("question_count", 0) for seg in segments),
        "by_segment_type": Counter(seg.get("segment_type") for seg in segments),
        "files": results,
        "segments": segments,
        "failed_segments": [seg for seg in segments if seg.get("segment_type") == "unknown"],
    }
    serializable = json.loads(json.dumps(payload, ensure_ascii=False, default=dict))
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {path}")

def write_segments_preview(results: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    segments = [seg for result in results for seg in result.get("segments", [])]
    by_type = Counter(seg.get("segment_type") for seg in segments)
    rows = []
    for result in results:
        file_info = result.get("file", {})
        seg_html = ""
        for seg in result.get("segments", []):
            context = seg.get("context_text") or ""
            qnums = ", ".join(str(n) for n in seg.get("question_numbers", []))
            questions_html = ""
            for block in seg.get("question_blocks", [])[:8]:
                first_line = re.sub(r"\s+", " ", block).strip()
                questions_html += f"<li>{html.escape(first_line[:360])}{'...' if len(first_line) > 360 else ''}</li>"
            if seg.get("question_count", 0) > 8:
                questions_html += f"<li class='muted'>... {seg.get('question_count', 0) - 8} more</li>"
            seg_html += (
                f"<details open><summary><b>{html.escape(str(seg.get('segment_type')))}</b> "
                f"seg#{html.escape(str(seg.get('segment_index')))} | q={html.escape(str(seg.get('question_count')))} "
                f"| nums={html.escape(qnums)} | p={html.escape(str(seg.get('page_start')))}-{html.escape(str(seg.get('page_end')))} "
                f"| {html.escape(str(seg.get('confidence')))}</summary>"
                f"<div class='context'>{html.escape(context[:1800])}{'...' if len(context) > 1800 else ''}</div>"
                f"<ol>{questions_html}</ol></details>"
            )
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(file_info.get('order', '')))}</td>"
            f"<td>{html.escape(file_info.get('relative_path', ''))}</td>"
            f"<td>{html.escape(result.get('status', ''))}</td>"
            f"<td>{len(result.get('segments', []))}</td>"
            f"<td>{sum(seg.get('question_count', 0) for seg in result.get('segments', []))}</td>"
            f"<td>{html.escape(result.get('error', '')[:160])}</td>"
            f"<td>{seg_html}</td>"
            "</tr>"
        )
    content = f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Practice Segments Preview</title>
<style>body{{font-family:Arial,sans-serif;font-size:12px;padding:20px;color:#222}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:6px;vertical-align:top}}th{{background:#333;color:white;position:sticky;top:0}}details{{margin-bottom:8px}}summary{{cursor:pointer}}.sum{{background:#f2f2f2;padding:12px;margin-bottom:16px}}.context{{white-space:pre-wrap;background:#f8f8f8;border-left:3px solid #777;padding:8px;margin:6px 0;max-width:900px}}.muted{{color:#777}}</style></head><body>
<h2>English Practice Segments Preview</h2>
<div class="sum">Files: <b>{len(results)}</b> | Segments: <b>{len(segments)}</b> | Question blocks: <b>{sum(seg.get('question_count', 0) for seg in segments)}</b> | Types: <b>{html.escape(', '.join(f'{k}:{v}' for k, v in by_type.items()))}</b></div>
<table><tr><th>#</th><th>File</th><th>Status</th><th>Segments</th><th>Question Blocks</th><th>Error</th><th>Segment Preview</th></tr>{''.join(rows)}</table>
</body></html>"""
    path.write_text(content, encoding="utf-8")
    print(f"Saved: {path}")

def run_segment_preview(args: argparse.Namespace, practice_files: list[dict[str, Any]]) -> None:
    results = []
    for index, file_info in enumerate(practice_files, start=1):
        result = process_file_segments(file_info, args.root)
        results.append(result)
        tag = "ok" if result.get("status") == "ok" else "fail"
        print(
            f"[{index:>3}/{len(practice_files)}] {tag:<5} seg={len(result.get('segments', [])):>3} "
            f"qblocks={sum(seg.get('question_count', 0) for seg in result.get('segments', [])):>3} "
            f"{file_info.get('file_name', '')[:80]}",
            flush=True,
        )
    write_segments_json(results, args.root / "output_json" / "practice_segments.json")
    write_segments_preview(results, args.root / "previews" / "practice_segments_preview.html")
    failed = sum(1 for result in results if result.get("status") != "ok")
    print(f"\nSegment preview done. files={len(results)} failed={failed}")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract English practice questions locally with 9Router AI.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-per-topic", type=int, help="Select N practice files from each third-level topic folder before --limit.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Use cache when present. This is the default unless --force is set.")
    parser.add_argument("--only-errors", action="store_true", help="Only retry files failed in existing practice_questions.json.")
    parser.add_argument("--no-solve", action="store_true", help="Do not ask AI to solve missing answers.")
    parser.add_argument("--regex-only", action="store_true", help="Skip AI extraction and use deterministic regex fallback only.")
    parser.add_argument("--segment-preview", action="store_true", help="Only segment practice PDFs and write practice_segments preview artifacts.")
    return parser.parse_args()


def main() -> None:
    configure_stdio()
    args = parse_args()
    root = args.root
    manifest_path = root / "output_json" / "file_manifest.json"
    taxonomy_path = root / "output_json" / "english_taxonomy_v2.json"
    if not manifest_path.exists():
        raise SystemExit(f"Missing manifest: {manifest_path}")
    if not taxonomy_path.exists():
        raise SystemExit(f"Missing taxonomy: {taxonomy_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    node_index = build_node_index(json.loads((root / "output_json" / "learning_map_nodes.json").read_text(encoding="utf-8")).get("nodes", [])) if (root / "output_json" / "learning_map_nodes.json").exists() else {}
    practice_files = [item for item in manifest.get("files", []) if item.get("file_type") == "practice"]

    out_json = root / "output_json" / "practice_questions.json"
    existing = load_existing_results(out_json)
    if args.only_errors:
        failed_paths = {r.get("file", {}).get("relative_path") for r in existing.values() if r.get("status") != "ok"}
        practice_files = [item for item in practice_files if item.get("relative_path") in failed_paths]
    if args.sample_per_topic:
        practice_files = sample_per_topic(practice_files, args.sample_per_topic)
    if args.limit:
        practice_files = practice_files[: args.limit]

    if args.segment_preview:
        print(f"Practice files: {len(practice_files)}")
        print("Mode: segment preview only\n", flush=True)
        run_segment_preview(args, practice_files)
        return

    print(f"Practice files: {len(practice_files)}")
    print(f"9Router/API: {normalize_base_url(BASE_URL)}/v1")
    print(f"Model: {MODEL}")
    print(f"Fallback: {FALLBACK_MODEL}")
    print(f"AI solve missing answers: {not args.no_solve}")
    print(f"Regex only: {args.regex_only}\n", flush=True)

    client = anthropic.Anthropic(api_key=API_KEY, base_url=f"{normalize_base_url(BASE_URL)}/v1", timeout=AI_TIMEOUT_SECONDS)
    cache_dir = root / "cache" / "scan_practice_meta"
    results = []
    ok = failed = cached = 0
    for index, file_info in enumerate(practice_files, start=1):
        result = process_file(
            file_info=file_info,
            root=root,
            client=client,
            taxonomy=taxonomy,
            node_index=node_index,
            cache_dir=cache_dir,
            force=args.force,
            allow_ai_solve=not args.no_solve,
            regex_only=args.regex_only,
        )
        results.append(result)
        if result.get("from_cache"):
            cached += 1
            tag = "cache"
        elif result.get("status") == "ok":
            ok += 1
            tag = "ok"
        else:
            failed += 1
            tag = "fail"
        print(f"[{index:>3}/{len(practice_files)}] {tag:<5} q={len(result.get('questions', [])):>3} {file_info.get('file_name', '')[:80]}", flush=True)

    if args.only_errors and existing:
        by_path = {r.get("file", {}).get("relative_path"): r for r in existing.values()}
        for result in results:
            by_path[result.get("file", {}).get("relative_path")] = result
        results = list(by_path.values())

    if args.save:
        write_questions_json(results, out_json)
        write_coverage(results, root / "output_json" / "practice_scan_coverage.json")
    if args.preview:
        write_preview(results, root / "previews" / "practice_questions_preview.html")
    if not args.save and not args.preview:
        coverage = build_coverage(results)
        print(json.dumps(coverage, ensure_ascii=False, indent=2, default=dict))
    print(f"\nDone. ok={ok} failed={failed} cache={cached}")


if __name__ == "__main__":
    main()
