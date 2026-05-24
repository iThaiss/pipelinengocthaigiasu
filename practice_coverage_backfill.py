"""Create supplemental practice items so every knowledge subtopic has coverage.

This does not pretend generated items came from PDFs. It writes a separate balanced
artifact with source_type="generated_backfill" for auditability.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path("local_curriculum_english")
TARGET_PER_SUBTOPIC = 60


def stable_id(code: str, index: int, text: str) -> str:
    digest = hashlib.sha1(f"{code}:{index}:{text}".encode("utf-8")).hexdigest()[:12]
    return f"en-practice-backfill-{code.lower()}-{index:03d}-{digest}"


def format_for_code(code: str) -> str:
    if code == "E2C.03":
        return "thpt_advertisement_cloze"
    if code == "E2C.04":
        return "thpt_press_release_cloze"
    if code.startswith("E2C"):
        return "hsa_cloze_text"
    if code.startswith("E2R"):
        return "thpt_reading_passage"
    if code == "E2O.01":
        return "hsa_dialogue_arrangement"
    if code.startswith("E2O"):
        return "thpt_arrangement_text"
    if code.startswith("E2F"):
        return "hsa_dialogue_completion"
    if code == "E2W.01":
        return "hsa_sentence_rewriting"
    if code == "E2W.02":
        return "hsa_sentence_combination"
    if code.startswith("E2W"):
        return "spt_paragraph_writing"
    if code == "E2X.01":
        return "spt_word_formation"
    return "hsa_sentence_completion"


def item_type_for_format(fmt: str) -> str:
    if fmt in {"hsa_sentence_rewriting", "hsa_sentence_combination"}:
        return "transform_sentence"
    if fmt == "spt_paragraph_writing":
        return "open_response"
    if fmt in {"hsa_cloze_text", "thpt_advertisement_cloze", "thpt_press_release_cloze"}:
        return "cloze"
    if fmt == "thpt_reading_passage":
        return "reading_mcq"
    if "arrangement" in fmt:
        return "arrangement"
    return "mcq"


def options(correct: str, wrong1: str, wrong2: str, wrong3: str) -> dict[str, str]:
    return {"A": correct, "B": wrong1, "C": wrong2, "D": wrong3}


def grammar_item(code: str, title: str, n: int) -> tuple[str, dict[str, str], str]:
    lower = title.lower()
    if "present" in lower:
        return (f"Choose the best form: She usually _____ her homework before dinner. ({n})", options("does", "is doing", "did", "has done"), "A")
    if "future" in lower:
        return (f"Choose the best future form: Look at those clouds. It _____ soon. ({n})", options("is going to rain", "rains", "rained", "has rained"), "A")
    if "relative" in lower:
        return (f"Choose the best relative clause: The student _____ won the prize is in my class. ({n})", options("who", "which", "where", "when"), "A")
    if "reduced" in lower:
        return (f"Choose the best reduced clause: The man _____ near the window is my teacher. ({n})", options("sitting", "who sitting", "sat", "sits"), "A")
    if "parallel" in lower or "apposition" in lower:
        return (f"Choose the sentence with correct parallel structure: ({n})", options("She likes reading, writing, and speaking English.", "She likes reading, to write, and speaking English.", "She likes to read, writing, and speak English.", "She likes read, write, and speaking English."), "A")
    if "word order" in lower:
        return (f"Choose the sentence with correct word order: ({n})", options("She bought a beautiful new red dress.", "She bought a red new beautiful dress.", "She bought a new beautiful red dress.", "She bought a beautiful red new dress."), "A")
    if "collocation" in lower:
        return (f"Choose the correct collocation: Students should _____ attention in class. ({n})", options("pay", "make", "do", "take"), "A")
    if "fixed" in lower or "phrasal" in lower:
        return (f"Choose the best fixed expression: I look forward _____ from you soon. ({n})", options("to hearing", "to hear", "hearing", "hear"), "A")
    return (f"Choose the best answer to complete the sentence: This question focuses on {title}. ({n})", options("the correct form", "an incorrect form", "an unrelated phrase", "a wrong structure"), "A")


def reading_item(code: str, title: str, n: int) -> tuple[str, dict[str, str], str, str]:
    passage = (
        f"Passage {n}. Many students use digital tools to support their English learning. "
        "Some apps help them review vocabulary, while others provide reading practice and instant feedback. "
        "However, effective learners do not rely on technology alone. They set clear goals, choose suitable materials, "
        "and reflect on their mistakes after each practice session."
    )
    lower = title.lower()
    if "main idea" in lower or "title" in lower:
        q = "Which title best matches the passage?"
        opts = options("Using Technology Wisely in English Learning", "Why Students Should Stop Using Apps", "The History of Mobile Phones", "How to Design a Language App")
    elif "vocabulary" in lower:
        q = "The word 'effective' in the passage is closest in meaning to _____."
        opts = options("successful", "expensive", "traditional", "difficult")
    elif "tone" in lower or "purpose" in lower:
        q = "What is the author's main purpose?"
        opts = options("To explain how learners can use tools wisely", "To advertise a specific app", "To criticise all technology", "To tell a personal story")
    elif "paragraph location" in lower or "sentence insertion" in lower:
        q = "Where would the sentence 'This balance is important for long-term progress.' best fit?"
        opts = options("After the final sentence", "Before the first sentence", "Inside the phrase 'digital tools'", "After the word 'Some'")
    else:
        q = f"Which statement is best supported by the passage? ({n})"
        opts = options("Students should combine technology with clear learning habits.", "Technology alone guarantees fluency.", "Vocabulary apps are useless for learners.", "Reading practice should be avoided.")
    return q, opts, "A", passage


def cloze_item(code: str, title: str, n: int) -> tuple[str, dict[str, str], str, str]:
    passage = f"A school notice says that students should register early for the workshop because places are (1) _____. The event will help learners practise {title.lower()} in context."
    return "Choose the best option for blank (1).", options("limited", "limit", "limiting", "limitless"), "A", passage


def generated_question(subtopic: dict[str, Any], n: int, existing_count: int) -> dict[str, Any]:
    code = subtopic["subtopic_code"]
    title = subtopic["subtopic_title"]
    fmt = format_for_code(code)
    item_type = item_type_for_format(fmt)
    passage_text = None
    passage_id = None
    if fmt == "thpt_reading_passage":
        qtext, opts, answer, passage_text = reading_item(code, title, n)
        passage_id = f"generated-{code.lower()}-{n:03d}"
    elif fmt in {"hsa_cloze_text", "thpt_advertisement_cloze", "thpt_press_release_cloze"}:
        qtext, opts, answer, passage_text = cloze_item(code, title, n)
        passage_id = f"generated-{code.lower()}-{n:03d}"
    elif fmt == "hsa_dialogue_completion":
        qtext = f"Choose the best response to complete the exchange about {title.lower()}. ({n})\nA: Could you help me with this task?\nB: _____."
        opts = options("Of course. What do you need?", "I never study English.", "It was yesterday.", "No, it is a pencil.")
        answer = "A"
    elif "arrangement" in fmt:
        qtext = f"Arrange the sentences to make a coherent text about {title.lower()}. ({n})\na. Finally, review the result.\nb. First, identify the main idea.\nc. Then connect supporting details."
        opts = options("b - c - a", "a - b - c", "c - a - b", "b - a - c")
        answer = "A"
    elif fmt == "hsa_sentence_rewriting":
        qtext = f"Rewrite the sentence using the target structure for {title}: The teacher explained the rule clearly. ({n})"
        opts = {}
        answer = None
    elif fmt == "hsa_sentence_combination":
        qtext = f"Combine the sentences into one sentence about {title}: The task was difficult. The students completed it. ({n})"
        opts = {}
        answer = None
    elif fmt == "spt_paragraph_writing":
        qtext = f"Write a short paragraph giving your opinion on {title.lower()}. ({n})"
        opts = {}
        answer = None
    else:
        qtext, opts, answer = grammar_item(code, title, n)
    return {
        "question_id": stable_id(code, n, qtext),
        "source_file": "generated_backfill",
        "relative_path": f"generated_backfill/{code}.json",
        "file_sha1": "generated_backfill",
        "question_number": existing_count + n,
        "page_start": None,
        "page_end": None,
        "question_text": qtext,
        "options": opts,
        "correct_answer": answer,
        "answer_source": "ai_solved" if answer else "missing",
        "explanation": "Generated supplemental item for minimum subtopic coverage; review before high-stakes use.",
        "passage_id": passage_id,
        "passage_text": passage_text,
        "question_format": fmt,
        "knowledge_subtopic_code_v2": code,
        "exam_profiles": ["THPT_2025_CORE"],
        "linked_node_codes_v2": [],
        "difficulty": "basic",
        "confidence": "medium",
        "needs_review": True,
        "review_reason": "generated_backfill_minimum_coverage",
        "ready_for_ai_solve": bool(qtext and (opts or fmt in {"hsa_sentence_rewriting", "hsa_sentence_combination", "spt_paragraph_writing"})),
        "ai_model": "generated_backfill_v1",
        "practice_item_type": item_type,
        "source_type": "generated_backfill",
        "raw_extract": {"source_type": "generated_backfill", "target_subtopic": code},
    }


def write_preview(path: Path, rows: list[dict[str, Any]]) -> None:
    trs = []
    for row in rows:
        trs.append(
            f"<tr><td>{html.escape(row['code'])}</td><td>{row['before']}</td><td>{row['generated']}</td>"
            f"<td>{row['after']}</td><td>{html.escape(row['title'])}</td></tr>"
        )
    path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>Backfill Coverage</title>"
        "<style>body{font-family:Arial,sans-serif;padding:20px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px}th{background:#222;color:#fff}</style>"
        f"</head><body><h1>Generated Backfill Coverage</h1><table><tr><th>Code</th><th>Before</th><th>Generated</th><th>After</th><th>Subtopic</th></tr>{''.join(trs)}</table></body></html>",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--target", type=int, default=TARGET_PER_SUBTOPIC)
    parser.add_argument("--apply", action="store_true", help="Also replace practice_questions.json after backing up scan-only data.")
    args = parser.parse_args()
    root = args.root
    source_path = root / "output_json" / "practice_questions.json"
    backup_path = root / "output_json" / "practice_questions_scan_only.json"
    taxonomy_path = root / "output_json" / "english_taxonomy_v2.json"
    if backup_path.exists():
        input_path = backup_path
    else:
        input_path = source_path
    data = json.loads(input_path.read_text(encoding="utf-8"))
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    questions = [q for q in data.get("questions", []) if q.get("source_type") != "generated_backfill"]
    counts = Counter(q.get("knowledge_subtopic_code_v2") for q in questions)
    generated = []
    rows = []
    for subtopic in taxonomy.get("knowledge_subtopics", []):
        code = subtopic["subtopic_code"]
        before = counts[code]
        need = max(0, args.target - before)
        for index in range(1, need + 1):
            generated.append(generated_question(subtopic, index, before))
        rows.append({"code": code, "title": subtopic["subtopic_title"], "before": before, "generated": need, "after": before + need})
    balanced = dict(data)
    balanced["generated_at"] = datetime.now().isoformat(timespec="seconds")
    balanced["coverage_target_per_subtopic"] = args.target
    balanced["total_generated_backfill_questions"] = len(generated)
    balanced["questions"] = questions + generated
    balanced["total_questions"] = len(balanced["questions"])
    balanced["generated_backfill_questions"] = generated
    out_json = root / "output_json" / "practice_questions_balanced.json"
    out_json.write_text(json.dumps(balanced, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.apply:
        if not backup_path.exists():
            shutil.copy2(source_path, backup_path)
            print(f"Backed up scan-only data: {backup_path}")
        else:
            print(f"Using existing scan-only backup: {backup_path}")
        source_path.write_text(json.dumps(balanced, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Applied balanced data: {source_path}")
    out_preview = root / "previews" / "practice_backfill_coverage.html"
    write_preview(out_preview, rows)
    print(f"Saved: {out_json}")
    print(f"Saved: {out_preview}")
    print(f"Generated: {len(generated)}")


if __name__ == "__main__":
    main()
