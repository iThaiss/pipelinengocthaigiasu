"""Merge scan-only practice data with improved local regex rescan output.

The regex rescan is better at layout recovery and taxonomy remapping. The older
scan-only file may contain better answers/explanations from AI or PDF keys. This
merge keeps regex-rescan as the structural source of truth, then overlays answer
metadata from matching scan-only questions.
"""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("local_curriculum_english")


def norm_text(value: str) -> str:
    return " ".join((value or "").split())


def option_signature(q: dict[str, Any]) -> str:
    opts = q.get("options") or {}
    return "|".join(f"{k}:{norm_text(str(opts[k]))}" for k in sorted(opts))[:600]


def question_key(q: dict[str, Any]) -> tuple[Any, ...]:
    opt_sig = option_signature(q)
    if opt_sig:
        return (q.get("file_sha1"), q.get("question_number"), opt_sig)
    return (q.get("file_sha1"), q.get("question_number"), norm_text(q.get("question_text") or "")[:240])


def overlay_answer_metadata(base: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    item = dict(base)
    if old.get("correct_answer") and not item.get("correct_answer"):
        item["correct_answer"] = old.get("correct_answer")
    if old.get("answer_source") and (not item.get("answer_source") or item.get("answer_source") == "missing"):
        item["answer_source"] = old.get("answer_source")
    if old.get("explanation") and not item.get("explanation"):
        item["explanation"] = old.get("explanation")
    old_model = old.get("ai_model")
    if old_model and old_model != "regex_fallback":
        item["answer_ai_model"] = old_model
    reasons: list[str] = []
    for value in (base.get("review_reason"), old.get("review_reason"), "merged_rescan_preserved_answer_metadata"):
        if value:
            reasons.extend(part for part in str(value).split("; ") if part)
    item["review_reason"] = "; ".join(sorted(set(reasons)))
    item["needs_review"] = bool(item.get("needs_review")) or bool(old.get("needs_review"))
    return item


def write_coverage_report(root: Path, payload: dict[str, Any], old: dict[str, Any], regex: dict[str, Any]) -> Path:
    taxonomy = json.loads((root / "output_json" / "english_taxonomy_v2.json").read_text(encoding="utf-8"))
    old_counts = Counter(q.get("knowledge_subtopic_code_v2") for q in old.get("questions", []))
    regex_counts = Counter(q.get("knowledge_subtopic_code_v2") for q in regex.get("questions", []))
    merged_counts = Counter(q.get("knowledge_subtopic_code_v2") for q in payload.get("questions", []))
    rows = []
    for subtopic in taxonomy.get("knowledge_subtopics", []):
        code = subtopic["subtopic_code"]
        current = merged_counts[code]
        need = max(0, 60 - current)
        rows.append((need, code, subtopic, old_counts[code], regex_counts[code], current))
    trs = []
    for need, code, subtopic, old_count, regex_count, current in sorted(rows, key=lambda row: (row[0] > 0, row[1])):
        cls = "ok" if need == 0 else ("zero" if current == 0 else "low")
        trs.append(
            f"<tr class='{cls}'><td>{html.escape(code)}</td><td>{old_count}</td><td>{regex_count}</td>"
            f"<td>{current}</td><td>{need}</td><td>{html.escape(subtopic.get('topic_title', ''))}</td>"
            f"<td>{html.escape(subtopic.get('subtopic_title', ''))}</td></tr>"
        )
    deficient = sum(1 for need, *_ in rows if need)
    zero = sum(1 for _, _, _, _, _, current in rows if current == 0)
    out = root / "previews" / "practice_source_improved_coverage.html"
    out.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>Practice Source Improved Coverage</title>"
        "<style>body{font-family:Arial,sans-serif;padding:20px}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #ccc;padding:6px}th{background:#222;color:white;position:sticky;top:0}"
        ".ok{background:#edf9ed}.low{background:#fff8d8}.zero{background:#ffe6e6}</style></head><body>"
        f"<h1>Practice Source Improved Coverage</h1><p>Scan-only: {len(old.get('questions', []))}. "
        f"Regex rescan: {len(regex.get('questions', []))}. Merged source-improved: {len(payload.get('questions', []))}. "
        f"Deficient: {deficient}. Zero: {zero}.</p>"
        f"<table><tr><th>Code</th><th>Old</th><th>Regex</th><th>Merged</th><th>Need</th><th>Topic</th><th>Subtopic</th></tr>{''.join(trs)}</table>"
        "</body></html>",
        encoding="utf-8",
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    root = args.root
    old_path = root / "output_json" / "practice_questions_scan_only.json"
    regex_path = root / "output_json" / "practice_questions_regex_rescan.json"
    old = json.loads(old_path.read_text(encoding="utf-8"))
    regex = json.loads(regex_path.read_text(encoding="utf-8"))

    old_by_key = {question_key(q): q for q in old.get("questions", [])}
    merged = []
    matched_old: set[tuple[Any, ...]] = set()
    for q in regex.get("questions", []):
        key = question_key(q)
        old_q = old_by_key.get(key)
        if old_q:
            matched_old.add(key)
            merged.append(overlay_answer_metadata(q, old_q))
        else:
            merged.append(q)
    for key, old_q in old_by_key.items():
        if key not in matched_old:
            merged.append(old_q)

    payload = dict(regex)
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    payload["merge_source"] = "regex_rescan_structure_plus_scan_only_answers"
    payload["scan_only_questions"] = len(old.get("questions", []))
    payload["regex_rescan_questions"] = len(regex.get("questions", []))
    payload["answer_metadata_matches"] = len(matched_old)
    payload["questions"] = merged
    payload["total_questions"] = len(merged)
    out = root / "output_json" / "practice_questions_source_improved.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report = write_coverage_report(root, payload, old, regex)
    print(f"Saved: {out}")
    print(f"Saved: {report}")
    print(f"Merged questions: {len(merged)}")
    print(f"Answer metadata matches: {len(matched_old)}")


if __name__ == "__main__":
    main()
