import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
import fitz

fitz.TOOLS.mupdf_display_errors(False)

DEFAULT_ROOT = Path("local_curriculum")
MODEL = os.getenv("CLAUDE_FAST_MODEL") or os.getenv("CLAUDE_MODEL", "cc/claude-haiku-4-5-20251001")
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


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_pdf_text(path: Path, cache_dir: Path) -> dict[str, Any]:
    digest = file_sha1(path)
    cache_path = cache_dir / f"{digest}.json"
    if cache_path.exists():
        cached = read_json(cache_path)
        cached["from_cache"] = True
        return cached

    pages: list[dict[str, Any]] = []
    started = time.time()
    with fitz.open(path) as doc:
        for index, page in enumerate(doc, start=1):
            text = page.get_text("text", sort=True).strip()
            pages.append(
                {
                    "page": index,
                    "text": text,
                    "char_count": len(text),
                }
            )

    result = {
        "sha1": digest,
        "file_name": path.name,
        "page_count": len(pages),
        "char_count": sum(page["char_count"] for page in pages),
        "pages": pages,
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        "extract_seconds": round(time.time() - started, 3),
        "from_cache": False,
    }
    write_json(cache_path, result)
    return result


def clean_line(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value


def interesting_lines(text: str, limit: int = 180) -> list[str]:
    lines = [clean_line(line) for line in text.splitlines()]
    lines = [line for line in lines if len(line) >= 8]

    selected: list[str] = []
    seen: set[str] = set()
    patterns = [
        r"^(bài|theme|chủ đề|dạng|phương pháp|ví dụ|câu|bài tập|nhận xét|định nghĩa|định lí|tính chất)",
        r"(mục tiêu|phương pháp|cách giải|áp dụng|vận dụng|nhận biết|thông hiểu|vận dụng cao)",
        r"^(I+\.|[A-Z]\.|[0-9]+\.|[0-9]+\))",
    ]

    for line in lines:
        folded = line.casefold()
        if any(re.search(pattern, folded) for pattern in patterns):
            key = folded[:160]
            if key not in seen:
                selected.append(line)
                seen.add(key)
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        for line in lines[: limit * 2]:
            key = line.casefold()[:160]
            if key not in seen:
                selected.append(line)
                seen.add(key)
            if len(selected) >= limit:
                break

    return selected[:limit]


def compact_document_text(extracted: dict[str, Any], max_chars: int) -> str:
    pages = extracted.get("pages", [])
    all_text = "\n\n".join(f"[Trang {page['page']}]\n{page['text']}" for page in pages if page.get("text"))
    if len(all_text) <= max_chars:
        return all_text

    head = "\n".join(
        f"[Trang {page['page']}]\n{page['text']}"
        for page in pages[:3]
        if page.get("text")
    )
    tail = "\n".join(
        f"[Trang {page['page']}]\n{page['text']}"
        for page in pages[-2:]
        if page.get("text")
    )
    lines = "\n".join(interesting_lines(all_text, limit=220))
    compact = f"PHẦN ĐẦU TÀI LIỆU:\n{head}\n\nDÒNG QUAN TRỌNG TRÍCH TỪ TOÀN BỘ TÀI LIỆU:\n{lines}\n\nPHẦN CUỐI TÀI LIỆU:\n{tail}"
    return compact[:max_chars]


def guess_phase(path_parts: list[str]) -> str | None:
    joined = " / ".join(path_parts).casefold()
    if "vận dụng cao" in joined:
        return "Vận dụng cao"
    if "vận dụng" in joined:
        return "Vận dụng"
    if "nền tảng" in joined or "cơ bản" in joined:
        return "Nền tảng"
    if "btvn" in joined or "bài tập" in joined:
        return "Bài tập"
    return None


def fallback_analysis(item: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    stem = item["stem"]
    folder = item.get("folder_path") or ""
    text = "\n".join(page.get("text", "") for page in extracted.get("pages", []))
    lines = interesting_lines(text, limit=60)
    question_lines = [
        line
        for line in lines
        if re.search(r"^(câu|bài|ví dụ)\s*\d+|trắc nghiệm|tự luận|đúng sai", line.casefold())
    ][:12]
    method_lines = [
        line
        for line in lines
        if re.search(r"phương pháp|cách giải|nhận xét|chú ý|định nghĩa|tính chất|định lí", line.casefold())
    ][:12]
    return {
        "lesson_title": stem,
        "program_area": folder.split("/")[0] if folder else None,
        "chapter": folder,
        "lesson_type": "unknown",
        "phase": guess_phase(item.get("path_parts", [])),
        "objectives": [],
        "concepts": [],
        "prerequisites": [],
        "teaching_methods": method_lines,
        "example_types": [],
        "application_questions": question_lines,
        "difficulty_progression": [],
        "source_strengths": [],
        "gaps_or_warnings": ["AI analysis unavailable; this is a local text/folder fallback."],
        "confidence": "low",
    }


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


def subject_scan_prompt(subject: str) -> tuple[str, str, str]:
    if subject == "english":
        return (
            "Bạn là chuyên gia thiết kế lộ trình học Tiếng Anh cho học sinh Việt Nam. "
            "Hãy đọc tài liệu bài học và trích xuất cấu trúc dạy học theo kỹ năng/chủ điểm tiếng Anh. "
            "Tập trung vào Grammar, Vocabulary, Reading, Listening, Speaking, Writing, Pronunciation, Test Practice. "
            "Chỉ trả về JSON object hợp lệ, không markdown.",
            "- Rút ra bài này dạy kỹ năng/chủ điểm gì, dạy bằng phương pháp nào, có dạng bài tập áp dụng nào.\n"
            "- Program area nên là Grammar, Vocabulary, Reading, Writing, Listening, Speaking, Pronunciation, Test Practice hoặc Mixed Skills.\n"
            "- Nếu tài liệu có tiếng Việt giải thích tiếng Anh, vẫn phân loại theo kỹ năng/chủ điểm tiếng Anh.\n"
            "- Nếu thiếu audio, thiếu đáp án, OCR kém hoặc tài liệu chỉ là bài tập, ghi rõ trong gaps_or_warnings.",
            '"Foundation|Practice|Application|Test Prep|Mixed|null"',
        )
    return (
        "Bạn là chuyên gia thiết kế lộ trình học Toán THPT Việt Nam. "
        "Hãy đọc tài liệu bài học và trích xuất cấu trúc dạy học. "
        "Chỉ trả về JSON object hợp lệ, không markdown.",
        "- Rút ra bài này dạy gì, dạy bằng phương pháp nào, có dạng câu áp dụng nào.",
        '"Nền tảng|Vận dụng|Vận dụng cao|Bài tập|null"',
    )


def analyze_with_ai(client: anthropic.Anthropic, item: dict[str, Any], extracted: dict[str, Any], max_chars: int, subject: str) -> dict[str, Any]:
    compact_text = compact_document_text(extracted, max_chars=max_chars)
    system, subject_instruction, phase_schema = subject_scan_prompt(subject)
    user = f"""
Nguồn: {item['source']}
Thứ tự trong nguồn: {item['order']}
Folder: {item.get('folder_path') or ''}
Tên file: {item['file_name']}
Số trang: {extracted.get('page_count')}

Yêu cầu:
- Folder/thứ tự file là source of truth; không tự đảo thứ tự.
{subject_instruction}
- Nếu tài liệu là BTVN/bài tập/bonus, hãy ghi lesson_type tương ứng.
- Không bịa nội dung nếu text không đủ.

Schema JSON:
{{
  "lesson_title": "string",
  "program_area": "string|null",
  "chapter": "string|null",
  "lesson_type": "lesson|homework|practice|bonus|test|unknown",
  "phase": {phase_schema},
  "objectives": ["string"],
  "concepts": ["string"],
  "prerequisites": ["string"],
  "teaching_methods": ["string"],
  "example_types": ["string"],
  "application_questions": ["string"],
  "difficulty_progression": ["string"],
  "source_strengths": ["string"],
  "gaps_or_warnings": ["string"],
  "confidence": "high|medium|low"
}}

Nội dung tài liệu:
{compact_text}
"""
    response = client.messages.create(
        model=MODEL,
        max_tokens=2200,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    return extract_json_object(text)


def normalize_analysis(value: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    result = dict(fallback)
    for key, current in value.items():
        if key not in result:
            continue
        if isinstance(result[key], list):
            result[key] = current if isinstance(current, list) else []
        elif current is not None:
            result[key] = current
    if result.get("lesson_type") not in {"lesson", "homework", "practice", "bonus", "test", "unknown"}:
        result["lesson_type"] = "unknown"
    if result.get("confidence") not in {"high", "medium", "low"}:
        result["confidence"] = "low"
    return result


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(
        """
        create table if not exists curriculum_lessons (
            id text primary key,
            source text not null,
            source_order integer not null,
            relative_path text not null,
            folder_path text,
            file_name text not null,
            sha1 text not null,
            page_count integer not null,
            char_count integer not null,
            lesson_title text,
            program_area text,
            chapter text,
            lesson_type text,
            phase text,
            confidence text,
            analysis_json text not null,
            scanned_at text not null
        )
        """
    )
    db.execute("create index if not exists idx_curriculum_lessons_source_order on curriculum_lessons(source, source_order)")
    db.execute("create index if not exists idx_curriculum_lessons_chapter on curriculum_lessons(chapter)")
    return db


def save_lesson(db: sqlite3.Connection, item: dict[str, Any], extracted: dict[str, Any], analysis: dict[str, Any]) -> None:
    lesson_id = hashlib.sha1(f"{item['source']}|{item['relative_path']}".encode("utf-8")).hexdigest()
    db.execute(
        """
        insert or replace into curriculum_lessons (
            id, source, source_order, relative_path, folder_path, file_name, sha1,
            page_count, char_count, lesson_title, program_area, chapter,
            lesson_type, phase, confidence, analysis_json, scanned_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lesson_id,
            item["source"],
            item["order"],
            item["relative_path"],
            item.get("folder_path"),
            item["file_name"],
            extracted["sha1"],
            extracted["page_count"],
            extracted["char_count"],
            analysis.get("lesson_title"),
            analysis.get("program_area"),
            analysis.get("chapter"),
            analysis.get("lesson_type"),
            analysis.get("phase"),
            analysis.get("confidence"),
            json.dumps(analysis, ensure_ascii=False),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    db.commit()


def render_preview(scan: dict[str, Any]) -> str:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in scan["lessons"]:
        groups.setdefault(item["source"], []).append(item)

    sections = []
    for source, rows in groups.items():
        body = []
        for row in rows:
            analysis = row["analysis"]
            concepts = ", ".join(analysis.get("concepts", [])[:6])
            methods = "; ".join(analysis.get("teaching_methods", [])[:3])
            warnings = "; ".join(analysis.get("gaps_or_warnings", [])[:2])
            body.append(
                "<tr>"
                f"<td>{row['order']}</td>"
                f"<td>{html.escape(row.get('folder_path') or '')}</td>"
                f"<td>{html.escape(analysis.get('lesson_title') or row['file_name'])}</td>"
                f"<td>{html.escape(analysis.get('lesson_type') or '')}</td>"
                f"<td>{html.escape(analysis.get('phase') or '')}</td>"
                f"<td>{html.escape(concepts)}</td>"
                f"<td>{html.escape(methods)}</td>"
                f"<td>{html.escape(warnings)}</td>"
                f"<td>{html.escape(row.get('status', ''))}</td>"
                "</tr>"
            )
        sections.append(
            f"""
            <section>
              <h2>{html.escape(source)} <span>{len(rows)} files</span></h2>
              <table>
                <thead>
                  <tr>
                    <th>Order</th><th>Folder</th><th>Lesson</th><th>Type</th><th>Phase</th>
                    <th>Concepts</th><th>Teaching methods</th><th>Warnings</th><th>Status</th>
                  </tr>
                </thead>
                <tbody>{''.join(body)}</tbody>
              </table>
            </section>
            """
        )

    return f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Curriculum Scan</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f6f7f8; color: #202124; }}
    header {{ padding: 24px 32px; background: #fff; border-bottom: 1px solid #d8dee4; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    main {{ padding: 24px 32px 40px; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }}
    .metric {{ background: #fff; border: 1px solid #d8dee4; border-radius: 6px; padding: 16px; }}
    .metric strong {{ display: block; font-size: 24px; }}
    section {{ margin-bottom: 28px; }}
    h2 {{ font-size: 20px; margin: 0 0 12px; }}
    h2 span {{ font-size: 14px; font-weight: normal; color: #59636e; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dee4; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid #eaeef2; vertical-align: top; text-align: left; font-size: 13px; }}
    th {{ background: #eef2f5; position: sticky; top: 0; z-index: 1; }}
    tr:hover td {{ background: #fafbfc; }}
  </style>
</head>
<body>
  <header>
    <h1>Curriculum Scan</h1>
    <div>Generated at {html.escape(scan["generated_at"])} | Model: {html.escape(scan.get("model") or "fallback")}</div>
  </header>
  <main>
    <section class="summary">
      <div class="metric"><strong>{scan["lesson_count"]}</strong>Lessons scanned</div>
      <div class="metric"><strong>{scan["ai_success_count"]}</strong>AI analyses</div>
      <div class="metric"><strong>{scan["fallback_count"]}</strong>Fallback analyses</div>
      <div class="metric"><strong>{scan["error_count"]}</strong>Errors</div>
    </section>
    {''.join(sections)}
  </main>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan local curriculum PDFs into local JSON and SQLite.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--source", action="append", help="Only scan selected source; can be repeated.")
    parser.add_argument("--max-ai-chars", type=int, default=30000)
    parser.add_argument("--subject", choices=["math", "english"], default="math")
    parser.add_argument("--no-ai", action="store_true")
    return parser.parse_args()


def main() -> None:
    configure_stdio()
    args = parse_args()
    root = Path(args.root)
    manifest_path = root / "output_json" / "curriculum_manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Missing manifest: {manifest_path}. Run curriculum_manifest.py first.")

    manifest = read_json(manifest_path)
    input_root = Path(manifest["input_root"])
    cache_dir = root / "cache" / "pdf_text"
    db = open_db(root / "output_sqlite" / "curriculum.sqlite")
    client = None if args.no_ai else anthropic.Anthropic(api_key=API_KEY, base_url=normalize_base_url(BASE_URL))

    files = manifest["files"]
    if args.source:
        wanted = set(args.source)
        files = [item for item in files if item["source"] in wanted]
    if args.limit > 0:
        files = files[: args.limit]

    lessons: list[dict[str, Any]] = []
    ai_success = 0
    fallback_count = 0
    error_count = 0

    for index, item in enumerate(files, start=1):
        pdf_path = input_root / item["source"] / item["relative_path"]
        print(f"[{index}/{len(files)}] {item['source']} #{item['order']}: {item['relative_path']}", flush=True)
        status = "ok"
        errors: list[str] = []
        try:
            extracted = extract_pdf_text(pdf_path, cache_dir)
            fallback = fallback_analysis(item, extracted)
            if client is None:
                analysis = fallback
                status = "fallback_no_ai"
                fallback_count += 1
            else:
                try:
                    ai_value = analyze_with_ai(client, item, extracted, max_chars=args.max_ai_chars, subject=args.subject)
                    analysis = normalize_analysis(ai_value, fallback)
                    ai_success += 1
                except Exception as exc:
                    analysis = fallback
                    status = "fallback_ai_error"
                    errors.append(str(exc))
                    fallback_count += 1
            save_lesson(db, item, extracted, analysis)
        except Exception as exc:
            extracted = {"sha1": "", "page_count": 0, "char_count": 0}
            analysis = fallback_analysis(item, {"pages": []})
            status = "error"
            errors.append(str(exc))
            error_count += 1

        lessons.append(
            {
                "source": item["source"],
                "order": item["order"],
                "relative_path": item["relative_path"],
                "folder_path": item.get("folder_path"),
                "file_name": item["file_name"],
                "sha1": extracted.get("sha1"),
                "page_count": extracted.get("page_count", 0),
                "char_count": extracted.get("char_count", 0),
                "status": status,
                "errors": errors,
                "analysis": analysis,
            }
        )

    scan = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(root.resolve()),
        "input_root": str(input_root.resolve()),
        "model": None if args.no_ai else MODEL,
        "subject": args.subject,
        "lesson_count": len(lessons),
        "ai_success_count": ai_success,
        "fallback_count": fallback_count,
        "error_count": error_count,
        "lessons": lessons,
    }
    write_json(root / "output_json" / "curriculum_scan.json", scan)
    (root / "previews").mkdir(parents=True, exist_ok=True)
    (root / "previews" / "curriculum_scan.html").write_text(render_preview(scan), encoding="utf-8")
    db.close()

    print(f"Wrote {(root / 'output_json' / 'curriculum_scan.json').resolve()}")
    print(f"Wrote {(root / 'output_sqlite' / 'curriculum.sqlite').resolve()}")
    print(f"Wrote {(root / 'previews' / 'curriculum_scan.html').resolve()}")


if __name__ == "__main__":
    main()
