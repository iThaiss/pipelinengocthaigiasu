"""Create a section-aware QA report for VIP90 bundle practice extraction."""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import fitz


DEFAULT_ROOT = Path("local_curriculum_english")
HEADING_RE = re.compile(r"(?im)^\s*(\[V90[^\n]{0,120}\]|(?:THI ONLINE|TÀI LIỆU LIVESTREAM|ĐỀ VẬN DỤNG|ĐỀ NÂNG CAO)[^\n]{0,140})")


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def classify_section(title: str) -> str:
    up = title.upper()
    if "THI ONLINE" in up or "ĐỀ VẬN DỤNG" in up or "ĐỀ NÂNG CAO" in up:
        return "online_practice"
    if "BÀI THI" in up and "TRƯỚC BUỔI" in up:
        return "pre_class_practice"
    if "ĐỌC HIỂU C1" in up or "BỘ ĐỀ" in up:
        return "practice_pack"
    if "TÀI LIỆU LIVESTREAM" in up or re.search(r"\[V90\.\d+\.\d+\]", up):
        return "lesson_theory"
    return "unknown"


def bundle_sections(path: Path) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    with fitz.open(path) as doc:
        last: dict[str, Any] | None = None
        for page_index, page in enumerate(doc, start=1):
            text = page.get_text("text", sort=True)
            headings = [m.group(1).strip() for m in HEADING_RE.finditer(text)]
            title = headings[-1] if headings else ""
            if title:
                if last:
                    last["page_end"] = page_index - 1
                    sections.append(last)
                last = {"title": title, "kind": classify_section(title), "page_start": page_index, "page_end": page_index, "sample": text[:1200]}
            elif last:
                last["page_end"] = page_index
        if last:
            sections.append(last)
    return sections


def find_section(sections: list[dict[str, Any]], page: int | None) -> dict[str, Any] | None:
    if page is None:
        return None
    for section in sections:
        if section["page_start"] <= page <= section["page_end"]:
            return section
    return None


def infer_week(relative_path: str) -> str:
    match = re.search(r"TUẦN\s+(\d+)", relative_path, flags=re.I)
    return match.group(1) if match else "?"


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    root = args.root
    manifest = json.loads((root / "output_json" / "file_manifest.json").read_text(encoding="utf-8"))
    practice = json.loads((root / "output_json" / "practice_questions.json").read_text(encoding="utf-8"))
    bundles = [item for item in manifest.get("files", []) if item.get("file_type") == "vip90_bundle"]
    questions_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for q in practice.get("questions", []):
        rel = q.get("relative_path") or ""
        if "/VIP90/" in rel and "Tài liệu đầy đủ Tuần" in rel:
            questions_by_path[rel].append(q)

    sections_by_path: dict[str, list[dict[str, Any]]] = {}
    rows = []
    for bundle in bundles:
        rel = bundle.get("relative_path", "")
        path = root / "input_sources" / rel
        if not path.exists():
            rows.append({"rel": rel, "week": infer_week(rel), "status": "missing", "sections": [], "questions": questions_by_path.get(rel, [])})
            continue
        sections = bundle_sections(path)
        sections_by_path[rel] = sections
        rows.append({"rel": rel, "week": infer_week(rel), "status": "ok", "sections": sections, "questions": questions_by_path.get(rel, [])})

    html_rows = []
    for row in rows:
        section_counts = Counter(section.get("kind") for section in row["sections"])
        q_counts = Counter(q.get("knowledge_subtopic_code_v2") for q in row["questions"])
        section_html = ""
        for section in row["sections"]:
            linked = []
            for q in row["questions"]:
                sec = find_section(row["sections"], q.get("page_start"))
                if sec is section:
                    linked.append(q)
            section_html += (
                f"<details><summary><b>{esc(section['kind'])}</b> p.{section['page_start']}-{section['page_end']} "
                f"{esc(section['title'])} | linked_questions={len(linked)}</summary>"
                f"<pre>{esc(section.get('sample', '')[:1400])}</pre>"
                f"</details>"
            )
        html_rows.append(
            "<tr>"
            f"<td>{esc(row['week'])}</td><td>{esc(row['status'])}</td><td>{esc(row['rel'])}</td>"
            f"<td>{len(row['sections'])}<br>{esc(', '.join(f'{k}:{v}' for k, v in section_counts.items()))}</td>"
            f"<td>{len(row['questions'])}<br>{esc(', '.join(f'{k}:{v}' for k, v in q_counts.most_common(8)))}</td>"
            f"<td>{section_html}</td></tr>"
        )
    out = root / "previews" / "practice_vip90_bundle_section_report.html"
    out.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>VIP90 Bundle Section Report</title>"
        "<style>body{font-family:Arial,sans-serif;padding:20px}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #ccc;padding:6px;vertical-align:top}th{background:#222;color:white;position:sticky;top:0}"
        "pre{white-space:pre-wrap;background:#f7f7f7;padding:8px;max-height:280px;overflow:auto}</style></head><body>"
        f"<h1>VIP90 Bundle Section Report</h1><p>Bundles: {len(rows)}. Questions currently linked to VIP90 bundles: {sum(len(row['questions']) for row in rows)}.</p>"
        f"<table><tr><th>Week</th><th>Status</th><th>Bundle</th><th>Sections</th><th>Questions</th><th>Section Preview</th></tr>{''.join(html_rows)}</table>"
        "</body></html>",
        encoding="utf-8",
    )
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
