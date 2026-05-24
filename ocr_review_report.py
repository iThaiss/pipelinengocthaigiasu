"""Build a review HTML for OCR-rescanned practice PDFs."""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("local_curriculum_english")


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def render_question(q: dict[str, Any]) -> str:
    opts = " | ".join(f"{k}. {v}" for k, v in (q.get("options") or {}).items())
    return (
        "<div class='q'>"
        f"<div><b>Q{esc(q.get('question_number'))}</b> {esc(q.get('knowledge_subtopic_code_v2'))} / {esc(q.get('question_format'))}</div>"
        f"<div class='text'>{esc((q.get('question_text') or '')[:700])}</div>"
        f"<div class='opts'>{esc(opts[:700])}</div>"
        f"<div class='muted'>page={esc(q.get('page_start'))}-{esc(q.get('page_end'))} reason={esc(q.get('review_reason'))}</div>"
        "</div>"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--text-chars", type=int, default=3500)
    args = parser.parse_args()
    root = args.root
    data = json.loads((root / "output_json" / "practice_questions_ocr_rescan.json").read_text(encoding="utf-8"))
    rows = []
    for result in data.get("files", []):
        info = result.get("file", {})
        rel = info.get("relative_path") or ""
        cache_path = result.get("ocr_cache")
        ocr_text = ""
        if cache_path and Path(cache_path).exists():
            try:
                ocr_text = json.loads(Path(cache_path).read_text(encoding="utf-8")).get("text", "")
            except Exception as exc:
                ocr_text = f"[cache read error: {exc}]"
        questions = result.get("questions", [])
        accepted = [q for q in data.get("questions", []) if q.get("relative_path") == rel]
        rejected = [q for q in data.get("rejected_questions", []) if q.get("relative_path") == rel]
        q_html = "".join(render_question(q) for q in accepted[:8]) or "<div class='empty'>No accepted questions</div>"
        rej_html = "".join(render_question(q) for q in rejected[:5]) or "<div class='empty'>No rejected samples</div>"
        rows.append(
            "<section>"
            f"<h2>{esc(Path(rel).name)}</h2>"
            f"<div class='path'>{esc(rel)}</div>"
            f"<div class='stats'>status={esc(result.get('status'))} | error={esc(result.get('error'))} | "
            f"raw={len(questions)} | accepted={len(accepted)} | rejected={len(rejected)} | "
            f"hints={esc(result.get('hints'))}</div>"
            f"<details open><summary>OCR text sample</summary><pre>{esc(ocr_text[:args.text_chars])}</pre></details>"
            f"<h3>Accepted samples</h3>{q_html}"
            f"<h3>Rejected samples</h3>{rej_html}"
            "</section>"
        )
    summary = (
        f"Files: <b>{len(data.get('files', []))}</b> | Accepted questions: <b>{len(data.get('questions', []))}</b> | "
        f"Rejected questions: <b>{len(data.get('rejected_questions', []))}</b> | "
        f"Status: <b>{esc(dict(Counter(r.get('status') for r in data.get('files', []))))}</b>"
    )
    out = root / "previews" / "practice_ocr_review.html"
    out.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>Practice OCR Review</title>"
        "<style>body{font-family:Arial,sans-serif;padding:20px;line-height:1.45}section{border-top:3px solid #222;margin-top:24px;padding-top:12px}"
        ".path,.muted{font-size:12px;color:#666;word-break:break-all}.stats{background:#f2f2f2;border:1px solid #ddd;padding:8px;margin:8px 0}"
        "pre{white-space:pre-wrap;background:#f8f8f8;border:1px solid #ddd;padding:10px;max-height:420px;overflow:auto}.q{border:1px solid #ccc;padding:8px;margin:8px 0}.text{font-weight:500;margin:6px 0}.opts{font-size:12px}.empty{color:#777;font-style:italic}</style>"
        f"</head><body><h1>Practice OCR Review</h1><div class='stats'>{summary}</div>{''.join(rows)}</body></html>",
        encoding="utf-8",
    )
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
