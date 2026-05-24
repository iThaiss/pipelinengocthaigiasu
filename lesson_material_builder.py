import argparse
import base64
import hashlib
import html
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
import fitz  # PyMuPDF

DEFAULT_ROOT = Path("local_curriculum")
MODEL = os.getenv("LESSON_MATERIAL_MODEL") or os.getenv("CLAUDE_MODEL", "cc/claude-sonnet-4-6")
BASE_URL = os.getenv("CLAUDE_BASE_URL", "http://localhost:20128").rstrip("/")
API_KEY = os.getenv("NINEROUTER_API_KEY") or os.getenv("CLAUDE_API_KEY") or "local-9router"
AI_TIMEOUT_SECONDS = float(os.getenv("LESSON_MATERIAL_AI_TIMEOUT_SECONDS", "120"))
VISION_TIMEOUT_SECONDS = float(os.getenv("LESSON_MATERIAL_VISION_TIMEOUT_SECONDS", "180"))
PDF_RENDER_DPI = 150

SYSTEM_PROMPT = """Bạn là giáo viên Toán THPT Việt Nam thiết kế tài liệu học dạng PDF chuẩn.
Nhiệm vụ: dựa trên nội dung tài liệu gốc được cung cấp, viết lại thành bài học rõ ràng, dễ hiểu cho học sinh trung bình.

Nguyên tắc bắt buộc:
- Bám sát nội dung tài liệu gốc (công thức, ví dụ, quy tắc). Không bịa thêm.
- Viết lại ngôn ngữ dễ hiểu hơn: giải thích rõ từng bước, thêm lưu ý thực tế.
- Không dùng vận dụng cao trong giai đoạn nền tảng.
- Viết công thức toán trong LaTeX, bọc inline bằng $...$, ví dụ: $x^n$, $\\sqrt{x}$, $f'(x)$, $\\frac{u'v - uv'}{v^2}$.
- Mỗi list tối đa 5 phần tử; công thức viết đầy đủ nhưng ngắn.
- Trả JSON duy nhất, không markdown ngoài JSON.

Schema JSON (bắt buộc đủ các trường):
{
  "title_note": "một dòng mô tả phạm vi bài, ví dụ: Phần 1 - Đạo hàm cơ bản và 4 quy tắc",
  "learning_outcomes": ["mục tiêu 1", "mục tiêu 2"],
  "formula_table": [
    {"y": "hàm số", "dy": "đạo hàm", "note": "ghi chú điều kiện nếu có"}
  ],
  "rule_boxes": [
    {
      "number": 1,
      "title": "Tên quy tắc",
      "statement": "Công thức quy tắc (plain text)",
      "note": "Lưu ý áp dụng hoặc điều kiện (ngắn gọn)",
      "example": "Ví dụ áp dụng ngắn 1 dòng"
    }
  ],
  "worked_examples": [
    {
      "number": 1,
      "problem": "Đề bài ví dụ cụ thể",
      "solution_steps": ["Bước 1: ...", "Bước 2: ...", "Bước 3: ..."],
      "answer": "Kết quả cuối"
    }
  ],
  "remarks": [
    {"label": "Nhận xét", "content": "Nội dung nhận xét quan trọng"}
  ],
  "common_mistakes": [
    {"mistake": "Lỗi sai thường gặp", "correction": "Cách đúng"}
  ],
  "solving_process": ["Bước 1", "Bước 2", "Bước 3", "Bước 4", "Bước 5"],
  "pre_class_self_check": ["Câu hỏi tự kiểm 1", "Câu hỏi tự kiểm 2"]
}
"""

DIFFICULTY_ORDER = {"Nhận biết": 1, "Thông hiểu": 2, "Vận dụng": 3, "Vận dụng cao": 4}

def safe_html(text: str) -> str:
    """Escape HTML outside $...$ math spans; inside math, only escape < and > (not &)."""
    if not text:
        return ""
    parts = re.split(r"(\$\$[\s\S]*?\$\$|\$[^\$\n]+?\$)", str(text))
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # inside $...$
            out.append(part.replace("<", "&lt;").replace(">", "&gt;"))
        else:
            out.append(html.escape(part))
    return "".join(out)

def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

def normalize_base_url(value: str) -> str:
    value = value.rstrip("/")
    if value.endswith("/v1"):
        return value[:-3]
    return value

def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    if start < 0:
        return {}
    decoder = json.JSONDecoder()
    try:
        data, _end = decoder.raw_decode(cleaned[start:])
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return {}

def prompt_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

def session_key(week: int, session_no: int) -> str:
    return f"week_{week:02d}_session_{session_no:02d}"


# ── Vision / PDF rendering ────────────────────────────────────────────────────

def find_source_pdf(root: Path, scan_data: dict[str, Any], unit_id: int) -> Path | None:
    """Return the absolute path of the primary source PDF for a unit, or None."""
    for lesson in scan_data.get("lessons", []):
        if lesson.get("source") == "LB" and lesson.get("order") == unit_id:
            rel = lesson.get("relative_path", "")
            if rel:
                candidate = root / "input_sources" / "LB" / rel
                if candidate.exists():
                    return candidate
    return None


def render_pdf_pages(pdf_path: Path, cache_dir: Path, dpi: int = PDF_RENDER_DPI) -> list[Path]:
    """Render every page of pdf_path to PNG, cache in cache_dir. Return sorted list."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    stem = pdf_path.stem.replace(" ", "_").replace(".", "_")
    doc = fitz.open(str(pdf_path))
    paths: list[Path] = []
    for i, page in enumerate(doc):
        out = cache_dir / f"{stem}_p{i+1:03d}.png"
        if not out.exists():
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            pix.save(str(out))
        paths.append(out)
    doc.close()
    return paths


def _img_block(image_path: Path) -> dict[str, Any]:
    """Build an Anthropic image content block from a PNG file."""
    data = base64.standard_b64encode(image_path.read_bytes()).decode()
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": data},
    }


def extract_source_via_vision(
    client: anthropic.Anthropic,
    image_paths: list[Path],
    cache_path: Path,
    refresh: bool = False,
) -> str:
    """
    Call Vision API on all pages, return extracted raw text content.
    Result is cached so re-runs are fast.
    """
    if cache_path.exists() and not refresh:
        return cache_path.read_text(encoding="utf-8")

    # Build content: interleave images with page labels
    content: list[Any] = []
    for i, p in enumerate(image_paths, 1):
        content.append({"type": "text", "text": f"--- Trang {i} ---"})
        content.append(_img_block(p))

    content.append({
        "type": "text",
        "text": (
            "Đây là tài liệu giảng dạy Toán THPT. "
            "Hãy đọc và trích xuất TOÀN BỘ nội dung từng trang theo thứ tự: "
            "tiêu đề, công thức, định lý, quy tắc, ví dụ (đề bài + lời giải từng bước), "
            "bài tập, nhận xét. "
            "Giữ nguyên ký hiệu toán học. Viết rõ ràng, không bỏ sót. "
            "Chỉ trả nội dung, không giải thích thêm."
        ),
    })

    resp = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        timeout=VISION_TIMEOUT_SECONDS,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return text

def find_session(roadmap: dict[str, Any], week_no: int, session_no: int) -> tuple[dict[str, Any], dict[str, Any]]:
    week = next((item for item in roadmap["weeks"] if int(item["week"]) == week_no), None)
    if not week:
        raise ValueError(f"Missing week {week_no}")
    session = next((item for item in week["sessions"] if int(item["session_no"]) == session_no), None)
    if not session:
        raise ValueError(f"Missing session {session_no} in week {week_no}")
    return week, session

def units_by_id(canonical: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(unit["order"]): unit for unit in canonical.get("roadmap_units", [])}

def build_context(week: dict[str, Any], session: dict[str, Any], canonical: dict[str, Any], questions: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = units_by_id(canonical)
    unit_ids = [int(unit_id) for unit_id in session.get("canonical_unit_ids", [])]
    units = [by_id[unit_id] for unit_id in unit_ids if unit_id in by_id]
    sample_questions = select_questions(questions, unit_ids, max_difficulty="Vận dụng", limit=6, allow_review=False)
    return {
        "week": week["week"],
        "phase": week["phase"],
        "session_no": session["session_no"],
        "track": session["track_label"],
        "lesson_focus": session["lesson_focus"],
        "canonical_unit_ids": unit_ids,
        "canonical_units": [
            {
                "id": unit.get("order"),
                "title": unit.get("canonical_title"),
                "learning_goals": unit.get("learning_goals", []),
                "prerequisites": unit.get("prerequisites", []),
                "application_types": unit.get("application_types", []),
                "gaps_to_fill": unit.get("gaps_to_fill", []),
            }
            for unit in units
        ],
        "pre_class_video": session.get("pre_class_video", {}),
        "pre_class_exercise": session.get("pre_class_exercise", {}),
        "live_class_plan": session.get("live_class_plan", {}),
        "post_class_homework": session.get("post_class_homework", {}),
        "retrieval_review": session.get("retrieval_review", []),
        "sample_questions": [compact_question(row) for row in sample_questions],
    }

def compact_question(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "question_type": row.get("question_type"),
        "difficulty": row.get("difficulty"),
        "question_text": row.get("question_text"),
        "option_a": row.get("option_a"),
        "option_b": row.get("option_b"),
        "option_c": row.get("option_c"),
        "option_d": row.get("option_d"),
        "correct_answer": row.get("correct_answer"),
        "numeric_answer": row.get("numeric_answer"),
        "application_type": row.get("application_type"),
    }

def difficulty_rank(value: Any) -> int:
    return DIFFICULTY_ORDER.get(str(value or ""), 99)

def has_answer(row: dict[str, Any]) -> bool:
    return bool(row.get("correct_answer") or row.get("numeric_answer") is not None or row.get("statements"))

def select_questions(
    questions: list[dict[str, Any]],
    unit_ids: list[int],
    max_difficulty: str,
    limit: int,
    allow_review: bool,
) -> list[dict[str, Any]]:
    max_rank = difficulty_rank(max_difficulty)
    candidates = []
    for row in questions:
        if int(row.get("canonical_unit_id") or -1) not in unit_ids:
            continue
        if not allow_review and row.get("mapping_needs_review"):
            continue
        if difficulty_rank(row.get("difficulty")) > max_rank:
            continue
        if not has_answer(row):
            continue
        candidates.append(row)
    candidates.sort(key=lambda row: (difficulty_rank(row.get("difficulty")), str(row.get("id"))))
    return candidates[:limit]

def fallback_theory_pack(context: dict[str, Any], reason: str) -> dict[str, Any]:
    focus = context["lesson_focus"]
    return {
        "title_note": f"Nền tảng: {focus}",
        "learning_outcomes": [
            f"Nắm được công thức/quy tắc cốt lõi: {focus}",
            "Áp dụng được quy trình giải vào bài mức nhận biết và thông hiểu",
        ],
        "formula_table": [
            {"y": "[chưa có dữ liệu]", "dy": "...", "note": "Giáo viên bổ sung trước khi phát hành"},
        ],
        "rule_boxes": [
            {
                "number": 1,
                "title": "Quy tắc cơ bản",
                "statement": f"[Công thức chính của {focus}]",
                "note": "Giáo viên điền công thức chuẩn",
                "example": "[Ví dụ ngắn áp dụng]",
            }
        ],
        "worked_examples": [
            {
                "number": 1,
                "problem": f"[Bài ví dụ mẫu cho {focus}]",
                "solution_steps": ["Bước 1: Đọc đề, xác định dạng", "Bước 2: Chọn công thức/quy tắc", "Bước 3: Thực hiện tính toán", "Bước 4: Kiểm tra kết quả"],
                "answer": "[Đáp số]",
            }
        ],
        "remarks": [
            {"label": "Lưu ý", "content": "Giáo viên bổ sung nhận xét quan trọng trước khi phát hành"}
        ],
        "common_mistakes": [
            {"mistake": "Nhớ công thức nhưng không kiểm tra điều kiện áp dụng", "correction": "Luôn đọc lại yêu cầu và điều kiện trước khi áp dụng"},
            {"mistake": "Tính toán đúng nhưng kết luận sai dạng", "correction": "Đọc kỹ câu hỏi yêu cầu tìm gì"},
        ],
        "solving_process": ["Đọc đề, gạch chân dữ kiện", "Nhận dạng dạng bài", "Chọn công thức/quy tắc", "Thực hiện từng bước", "Kiểm tra đáp án"],
        "pre_class_self_check": [
            "Em có nhớ công thức/quy tắc chính không?",
            "Em nhận ra dạng bài bằng dấu hiệu nào?",
            "Em có thể làm một câu cơ bản trong 2-3 phút không?",
        ],
        "_fallback_reason": reason,
    }

def call_ai_theory_pack(
    context: dict[str, Any],
    cache_path: Path,
    refresh_ai: bool,
    source_content: str = "",
) -> dict[str, Any]:
    if cache_path.exists() and not refresh_ai:
        return read_json(cache_path)
    client = anthropic.Anthropic(api_key=API_KEY, base_url=f"{normalize_base_url(BASE_URL)}/v1", timeout=AI_TIMEOUT_SECONDS)

    parts = ["Dữ liệu buổi học:\n" + json.dumps(context, ensure_ascii=False, indent=2)[:6000]]
    if source_content:
        parts.append("\n\n--- NỘI DUNG TÀI LIỆU GỐC ---\n" + source_content[:10000])
        parts.append("\n--- HẾT NỘI DUNG GỐC ---\n\nDựa trên nội dung gốc trên, hãy viết lại theo schema JSON.")
    else:
        parts.append("\n\nKhông có tài liệu gốc. Dùng kiến thức chuẩn THPT để tạo nội dung.")

    resp = client.messages.create(
        model=MODEL,
        max_tokens=6000,
        temperature=0.2,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "".join(parts)}],
    )
    text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
    data = extract_json(text)
    if not data:
        raise ValueError(f"AI response is not valid JSON: {text[:500]}")
    write_json(cache_path, data)
    return data

def build_material(
    root: Path,
    week_no: int,
    session_no: int,
    no_ai: bool,
    refresh_ai: bool,
    no_vision: bool = False,
    refresh_vision: bool = False,
) -> dict[str, Any]:
    roadmap = read_json(root / "output_json" / "math_teaching_roadmap.json")
    canonical = read_json(root / "output_json" / "canonical_roadmap.json")
    scan_data = read_json(root / "output_json" / "curriculum_scan.json")
    question_rows = read_json(root / "output_json" / "question_canonical_mapping.json").get("rows", [])
    week, session = find_session(roadmap, week_no, session_no)
    context = build_context(week, session, canonical, question_rows)
    unit_ids = context["canonical_unit_ids"]
    p_hash = prompt_hash(context)
    key = session_key(week_no, session_no)

    # ── Vision: extract source content from PDF pages ──────────────────────
    source_content = ""
    vision_status = "skipped"
    warnings = []

    if not no_ai and not no_vision and unit_ids:
        primary_unit = unit_ids[0]
        pdf_path = find_source_pdf(root, scan_data, primary_unit)
        if pdf_path:
            img_cache = root / "cache" / "lesson_materials" / "pdf_pages" / f"unit_{primary_unit:03d}"
            vision_cache = root / "cache" / "lesson_materials" / "vision" / f"unit_{primary_unit:03d}_raw.txt"
            try:
                print(f"Rendering PDF: {pdf_path.name} ({pdf_path.stat().st_size//1024} KB)")
                image_paths = render_pdf_pages(pdf_path, img_cache)
                print(f"  → {len(image_paths)} pages rendered")
                client = anthropic.Anthropic(
                    api_key=API_KEY,
                    base_url=f"{normalize_base_url(BASE_URL)}/v1",
                    timeout=VISION_TIMEOUT_SECONDS,
                )
                source_content = extract_source_via_vision(
                    client, image_paths, vision_cache, refresh=refresh_vision
                )
                vision_status = "cached" if (vision_cache.exists() and not refresh_vision) else "fresh"
                print(f"  → Vision extract: {len(source_content)} chars ({vision_status})")
            except Exception as exc:
                warnings.append(f"Vision extract failed: {exc}")
                vision_status = f"error: {exc}"
        else:
            warnings.append(f"No source PDF found for unit {primary_unit}")
            vision_status = "no_pdf"

    cache_path = root / "cache" / "lesson_materials" / "theory_pack" / f"{key}_{p_hash}.json"

    if no_ai:
        theory_pack = fallback_theory_pack(context, "no_ai")
    else:
        try:
            theory_pack = call_ai_theory_pack(
                context, cache_path, refresh_ai=refresh_ai, source_content=source_content
            )
        except Exception as exc:
            warnings.append(f"AI theory pack failed: {exc}")
            theory_pack = fallback_theory_pack(context, str(exc))

    unit_ids = context["canonical_unit_ids"]
    pre_class_questions = select_questions(question_rows, unit_ids, "Thông hiểu", 5, allow_review=False)
    in_class_questions = select_questions(question_rows, unit_ids, "Vận dụng", 15, allow_review=False)
    homework_questions = select_questions(question_rows, unit_ids, "Vận dụng", 18, allow_review=False)
    if len(pre_class_questions) < 5:
        warnings.append(f"Only {len(pre_class_questions)}/5 clean pre-class questions available")
    if len(in_class_questions) < 10:
        warnings.append(f"Only {len(in_class_questions)}/10 clean in-class questions available")
    if len(homework_questions) < 12:
        warnings.append(f"Only {len(homework_questions)}/12 clean homework questions available")

    material = {
        "material_id": key,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "week": week_no,
        "session_no": session_no,
        "context": context,
        "theory_pack": theory_pack,
        "questions": {
            "pre_class_exercise": [compact_question(row) for row in pre_class_questions],
            "in_class_practice": [compact_question(row) for row in in_class_questions],
            "post_class_homework": [compact_question(row) for row in homework_questions],
        },
        "ai_metadata": {
            "ai_generated": not no_ai and "_fallback_reason" not in theory_pack,
            "needs_human_review": True,
            "model": None if no_ai else MODEL,
            "base_url": normalize_base_url(BASE_URL),
            "prompt_hash": p_hash,
            "cache_path": str(cache_path),
            "vision_status": vision_status,
            "source_content_chars": len(source_content),
        },
        "warnings": warnings,
    }
    return material

KATEX_HEAD = """<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false}],throwOnError:false})"></script>
"""

STYLE = """<style>
*{box-sizing:border-box}
body{font-family:'Times New Roman',Times,serif;margin:0;background:#fff;color:#111;font-size:14px;line-height:1.6}
main{max-width:900px;margin:0 auto;padding:28px 32px}
/* Header */
.doc-header{border-bottom:3px solid #1a56a0;padding-bottom:10px;margin-bottom:20px}
.doc-header h1{font-size:18px;margin:0 0 2px;color:#1a56a0}
.doc-header .subtitle{font-size:13px;color:#555}
/* Section titles */
h2.sec{font-size:15px;font-weight:bold;color:#1a56a0;border-bottom:1px solid #1a56a0;padding-bottom:3px;margin:22px 0 10px}
h3.subsec{font-size:13px;font-weight:bold;margin:14px 0 6px;color:#222}
/* Formula table */
table.formula-tbl{border-collapse:collapse;width:100%;margin:8px 0 14px;font-size:13px}
table.formula-tbl th{background:#1a56a0;color:#fff;padding:5px 10px;text-align:center;font-weight:bold}
table.formula-tbl td{border:1px solid #aac;padding:5px 10px;text-align:center;vertical-align:middle}
table.formula-tbl td.note{text-align:left;color:#555;font-size:12px}
table.formula-tbl tr:nth-child(even) td{background:#f0f4fb}
/* Rule box */
.rule-box{border:1.5px solid #1a56a0;border-radius:4px;margin:10px 0 14px;overflow:hidden}
.rule-box .rule-title{background:#1a56a0;color:#fff;font-weight:bold;padding:5px 12px;font-size:13px}
.rule-box .rule-body{padding:10px 14px}
.rule-box .rule-stmt{font-weight:bold;font-size:14px;color:#1a1a1a;margin-bottom:4px}
.rule-box .rule-note{font-size:12px;color:#555;font-style:italic;margin-bottom:4px}
.rule-box .rule-example{font-size:13px;color:#1a56a0;border-left:3px solid #1a56a0;padding-left:8px;margin-top:6px}
/* Worked example */
.example-box{border:1px solid #d1a020;border-radius:4px;margin:10px 0 14px;overflow:hidden}
.example-box .ex-title{background:#f59e0b;color:#fff;font-weight:bold;padding:5px 12px;font-size:13px}
.example-box .ex-body{padding:10px 14px}
.example-box .ex-problem{font-weight:bold;margin-bottom:8px}
.example-box .ex-steps{margin:0;padding-left:20px}
.example-box .ex-steps li{margin:3px 0;font-size:13px}
.example-box .ex-answer{margin-top:8px;font-weight:bold;color:#0a6640;border-top:1px dashed #ccc;padding-top:6px}
/* Remark box */
.remark-box{background:#fff7e6;border-left:4px solid #f59e0b;padding:8px 14px;margin:10px 0;border-radius:0 4px 4px 0}
.remark-box .remark-label{font-weight:bold;color:#b45309;font-size:13px}
.remark-box .remark-text{font-size:13px;margin-top:3px}
/* Common mistakes */
.mistake-table{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}
.mistake-table th{background:#fee2e2;color:#b42318;padding:5px 10px;text-align:left;border:1px solid #fca5a5}
.mistake-table td{border:1px solid #e5e7eb;padding:6px 10px;vertical-align:top}
.mistake-table td.wrong{color:#b42318}
.mistake-table td.right{color:#166534}
/* Questions */
.q-block{border-top:1px solid #e5e7eb;padding:10px 0}
.q-num{font-weight:bold;color:#1a56a0}
.q-diff{font-size:11px;color:#6b7280;background:#f3f4f6;padding:1px 6px;border-radius:3px;margin-left:6px}
.q-text{margin:5px 0 8px}
.q-options{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:1fr 1fr;gap:3px 16px}
.q-options li{font-size:13px}
.q-options li .lbl{font-weight:bold;color:#374151;margin-right:4px}
.q-answer{margin-top:6px;font-weight:bold;color:#0f766e;font-size:13px}
/* Solving process */
ol.steps{margin:6px 0 6px 20px;padding:0}
ol.steps li{margin:3px 0;font-size:13px}
/* Meta/warnings */
.warn-list{color:#b42318;font-size:12px;margin:4px 0;padding-left:16px}
.meta-box{background:#f8fafc;border:1px dashed #94a3b8;border-radius:4px;padding:8px 12px;font-size:12px;color:#475569;margin:6px 0}
/* No-content placeholder */
.no-content{color:#9ca3af;font-style:italic;font-size:13px}
ul{margin:5px 0 5px 22px;padding:0}
li{margin:2px 0}
</style>"""


def render_question_list(questions: list[dict[str, Any]], show_answers: bool) -> str:
    if not questions:
        return "<p class='no-content'>Chưa có câu hỏi sạch phù hợp trong question bank.</p>"
    items = []
    for index, question in enumerate(questions, start=1):
        opts = []
        for label in ["a", "b", "c", "d"]:
            value = question.get(f"option_{label}")
            if value:
                opts.append(f"<li><span class='lbl'>{label.upper()}.</span>{safe_html(str(value))}</li>")
        answer = ""
        if show_answers:
            answer_value = question.get("correct_answer") or question.get("numeric_answer")
            if answer_value is not None:
                answer = f"<div class='q-answer'>Đáp án: {safe_html(str(answer_value))}</div>"
        diff_tag = f"<span class='q-diff'>{html.escape(str(question.get('difficulty') or ''))}</span>" if question.get("difficulty") else ""
        items.append(
            f"<div class='q-block'>"
            f"<div><span class='q-num'>Câu {index}.</span>{diff_tag}</div>"
            f"<div class='q-text'>{safe_html(str(question.get('question_text') or ''))}</div>"
            f"{('<ul class=\"q-options\">' + ''.join(opts) + '</ul>') if opts else ''}"
            f"{answer}</div>"
        )
    return "".join(items)


def render_formula_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    trs = "".join(
        f"<tr><td>{safe_html(str(r.get('y','')))} </td><td>{safe_html(str(r.get('dy','')))} </td><td class='note'>{safe_html(str(r.get('note','')))} </td></tr>"
        for r in rows
    )
    return (
        "<table class='formula-tbl'>"
        "<tr><th>$y = f(x)$</th><th>$y' = f'(x)$</th><th>Ghi chú</th></tr>"
        f"{trs}</table>"
    )


def render_rule_boxes(boxes: list[dict[str, Any]]) -> str:
    out = []
    for box in boxes or []:
        note_html = f"<div class='rule-note'>{safe_html(str(box.get('note','')))}</div>" if box.get('note') else ""
        example_html = f"<div class='rule-example'><em>Ví dụ:</em> {safe_html(str(box.get('example','')))}</div>" if box.get('example') else ""
        out.append(
            f"<div class='rule-box'>"
            f"<div class='rule-title'>Quy tắc {box.get('number','')}: {html.escape(str(box.get('title','')))} </div>"
            f"<div class='rule-body'>"
            f"<div class='rule-stmt'>{safe_html(str(box.get('statement','')))} </div>"
            f"{note_html}{example_html}"
            f"</div></div>"
        )
    return "".join(out)


def render_worked_examples(examples: list[dict[str, Any]]) -> str:
    out = []
    for ex in examples or []:
        steps = ex.get("solution_steps") or []
        steps_html = "<ol class='ex-steps'>" + "".join(f"<li>{safe_html(str(s))}</li>" for s in steps) + "</ol>"
        answer_html = f"<div class='ex-answer'>Kết quả: {safe_html(str(ex.get('answer','')))}</div>" if ex.get('answer') else ""
        out.append(
            f"<div class='example-box'>"
            f"<div class='ex-title'>Ví dụ {ex.get('number','')}</div>"
            f"<div class='ex-body'>"
            f"<div class='ex-problem'>{safe_html(str(ex.get('problem','')))}</div>"
            f"<strong>Lời giải:</strong>{steps_html}"
            f"{answer_html}"
            f"</div></div>"
        )
    return "".join(out)


def render_remarks(remarks: list[dict[str, Any]]) -> str:
    out = []
    for r in remarks or []:
        out.append(
            f"<div class='remark-box'>"
            f"<div class='remark-label'>{html.escape(str(r.get('label','Nhận xét')))}</div>"
            f"<div class='remark-text'>{safe_html(str(r.get('content','')))}</div>"
            f"</div>"
        )
    return "".join(out)


def render_mistake_table(mistakes: list[dict[str, Any]]) -> str:
    if not mistakes:
        return ""
    rows = "".join(
        f"<tr><td class='wrong'>{safe_html(str(m.get('mistake','')))} </td><td class='right'>{safe_html(str(m.get('correction','')))} </td></tr>"
        for m in mistakes
    )
    return (
        "<table class='mistake-table'>"
        "<tr><th>Lỗi sai thường gặp</th><th>Cách đúng</th></tr>"
        f"{rows}</table>"
    )


def render_student_html(material: dict[str, Any]) -> str:
    ctx = material["context"]
    theory = material["theory_pack"]
    week, session = ctx['week'], ctx['session_no']
    focus = html.escape(ctx['lesson_focus'])
    title_note = html.escape(theory.get('title_note') or focus)

    outcomes = "".join(f"<li>{safe_html(str(o))}</li>" for o in (theory.get('learning_outcomes') or []))
    solving = "".join(f"<li>{safe_html(str(s))}</li>" for s in (theory.get('solving_process') or []))
    self_check = "".join(f"<li>{safe_html(str(s))}</li>" for s in (theory.get('pre_class_self_check') or []))

    formula_html = render_formula_table(theory.get('formula_table') or [])
    rules_html = render_rule_boxes(theory.get('rule_boxes') or [])
    examples_html = render_worked_examples(theory.get('worked_examples') or [])
    remarks_html = render_remarks(theory.get('remarks') or [])
    mistake_html = render_mistake_table(theory.get('common_mistakes') or [])
    preclass_html = render_question_list(material['questions']['pre_class_exercise'], show_answers=False)

    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<title>Tuần {week} Buổi {session}</title>{KATEX_HEAD}{STYLE}</head><body><main>
<div class="doc-header">
  <h1>Tuần {week} · Buổi {session} — {focus}</h1>
  <div class="subtitle">{title_note}</div>
</div>

<h2 class="sec">Mục tiêu buổi học</h2>
<ul>{outcomes}</ul>

{('<h2 class="sec">1. Bảng công thức</h2>' + formula_html) if formula_html else ''}

{('<h2 class="sec">2. Quy tắc</h2>' + rules_html) if rules_html else ''}

{remarks_html}

{('<h2 class="sec">3. Ví dụ mẫu</h2>' + examples_html) if examples_html else ''}

{('<h2 class="sec">4. Lỗi sai thường gặp</h2>' + mistake_html) if mistake_html else ''}

{('<h2 class="sec">5. Quy trình giải</h2><ol class="steps">' + solving + '</ol>') if solving else ''}

<h2 class="sec">Bài tập trước buổi</h2>
{preclass_html}

<h2 class="sec">Tự kiểm tra trước buổi</h2>
<ul>{self_check}</ul>

</main></body></html>"""


def render_teacher_html(material: dict[str, Any]) -> str:
    ctx = material["context"]
    theory = material["theory_pack"]
    week, session = ctx['week'], ctx['session_no']
    focus = html.escape(ctx['lesson_focus'])

    warnings_html = ""
    if material.get("warnings"):
        w_items = "".join(f"<li>{html.escape(w)}</li>" for w in material["warnings"])
        warnings_html = f"<ul class='warn-list'>{w_items}</ul>"

    segments = ctx.get("live_class_plan", {}).get("segments", [])
    plan_html = "".join(
        f"<li><strong>{html.escape(str(s.get('minutes','?')))} phút</strong> — {html.escape(str(s.get('activity','')))}</li>"
        for s in segments
    ) if segments else "<li class='no-content'>Không có dữ liệu kế hoạch</li>"

    inclass_html = render_question_list(material['questions']['in_class_practice'], show_answers=True)
    homework_html = render_question_list(material['questions']['post_class_homework'], show_answers=True)
    preclass_html = render_question_list(material['questions']['pre_class_exercise'], show_answers=True)

    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<title>Teacher: Tuần {week} Buổi {session}</title>{KATEX_HEAD}{STYLE}</head><body><main>
<div class="doc-header">
  <h1>Teacher Guide — Tuần {week} · Buổi {session}: {focus}</h1>
  <div class="subtitle">Units: {', '.join(map(str, ctx['canonical_unit_ids']))} · AI review: cần kiểm tra</div>
</div>
{warnings_html}

<div class="meta-box">
  <strong>Model:</strong> {html.escape(str(material['ai_metadata'].get('model') or 'fallback'))} &nbsp;|&nbsp;
  <strong>AI:</strong> {'có' if material['ai_metadata']['ai_generated'] else 'fallback'} &nbsp;|&nbsp;
  <strong>Hash:</strong> {html.escape(str(material['ai_metadata'].get('prompt_hash','')))}
</div>

<h2 class="sec">Kế hoạch live 90 phút</h2>
<ol class="steps">{plan_html}</ol>

<h2 class="sec">Theory Pack (review)</h2>
<h3 class="subsec">Mục tiêu</h3>
<ul>{"".join(f'<li>{safe_html(str(o))}</li>' for o in (theory.get("learning_outcomes") or []))}</ul>
{render_formula_table(theory.get("formula_table") or [])}
{render_rule_boxes(theory.get("rule_boxes") or [])}
{render_worked_examples(theory.get("worked_examples") or [])}
{render_remarks(theory.get("remarks") or [])}
{render_mistake_table(theory.get("common_mistakes") or [])}

<h2 class="sec">Pre-class (có đáp án)</h2>
{preclass_html}

<h2 class="sec">In-class Practice</h2>
{inclass_html}

<h2 class="sec">Homework</h2>
{homework_html}

</main></body></html>"""

def save_material(root: Path, material: dict[str, Any]) -> None:
    key = material["material_id"]
    out_json = root / "output_json" / "lesson_materials" / f"{key}.json"
    student_html = root / "previews" / "lesson_materials" / f"{key}_student.html"
    teacher_html = root / "previews" / "lesson_materials" / f"{key}_teacher.html"
    write_json(out_json, material)
    write_text(student_html, render_student_html(material))
    write_text(teacher_html, render_teacher_html(material))
    write_text(root / "previews" / "lesson_materials" / "index.html", render_index(root))

def render_index(root: Path) -> str:
    items = []
    preview_dir = root / "previews" / "lesson_materials"
    for student in sorted(preview_dir.glob("week_*_session_*_student.html")):
        teacher = student.with_name(student.name.replace("_student", "_teacher"))
        label = student.name.replace("_student.html", "")
        items.append(f"<tr><td>{html.escape(label)}</td><td><a href='{student.name}'>Student</a></td><td><a href='{teacher.name}'>Teacher</a></td></tr>")
    return f"<!doctype html><html lang='vi'><head><meta charset='utf-8'><title>Lesson Materials</title>{STYLE}</head><body><main><h1>Lesson Materials</h1><table>{''.join(items)}</table></main></body></html>"

def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Build AI-assisted lesson material for one roadmap session.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--week", type=int, default=1)
    parser.add_argument("--session", type=int, default=1)
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--refresh-ai", action="store_true")
    parser.add_argument("--no-vision", action="store_true", help="Skip PDF vision extraction")
    parser.add_argument("--refresh-vision", action="store_true", help="Force re-run vision even if cached")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    root = Path(args.root)
    material = build_material(
        root, args.week, args.session,
        no_ai=args.no_ai or args.validate_only,
        refresh_ai=args.refresh_ai,
        no_vision=args.no_vision,
        refresh_vision=args.refresh_vision,
    )
    print(f"Material: {material['material_id']}")
    print(f"Warnings: {len(material['warnings'])}")
    for warning in material["warnings"]:
        print(f"WARN: {warning}")
    if not args.validate_only:
        save_material(root, material)
        print(f"Wrote {(root / 'output_json' / 'lesson_materials' / (material['material_id'] + '.json')).resolve()}")
        print(f"Wrote {(root / 'previews' / 'lesson_materials' / (material['material_id'] + '_student.html')).resolve()}")
        print(f"Wrote {(root / 'previews' / 'lesson_materials' / (material['material_id'] + '_teacher.html')).resolve()}")

if __name__ == "__main__":
    main()
