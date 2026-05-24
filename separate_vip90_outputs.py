"""Separate VIP90 bundle content from the main English practice dataset.

Outputs:
- output_json/vip90_practice_questions.json
- output_json/vip90_theory_sections.json
- output_json/practice_questions_no_vip90_source.json
- output_json/practice_questions_no_vip90_balanced.json

The main practice file can then use the no-VIP90 balanced output, while VIP90 is
kept as a separate source for later review/theory material.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz

import practice_coverage_backfill as backfill


DEFAULT_ROOT = Path("local_curriculum_english")
VIP90_SECTION_RE = re.compile(r"(?im)^\s*(\[V90[^\n]{0,160}\]|(?:TÀI LIỆU LIVESTREAM|THI ONLINE|ĐỀ VẬN DỤNG|ĐỀ NÂNG CAO)[^\n]{0,160})")


def is_vip90_bundle_path(path: str | None) -> bool:
    value = path or ""
    return "/VIP90/" in value and "Tài liệu đầy đủ Tuần" in value


def is_any_vip90_path(path: str | None) -> bool:
    return "/VIP90/" in (path or "")


def infer_week(relative_path: str) -> int | None:
    match = re.search(r"TUẦN\s+(\d+)", relative_path, flags=re.I)
    return int(match.group(1)) if match else None


def classify_section(title: str) -> str:
    up = title.upper()
    if "THI ONLINE" in up or "ĐỀ VẬN DỤNG" in up or "ĐỀ NÂNG CAO" in up:
        return "online_practice"
    if "ĐỌC HIỂU C1" in up or "BỘ ĐỀ" in up:
        return "practice_pack"
    if "TÀI LIỆU LIVESTREAM" in up or re.search(r"\[V90\.\d+\.\d+\]", up):
        return "lesson_theory"
    return "unknown"


def extract_vip90_sections(root: Path, bundle_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for item in bundle_items:
        rel = item.get("relative_path") or ""
        path = root / "input_sources" / rel
        if not path.exists():
            sections.append({
                "source_type": "vip90_bundle_missing",
                "relative_path": rel,
                "vip90_week": infer_week(rel),
                "status": "missing_pdf",
                "section_title": "",
                "section_kind": "missing",
                "page_start": None,
                "page_end": None,
                "raw_text": "",
            })
            continue
        with fitz.open(path) as doc:
            current: dict[str, Any] | None = None
            current_text: list[str] = []
            for page_index, page in enumerate(doc, start=1):
                text = page.get_text("text", sort=True)
                headings = [m.group(1).strip() for m in VIP90_SECTION_RE.finditer(text)]
                title = headings[-1] if headings else ""
                if title:
                    if current is not None:
                        current["page_end"] = page_index - 1
                        current["raw_text"] = "\n\n".join(current_text).strip()
                        sections.append(current)
                    current = {
                        "source_type": "vip90_bundle_section",
                        "relative_path": rel,
                        "vip90_week": infer_week(rel),
                        "status": "ok",
                        "section_title": title,
                        "section_kind": classify_section(title),
                        "page_start": page_index,
                        "page_end": page_index,
                        "raw_text": "",
                    }
                    current_text = [text]
                elif current is not None:
                    current_text.append(text)
            if current is not None:
                current["page_end"] = doc.page_count
                current["raw_text"] = "\n\n".join(current_text).strip()
                sections.append(current)
    return sections


def build_passages(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for q in questions:
        pid = q.get("passage_id")
        text = q.get("passage_text")
        if not pid or not text:
            continue
        if pid not in grouped:
            grouped[pid] = {
                "passage_id": pid,
                "relative_path": q.get("relative_path"),
                "source_file": q.get("source_file"),
                "file_sha1": q.get("file_sha1"),
                "passage_text": text,
                "question_ids": [],
                "question_numbers": [],
                "question_count": 0,
            }
        grouped[pid]["question_ids"].append(q.get("question_id"))
        grouped[pid]["question_numbers"].append(q.get("question_number"))
    for passage in grouped.values():
        passage["question_count"] = len(passage["question_ids"])
    return sorted(grouped.values(), key=lambda item: (item.get("relative_path") or "", item.get("passage_id") or ""))


def make_balanced(root: Path, source_payload: dict[str, Any], target: int) -> dict[str, Any]:
    taxonomy = json.loads((root / "output_json" / "english_taxonomy_v2.json").read_text(encoding="utf-8"))
    source_questions = [q for q in source_payload.get("questions", []) if q.get("source_type") != "generated_backfill"]
    counts = Counter(q.get("knowledge_subtopic_code_v2") for q in source_questions)
    generated = []
    for subtopic in taxonomy.get("knowledge_subtopics", []):
        before = counts[subtopic["subtopic_code"]]
        need = max(0, target - before)
        for index in range(1, need + 1):
            generated.append(backfill.generated_question(subtopic, index, before))
    balanced = dict(source_payload)
    balanced["generated_at"] = datetime.now().isoformat(timespec="seconds")
    balanced["coverage_target_per_subtopic"] = target
    balanced["total_generated_backfill_questions"] = len(generated)
    balanced["generated_backfill_questions"] = generated
    balanced["questions"] = source_questions + generated
    balanced["total_questions"] = len(balanced["questions"])
    balanced["passages"] = build_passages(source_questions)
    balanced["total_passages"] = len(balanced["passages"])
    return balanced


def write_vip90_preview(root: Path, vip_questions: list[dict[str, Any]], sections: list[dict[str, Any]]) -> Path:
    by_week = Counter(q.get("vip90_week") or infer_week(q.get("relative_path") or "") for q in vip_questions)
    section_counts = Counter(s.get("section_kind") for s in sections)
    trs = []
    for week in sorted(set(by_week) | {s.get("vip90_week") for s in sections if s.get("vip90_week") is not None}):
        q_count = by_week.get(week, 0)
        week_sections = [s for s in sections if s.get("vip90_week") == week]
        trs.append(
            f"<tr><td>{week}</td><td>{q_count}</td><td>{len(week_sections)}</td>"
            f"<td>{html.escape(', '.join(f'{k}:{v}' for k, v in Counter(s.get('section_kind') for s in week_sections).items()))}</td></tr>"
        )
    out = root / "previews" / "vip90_separate_summary.html"
    out.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>VIP90 Separate Summary</title>"
        "<style>body{font-family:Arial,sans-serif;padding:20px}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #ccc;padding:6px}th{background:#222;color:white}</style></head><body>"
        f"<h1>VIP90 Separate Summary</h1><p>VIP90 practice questions: {len(vip_questions)}. "
        f"VIP90 sections: {len(sections)}. Section kinds: {html.escape(str(dict(section_counts)))}</p>"
        f"<table><tr><th>Week</th><th>Practice Questions</th><th>Sections</th><th>Section Kinds</th></tr>{''.join(trs)}</table>"
        "</body></html>",
        encoding="utf-8",
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--target", type=int, default=60)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root
    output_dir = root / "output_json"

    current = json.loads((output_dir / "practice_questions.json").read_text(encoding="utf-8"))
    manifest = json.loads((output_dir / "file_manifest.json").read_text(encoding="utf-8"))
    source_improved_path = output_dir / "practice_questions_source_improved.json"
    source_improved = json.loads(source_improved_path.read_text(encoding="utf-8")) if source_improved_path.exists() else current

    source_questions = [q for q in source_improved.get("questions", []) if q.get("source_type") != "generated_backfill"]
    vip_questions = []
    main_questions = []
    for q in source_questions:
        if is_vip90_bundle_path(q.get("relative_path")):
            item = dict(q)
            item["source_type"] = "vip90_bundle_practice"
            item["vip90_week"] = infer_week(item.get("relative_path") or "")
            vip_questions.append(item)
        else:
            main_questions.append(q)

    bundle_items = [item for item in manifest.get("files", []) if item.get("file_type") == "vip90_bundle"]
    vip_sections = extract_vip90_sections(root, bundle_items)

    vip_payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_type": "vip90_separate_practice",
        "total_questions": len(vip_questions),
        "questions": vip_questions,
        "by_week": Counter(q.get("vip90_week") for q in vip_questions),
        "by_subtopic": Counter(q.get("knowledge_subtopic_code_v2") for q in vip_questions),
    }
    vip_payload = json.loads(json.dumps(vip_payload, ensure_ascii=False, default=dict))
    vip_path = output_dir / "vip90_practice_questions.json"
    vip_path.write_text(json.dumps(vip_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    theory_payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_type": "vip90_theory_sections",
        "total_sections": len(vip_sections),
        "sections": vip_sections,
        "by_week": Counter(s.get("vip90_week") for s in vip_sections),
        "by_section_kind": Counter(s.get("section_kind") for s in vip_sections),
    }
    theory_payload = json.loads(json.dumps(theory_payload, ensure_ascii=False, default=dict))
    theory_path = output_dir / "vip90_theory_sections.json"
    theory_path.write_text(json.dumps(theory_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    main_files = [f for f in source_improved.get("files", []) if not is_any_vip90_path((f.get("file") or {}).get("relative_path"))]
    main_rejected = [q for q in source_improved.get("rejected_questions", []) if not is_any_vip90_path(q.get("relative_path"))]
    main_payload = dict(source_improved)
    main_payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    main_payload["source_policy"] = "vip90_bundle_removed_to_separate_outputs"
    main_payload["vip90_practice_output"] = str(vip_path.relative_to(root))
    main_payload["vip90_theory_output"] = str(theory_path.relative_to(root))
    main_payload["questions"] = main_questions
    main_payload["total_questions"] = len(main_questions)
    main_payload["files"] = main_files
    main_payload["total_files"] = len(main_files)
    main_payload["rejected_questions"] = main_rejected
    main_payload["total_rejected_questions"] = len(main_rejected)
    main_payload["passages"] = build_passages(main_questions)
    main_payload["total_passages"] = len(main_payload["passages"])
    main_source_path = output_dir / "practice_questions_no_vip90_source.json"
    main_source_path.write_text(json.dumps(main_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    balanced = make_balanced(root, main_payload, args.target)
    balanced_path = output_dir / "practice_questions_no_vip90_balanced.json"
    balanced_path.write_text(json.dumps(balanced, ensure_ascii=False, indent=2), encoding="utf-8")

    preview = write_vip90_preview(root, vip_questions, vip_sections)

    if args.apply:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        current_path = output_dir / "practice_questions.json"
        backup = output_dir / f"practice_questions_before_vip90_separate_{stamp}.json"
        shutil.copy2(current_path, backup)
        shutil.copy2(balanced_path, current_path)
        print(f"Backed up: {backup}")
        print(f"Applied: {current_path}")

    print(f"Saved: {vip_path}")
    print(f"Saved: {theory_path}")
    print(f"Saved: {main_source_path}")
    print(f"Saved: {balanced_path}")
    print(f"Saved: {preview}")
    print(f"Main source questions: {len(main_questions)}")
    print(f"VIP90 practice questions: {len(vip_questions)}")
    print(f"VIP90 sections: {len(vip_sections)}")
    print(f"Backfill needed without VIP90: {balanced.get('total_generated_backfill_questions')}")


if __name__ == "__main__":
    main()
