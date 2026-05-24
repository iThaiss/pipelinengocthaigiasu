import argparse
import copy
import unicodedata
import html
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic


DEFAULT_ROOT = Path("local_curriculum")
MODEL = os.getenv("CLAUDE_MODEL", "cc/claude-sonnet-4-6")
BASE_URL = os.getenv("CLAUDE_BASE_URL", "http://localhost:20128").rstrip("/")
API_KEY = os.getenv("NINEROUTER_API_KEY") or os.getenv("CLAUDE_API_KEY") or "local-9router"


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


def clip_list(values: Any, count: int = 4) -> list[str]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        text = str(value).strip()
        if text:
            result.append(text)
        if len(result) >= count:
            break
    return result


def fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.casefold())
    no_marks = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return no_marks.replace("đ", "d")


STOPWORDS = {
    "bai",
    "theme",
    "bon",
    "step",
    "ngoc",
    "huyen",
    "lb",
    "phan",
    "buoi",
    "dang",
    "btvn",
    "dung",
    "sai",
    "co",
    "cua",
    "va",
    "trong",
    "cho",
    "pdf",
    "toan",
    "thuc",
    "te",
    "ham",
    "so",
    "ung",
    "dung",
    "dao",
    "khao",
    "sat",
    "chuong",
    "lop",
    "hoc",
    "nang",
    "cao",
    "co",
    "ban",
    "lien",
    "quan",
    "tinh",
    "chat",
    "cac",
    "mot",
    "nhieu",
    "dinh",
    "nghia",
    "ly",
    "phuong",
    "trinh",
    "mat",
}


def tokens_for_lesson(lesson: dict[str, Any]) -> set[str]:
    analysis = lesson["analysis"]
    text_parts = [
        lesson.get("file_name") or "",
        analysis.get("lesson_title") or "",
        " ".join(clip_list(analysis.get("concepts"), 10)),
        " ".join(clip_list(analysis.get("objectives"), 6)),
    ]
    folded = fold_text(" ".join(text_parts))
    words = re.findall(r"[a-z0-9]+", folded)
    return {word for word in words if len(word) >= 3 and word not in STOPWORDS and not word.isdigit()}


def title_tokens_for_lesson(lesson: dict[str, Any]) -> set[str]:
    analysis = lesson["analysis"]
    text_parts = [
        lesson.get("file_name") or "",
        analysis.get("lesson_title") or "",
    ]
    folded = fold_text(" ".join(text_parts))
    words = re.findall(r"[a-z0-9]+", folded)
    tokens = {word for word in words if len(word) >= 3 and word not in STOPWORDS and not word.isdigit()}
    if {"don", "dieu"} <= tokens:
        tokens.update({"dong", "nghich", "bien"})
    if {"dong", "bien"} <= tokens or {"nghich", "bien"} <= tokens:
        tokens.update({"don", "dieu"})
    if "maxmin" in tokens or {"lon", "nhat"} <= tokens or {"nho", "nhat"} <= tokens:
        tokens.update({"gtln", "gtnn", "maxmin"})
    if {"tich", "phan"} <= tokens:
        tokens.update({"integral"})
    if {"nguyen"} <= tokens:
        tokens.update({"antiderivative"})
    return tokens


def category_for_lesson(lesson: dict[str, Any]) -> str:
    analysis = lesson["analysis"]
    text = fold_text(
        " ".join(
            [
                lesson.get("folder_path") or "",
                lesson.get("file_name") or "",
                analysis.get("lesson_title") or "",
                analysis.get("program_area") or "",
                analysis.get("chapter") or "",
            ]
        )
    )
    if any(key in text for key in ["xac suat", "bayes", "thong ke", "phuong sai", "lech chuan", "mau so lieu"]):
        return "statistics_probability"
    if any(key in text for key in ["nguyen ham", "tich phan"]):
        return "integral"
    if any(key in text for key in ["don dieu", "cuc tri", "maxmin", "tiem can", "khao sat", "dao ham"]):
        return "derivative"
    if any(
        key in text
        for key in [
            "oxyz",
            "toa do",
            "vecto",
            "vector",
            "mat phang",
            "duong thang",
            "mat cau",
            "tich co huong",
            "he truc",
            "hinh hoc",
            "goc trong khong gian",
        ]
    ):
        return "geometry_oxyz"
    if any(key in text for key in ["gioi han", "lien tuc"]):
        return "prerequisite"
    return "other"


def lesson_role(analysis: dict[str, Any]) -> str:
    lesson_type = analysis.get("lesson_type")
    if lesson_type == "homework":
        return "homework"
    if lesson_type in {"practice", "test"}:
        return "practice"
    if lesson_type == "bonus":
        return "extension"
    return "core"


def merge_unique(existing: list[str], values: Any, limit: int = 6) -> list[str]:
    result = list(existing)
    seen = {fold_text(item) for item in result}
    for value in clip_list(values, limit):
        key = fold_text(value)
        if key not in seen:
            result.append(value)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


def similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    return overlap / max(1, len(left | right))


def build_unit(order: int, lesson: dict[str, Any]) -> dict[str, Any]:
    analysis = lesson["analysis"]
    return {
        "order": order,
        "canonical_title": analysis.get("lesson_title") or lesson["file_name"],
        "program_area": analysis.get("program_area") or lesson.get("folder_path") or "Chưa phân loại",
        "phase": analysis.get("phase") or "Kết hợp",
        "source_lessons": [
            {
                "source": lesson["source"],
                "source_order": lesson["order"],
                "lesson_title": analysis.get("lesson_title") or lesson["file_name"],
                "role": lesson_role(analysis),
            }
        ],
        "learning_goals": clip_list(analysis.get("objectives"), 6),
        "prerequisites": clip_list(analysis.get("prerequisites"), 6),
        "teaching_strategy": clip_list(analysis.get("teaching_methods"), 6),
        "application_types": clip_list(analysis.get("application_questions"), 6),
        "recommended_sequence_note": "Giữ vị trí theo thứ tự nguồn gốc; bài từ nguồn khác được ghép nếu trùng chủ đề.",
        "best_source_notes": clip_list(analysis.get("source_strengths"), 5),
        "gaps_to_fill": clip_list(analysis.get("gaps_or_warnings"), 5),
        "_tokens": sorted(tokens_for_lesson(lesson)),
        "_title_tokens": sorted(title_tokens_for_lesson(lesson)),
        "_category": category_for_lesson(lesson),
    }


def merge_lesson_into_unit(unit: dict[str, Any], lesson: dict[str, Any]) -> None:
    analysis = lesson["analysis"]
    unit["source_lessons"].append(
        {
            "source": lesson["source"],
            "source_order": lesson["order"],
            "lesson_title": analysis.get("lesson_title") or lesson["file_name"],
            "role": lesson_role(analysis),
        }
    )
    unit["learning_goals"] = merge_unique(unit.get("learning_goals", []), analysis.get("objectives"), 6)
    unit["prerequisites"] = merge_unique(unit.get("prerequisites", []), analysis.get("prerequisites"), 6)
    unit["teaching_strategy"] = merge_unique(unit.get("teaching_strategy", []), analysis.get("teaching_methods"), 6)
    unit["application_types"] = merge_unique(unit.get("application_types", []), analysis.get("application_questions"), 6)
    unit["best_source_notes"] = merge_unique(unit.get("best_source_notes", []), analysis.get("source_strengths"), 5)
    unit["gaps_to_fill"] = merge_unique(unit.get("gaps_to_fill", []), analysis.get("gaps_or_warnings"), 5)
    unit["_tokens"] = sorted(set(unit.get("_tokens", [])) | tokens_for_lesson(lesson))
    unit["_title_tokens"] = sorted(set(unit.get("_title_tokens", [])) | title_tokens_for_lesson(lesson))


def compact_lessons(scan: dict[str, Any], max_chars: int) -> str:
    blocks = []
    for lesson in scan["lessons"]:
        analysis = lesson["analysis"]
        block = {
            "source": lesson["source"],
            "order": lesson["order"],
            "folder": lesson.get("folder_path"),
            "file": lesson["file_name"],
            "title": analysis.get("lesson_title"),
            "type": analysis.get("lesson_type"),
            "phase": analysis.get("phase"),
            "objectives": clip_list(analysis.get("objectives"), 5),
            "concepts": clip_list(analysis.get("concepts"), 8),
            "prerequisites": clip_list(analysis.get("prerequisites"), 5),
            "methods": clip_list(analysis.get("teaching_methods"), 5),
            "applications": clip_list(analysis.get("application_questions"), 5),
            "strengths": clip_list(analysis.get("source_strengths"), 4),
            "warnings": clip_list(analysis.get("gaps_or_warnings"), 3),
        }
        blocks.append(json.dumps(block, ensure_ascii=False))

    text = "\n".join(blocks)
    if len(text) <= max_chars:
        return text

    by_source: dict[str, list[str]] = {}
    for block in blocks:
        match = re.search(r'"source":\s*"([^"]+)"', block)
        source = match.group(1) if match else "UNKNOWN"
        by_source.setdefault(source, []).append(block)

    selected = []
    budget_per_source = max_chars // max(1, len(by_source))
    for rows in by_source.values():
        current = ""
        for row in rows:
            if len(current) + len(row) + 1 > budget_per_source:
                break
            current += row + "\n"
        selected.append(current)
    return "\n".join(selected)[:max_chars]


def extract_json_object(value: str) -> dict[str, Any]:
    value = value.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?", "", value).strip()
        value = re.sub(r"```$", "", value).strip()
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object found in AI response")
    return json.loads(value[start : end + 1])


def subject_synthesis_prompt(subject: str) -> tuple[str, str, str]:
    if subject == "english":
        return (
            "Bạn là kiến trúc sư chương trình học Tiếng Anh cho học sinh Việt Nam. "
            "Nhiệm vụ là hợp nhất roadmap từ nhiều nguồn local đã scan. "
            "Chỉ trả về JSON object hợp lệ, không markdown.",
            "Hãy tạo roadmap tổng hợp theo kỹ năng/chủ điểm tiếng Anh. Nếu hai nguồn dạy cùng chủ điểm hoặc cùng dạng bài, gom vào cùng roadmap unit và ghi rõ nguồn nào cung cấp gì.",
            '"Foundation|Practice|Application|Test Prep|Mixed"',
        )
    return (
        "Bạn là kiến trúc sư chương trình học Toán THPT. "
        "Nhiệm vụ là hợp nhất roadmap từ nhiều nguồn local đã scan. "
        "Chỉ trả về JSON object hợp lệ, không markdown.",
        "Hãy tạo roadmap tổng hợp theo chương trình, không bỏ qua các lesson quan trọng. Nếu hai nguồn dạy cùng chủ đề, gom vào cùng roadmap unit và ghi rõ nguồn nào cung cấp gì.",
        '"Nền tảng|Vận dụng|Vận dụng cao|Bài tập|Kết hợp"',
    )


def synthesize_with_ai(client: anthropic.Anthropic, scan: dict[str, Any], max_chars: int, subject: str) -> dict[str, Any]:
    lessons_text = compact_lessons(scan, max_chars=max_chars)
    system, roadmap_instruction, phase_schema = subject_synthesis_prompt(subject)
    user = f"""
Dữ liệu gồm lesson summaries từ từng nguồn. Thứ tự trong mỗi nguồn là source of truth.

{roadmap_instruction}
Giới hạn đầu ra:
- Tạo tối đa 35 roadmap_units.
- Mỗi array tối đa 4 ý ngắn.
- Mỗi recommended_sequence_note tối đa 1 câu.
- Trả JSON compact, không giải thích ngoài JSON.

Schema JSON:
{{
  "title": "string",
  "summary": "string",
  "source_profiles": [
    {{
      "source": "string",
      "teaching_style": "string",
      "strengths": ["string"],
      "weaknesses_or_gaps": ["string"]
    }}
  ],
  "roadmap_units": [
    {{
      "order": 1,
      "canonical_title": "string",
      "program_area": "string",
      "phase": {phase_schema},
      "source_lessons": [
        {{"source": "string", "source_order": 1, "lesson_title": "string", "role": "core|practice|extension|homework"}}
      ],
      "learning_goals": ["string"],
      "prerequisites": ["string"],
      "teaching_strategy": ["string"],
      "application_types": ["string"],
      "recommended_sequence_note": "string",
      "best_source_notes": ["string"],
      "gaps_to_fill": ["string"]
    }}
  ],
  "global_gaps": ["string"],
  "next_actions": ["string"]
}}

Lesson summaries:
{lessons_text}
"""
    response = client.messages.create(
        model=MODEL,
        max_tokens=12000,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    return extract_json_object(text)


def fallback_roadmap(scan: dict[str, Any]) -> dict[str, Any]:
    source_priority = {"LB": 0, "NGPHANTIEN": 1}
    lessons = sorted(
        scan["lessons"],
        key=lambda lesson: (source_priority.get(lesson["source"], 9), lesson["order"]),
    )

    units = []
    for lesson in lessons:
        lesson_tokens = tokens_for_lesson(lesson)
        lesson_title_tokens = title_tokens_for_lesson(lesson)
        lesson_category = category_for_lesson(lesson)
        best_index = None
        best_score = 0.0
        for index, unit in enumerate(units):
            unit_tokens = set(unit.get("_tokens", []))
            unit_category = unit.get("_category") or "other"
            if lesson_category != "other" and unit_category != "other" and lesson_category != unit_category:
                continue
            title_score = similarity(lesson_title_tokens, set(unit.get("_title_tokens", [])))
            full_score = similarity(lesson_tokens, unit_tokens)
            score = title_score * 0.75 + full_score * 0.25
            same_source = any(src["source"] == lesson["source"] for src in unit.get("source_lessons", []))
            if same_source and lesson_role(lesson["analysis"]) == "core":
                continue
            if same_source and score < 0.35:
                continue
            if score > best_score:
                best_index = index
                best_score = score

        if best_index is not None and best_score >= 0.18:
            merge_lesson_into_unit(units[best_index], lesson)
        else:
            units.append(build_unit(len(units) + 1, lesson))

    for index, unit in enumerate(units, start=1):
        unit["order"] = index
        unit.pop("_tokens", None)
        unit.pop("_title_tokens", None)
        unit.pop("_category", None)

    source_profiles = []
    for source in sorted({lesson["source"] for lesson in scan["lessons"]}):
        source_lessons = [lesson for lesson in scan["lessons"] if lesson["source"] == source]
        strengths: list[str] = []
        gaps: list[str] = []
        methods: list[str] = []
        for lesson in source_lessons:
            analysis = lesson["analysis"]
            strengths = merge_unique(strengths, analysis.get("source_strengths"), 6)
            gaps = merge_unique(gaps, analysis.get("gaps_or_warnings"), 6)
            methods = merge_unique(methods, analysis.get("teaching_methods"), 6)
        source_profiles.append(
            {
                "source": source,
                "teaching_style": "; ".join(methods[:3]) or "Chưa đủ tín hiệu rõ ràng.",
                "strengths": strengths[:5],
                "weaknesses_or_gaps": gaps[:5],
            }
        )

    return {
        "title": "Local Curriculum Roadmap",
        "summary": "Roadmap tổng hợp local, gom các bài tương đương bằng title/concept similarity và giữ thứ tự folder làm chuẩn.",
        "source_profiles": source_profiles,
        "roadmap_units": units,
        "global_gaps": [
            "Một số bài cùng chủ đề nhưng tên quá khác nhau có thể chưa được merge hoàn hảo.",
            "Các file scan text ít ký tự cần kiểm tra lại nếu PDF chủ yếu là ảnh.",
        ],
        "next_actions": [
            "Review preview HTML và chỉnh ngưỡng merge nếu thấy bài bị gom sai.",
            "Thêm MAPSTUDY/NGTIENDAT vào input_sources rồi chạy lại manifest, scan, synthesize.",
        ],
    }


def open_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute(
        """
        create table if not exists curriculum_roadmap_units (
            id integer primary key,
            canonical_title text not null,
            program_area text,
            phase text,
            source_lessons_json text not null,
            learning_goals_json text not null,
            prerequisites_json text not null,
            teaching_strategy_json text not null,
            application_types_json text not null,
            recommended_sequence_note text,
            best_source_notes_json text not null,
            gaps_to_fill_json text not null
        )
        """
    )
    return db


def save_roadmap_to_db(path: Path, roadmap: dict[str, Any]) -> None:
    db = open_db(path)
    db.execute("delete from curriculum_roadmap_units")
    for unit in roadmap.get("roadmap_units", []):
        db.execute(
            """
            insert into curriculum_roadmap_units (
                id, canonical_title, program_area, phase, source_lessons_json,
                learning_goals_json, prerequisites_json, teaching_strategy_json,
                application_types_json, recommended_sequence_note,
                best_source_notes_json, gaps_to_fill_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(unit.get("order") or 0),
                unit.get("canonical_title") or "",
                unit.get("program_area"),
                unit.get("phase"),
                json.dumps(unit.get("source_lessons") or [], ensure_ascii=False),
                json.dumps(unit.get("learning_goals") or [], ensure_ascii=False),
                json.dumps(unit.get("prerequisites") or [], ensure_ascii=False),
                json.dumps(unit.get("teaching_strategy") or [], ensure_ascii=False),
                json.dumps(unit.get("application_types") or [], ensure_ascii=False),
                unit.get("recommended_sequence_note"),
                json.dumps(unit.get("best_source_notes") or [], ensure_ascii=False),
                json.dumps(unit.get("gaps_to_fill") or [], ensure_ascii=False),
            ),
        )
    db.commit()
    db.close()


def render_html(roadmap: dict[str, Any]) -> str:
    profiles = []
    for profile in roadmap.get("source_profiles", []):
        profiles.append(
            f"""
            <section class="profile">
              <h2>{html.escape(profile.get("source", ""))}</h2>
              <p>{html.escape(profile.get("teaching_style", ""))}</p>
              <strong>Strengths</strong>
              <ul>{''.join(f'<li>{html.escape(str(x))}</li>' for x in profile.get('strengths', []))}</ul>
              <strong>Gaps</strong>
              <ul>{''.join(f'<li>{html.escape(str(x))}</li>' for x in profile.get('weaknesses_or_gaps', []))}</ul>
            </section>
            """
        )

    rows = []
    for unit in roadmap.get("roadmap_units", []):
        sources = "; ".join(
            f"{lesson.get('source')} #{lesson.get('source_order')}: {lesson.get('lesson_title')}"
            for lesson in unit.get("source_lessons", [])
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(unit.get('order', '')))}</td>"
            f"<td>{html.escape(unit.get('canonical_title') or '')}</td>"
            f"<td>{html.escape(unit.get('program_area') or '')}</td>"
            f"<td>{html.escape(unit.get('phase') or '')}</td>"
            f"<td>{html.escape(sources)}</td>"
            f"<td>{html.escape('; '.join(unit.get('learning_goals') or []))}</td>"
            f"<td>{html.escape('; '.join(unit.get('teaching_strategy') or []))}</td>"
            f"<td>{html.escape('; '.join(unit.get('application_types') or []))}</td>"
            f"<td>{html.escape('; '.join(unit.get('gaps_to_fill') or []))}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Curriculum Roadmap</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; color: #1f2328; background: #f6f7f9; }}
    header {{ padding: 24px 32px; background: #fff; border-bottom: 1px solid #d8dee4; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    main {{ padding: 24px 32px 40px; }}
    .profiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 24px; }}
    .profile {{ background: #fff; border: 1px solid #d8dee4; border-radius: 6px; padding: 16px; }}
    .profile h2 {{ margin: 0 0 8px; font-size: 18px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dee4; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid #eaeef2; vertical-align: top; text-align: left; font-size: 13px; }}
    th {{ position: sticky; top: 0; background: #eef2f5; z-index: 1; }}
    tr:hover td {{ background: #fafbfc; }}
    ul {{ margin: 6px 0 12px 20px; padding: 0; }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(roadmap.get("title", "Curriculum Roadmap"))}</h1>
    <div>{html.escape(roadmap.get("summary", ""))}</div>
  </header>
  <main>
    <section class="profiles">{''.join(profiles)}</section>
    <table>
      <thead>
        <tr>
          <th>#</th><th>Roadmap unit</th><th>Area</th><th>Phase</th><th>Source lessons</th>
          <th>Goals</th><th>Teaching</th><th>Applications</th><th>Gaps</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </main>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synthesize scanned local lessons into a combined roadmap.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--max-chars", type=int, default=70000)
    parser.add_argument("--subject", choices=["math", "english"], default="math")
    parser.add_argument("--no-ai", action="store_true")
    return parser.parse_args()


def main() -> None:
    configure_stdio()
    args = parse_args()
    root = Path(args.root)
    scan_path = root / "output_json" / "curriculum_scan.json"
    if not scan_path.exists():
        raise SystemExit(f"Missing scan file: {scan_path}. Run curriculum_scan.py first.")
    scan = read_json(scan_path)

    if args.no_ai:
        roadmap = fallback_roadmap(scan)
        ai_status = "fallback_no_ai"
    else:
        client = anthropic.Anthropic(api_key=API_KEY, base_url=normalize_base_url(BASE_URL))
        try:
            roadmap = synthesize_with_ai(client, scan, max_chars=args.max_chars, subject=args.subject)
            ai_status = "ok"
        except Exception as exc:
            roadmap = fallback_roadmap(scan)
            roadmap["global_gaps"] = [f"AI synthesis failed: {exc}"] + roadmap.get("global_gaps", [])
            ai_status = "fallback_ai_error"

    roadmap["_meta"] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ai_status": ai_status,
        "model": None if args.no_ai else MODEL,
        "subject": args.subject,
        "scan_file": str(scan_path.resolve()),
        "lesson_count": scan.get("lesson_count"),
    }

    json_path = root / "output_json" / "curriculum_roadmap.json"
    html_path = root / "previews" / "curriculum_roadmap.html"
    db_path = root / "output_sqlite" / "curriculum.sqlite"
    write_json(json_path, roadmap)
    html_path.write_text(render_html(roadmap), encoding="utf-8")
    save_roadmap_to_db(db_path, roadmap)

    print(f"AI status: {ai_status}")
    print(f"Roadmap units: {len(roadmap.get('roadmap_units', []))}")
    print(f"Wrote {json_path.resolve()}")
    print(f"Wrote {html_path.resolve()}")
    print(f"Updated {db_path.resolve()}")


if __name__ == "__main__":
    main()
