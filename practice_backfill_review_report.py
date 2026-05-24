"""Build a focused review report for generated English practice backfill items."""
from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("local_curriculum_english")


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def quality_flags(q: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    text = q.get("question_text") or ""
    opts = q.get("options") or {}
    fmt = q.get("question_format") or ""
    if "This question focuses on" in text:
        flags.append("generic_template")
    if fmt not in {"hsa_sentence_rewriting", "hsa_sentence_combination", "spt_paragraph_writing"} and len(opts) < 4:
        flags.append("missing_options")
    if fmt in {"hsa_sentence_rewriting", "hsa_sentence_combination", "spt_paragraph_writing"}:
        flags.append("open_response")
    elif not q.get("correct_answer"):
        flags.append("missing_answer")
    if fmt in {"thpt_reading_passage", "hsa_cloze_text", "thpt_advertisement_cloze", "thpt_press_release_cloze"} and not q.get("passage_text"):
        flags.append("missing_passage")
    if len(text) < 35:
        flags.append("short_stem")
    return flags or ["review_sample"]


def render_question(q: dict[str, Any]) -> str:
    opts = " | ".join(f"{k}. {v}" for k, v in (q.get("options") or {}).items())
    passage = q.get("passage_text") or ""
    flags = quality_flags(q)
    flag_class = " bad" if any(f in flags for f in ["generic_template", "missing_options", "missing_answer", "missing_passage"]) else ""
    passage_html = ""
    if passage:
        passage_html = f"<details><summary>Passage</summary><div class='passage'>{esc(passage[:1200])}{'...' if len(passage) > 1200 else ''}</div></details>"
    return f"""
    <div class="qcard{flag_class}">
      <div class="meta"><b>{esc(q.get('question_id'))}</b></div>
      <div class="flags">{esc(', '.join(flags))}</div>
      {passage_html}
      <div class="qtext">{esc(q.get('question_text'))}</div>
      <div class="opts">{esc(opts)}</div>
      <div class="meta">answer={esc(q.get('correct_answer'))} | answer_source={esc(q.get('answer_source'))} | type={esc(q.get('practice_item_type'))}</div>
    </div>
    """


def write_report(root: Path, sample_per_subtopic: int) -> Path:
    questions_path = root / "output_json" / "practice_questions.json"
    taxonomy_path = root / "output_json" / "english_taxonomy_v2.json"
    data = json.loads(questions_path.read_text(encoding="utf-8"))
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))

    subtopic_meta = {s["subtopic_code"]: s for s in taxonomy.get("knowledge_subtopics", [])}
    items = [q for q in data.get("questions", []) if q.get("source_type") == "generated_backfill"]
    by_subtopic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_subtopic[item.get("knowledge_subtopic_code_v2") or "unknown"].append(item)

    format_counts = Counter(q.get("question_format") for q in items)
    answer_counts = Counter(q.get("answer_source") for q in items)
    flag_counts = Counter(flag for q in items for flag in quality_flags(q))

    sections: list[str] = []
    for code in sorted(by_subtopic):
        rows = by_subtopic[code]
        meta = subtopic_meta.get(code, {})
        formats = Counter(q.get("question_format") for q in rows)
        answers = Counter(q.get("answer_source") for q in rows)
        flags = Counter(flag for q in rows for flag in quality_flags(q))
        samples = sorted(rows, key=lambda q: ("generic_template" not in quality_flags(q), q.get("question_number") or 0))[:sample_per_subtopic]
        section_class = " needs-work" if flags.get("generic_template") or flags.get("missing_answer") or flags.get("missing_options") else ""
        sections.append(
            f"""
            <section class="subtopic{section_class}">
              <h2>{esc(code)} - {esc(meta.get('subtopic_title', 'Unknown'))}</h2>
              <div class="stats">
                topic={esc(meta.get('topic_title', ''))}<br>
                generated={len(rows)} | formats={esc(', '.join(f'{k}:{v}' for k, v in formats.most_common()))} | answers={esc(', '.join(f'{k}:{v}' for k, v in answers.most_common()))}<br>
                flags={esc(', '.join(f'{k}:{v}' for k, v in flags.most_common()))}
              </div>
              <div class="grid">{''.join(render_question(q) for q in samples)}</div>
            </section>
            """
        )

    summary = (
        f"Generated backfill items: <b>{len(items)}</b> | "
        f"Subtopics with backfill: <b>{len(by_subtopic)}</b> | "
        f"Formats: <b>{esc(', '.join(f'{k}:{v}' for k, v in format_counts.most_common()))}</b><br>"
        f"Answer sources: <b>{esc(', '.join(f'{k}:{v}' for k, v in answer_counts.most_common()))}</b><br>"
        f"Quality flags: <b>{esc(', '.join(f'{k}:{v}' for k, v in flag_counts.most_common()))}</b>"
    )
    content = f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Generated Backfill Review</title>
<style>
body{{font-family:Arial,sans-serif;font-size:13px;line-height:1.45;padding:20px;color:#222}}
h1{{margin-top:0}} h2{{border-top:3px solid #222;padding-top:14px;margin-top:28px}}
.summary,.stats{{background:#f2f2f2;border:1px solid #ddd;padding:10px;margin:10px 0}}
.needs-work h2{{border-top-color:#a34700}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:10px}}
.qcard{{border:1px solid #ccc;background:#fff;padding:10px}}
.qcard.bad{{border-color:#d08000;background:#fff8ed}}
.meta{{font-size:11px;color:#666;word-break:break-all}}
.flags{{font-size:12px;color:#7a3b00;font-weight:bold;margin:4px 0}}
.qtext{{margin:8px 0;font-weight:500}}
.opts{{font-size:12px;color:#333;margin:6px 0}}
.passage{{white-space:pre-wrap;background:#f8f8f8;border-left:3px solid #777;padding:8px;margin:6px 0}}
</style></head><body><h1>Generated Backfill Review</h1><div class="summary">{summary}</div>{''.join(sections)}</body></html>"""
    out_path = root / "previews" / "practice_backfill_review.html"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--sample-per-subtopic", type=int, default=8)
    args = parser.parse_args()
    path = write_report(args.root, args.sample_per_subtopic)
    print(f"Saved: {path}")


if __name__ == "__main__":
    main()
