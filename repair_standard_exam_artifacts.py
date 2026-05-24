from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import anthropic
import fitz

import standard_exam_ingest as ingest
import standard_exam_preview_html


PROMO_MARKERS = (
    "Khoá học SSLive",
    "Khóa học SSLive",
    "MÔN TOÁN",
    "Classin",
    "ssstudy.vn",
    "Shared By",
    "Fanpage",
    "Đăng Ký Khóa Học",
    "Trên bước đường thành công",
)


def compact_pdf_text(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(marker.casefold() in stripped.casefold() for marker in PROMO_MARKERS):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def native_window(source_file: str, page_number: int, page_span: int = 3) -> str:
    if not source_file or not Path(source_file).exists():
        return ""
    pieces = []
    with fitz.open(source_file) as pdf:
        start = max(0, page_number - 1)
        end = min(pdf.page_count, start + page_span)
        for page_index in range(start, end):
            pieces.append(pdf[page_index].get_text("text") or "")
    return compact_pdf_text("\n".join(pieces))


def question_chunk_from_native(text: str, question_number: int) -> str:
    pattern = re.compile(rf"Câu\s+{question_number}\s*:\s*", re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    next_match = re.search(rf"\n?Câu\s+{question_number + 1}\s*:\s*", text[start:], re.IGNORECASE)
    end = start + next_match.start() if next_match else len(text)
    return text[start:end].strip()


def normalize_inline(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip(" .\n\t")


def parse_source_answer(chunk: str, choices: str) -> str | None:
    match = re.search(r"Lời\s+giải\s+Chọn\s+([ABCD])", chunk, re.IGNORECASE)
    if match and "ABCD" == choices:
        return match.group(1).upper()
    return None


def hydrate_missing_native_parts(question: dict[str, Any]) -> None:
    source_file = str(question.get("source_file") or "")
    page_number = int(question.get("page_number") or 0)
    question_number = int(question.get("question_number") or 0)
    if not source_file or page_number <= 0 or question_number <= 0:
        return
    q_type = question.get("question_type")
    needs_mc_options = q_type == "multiple_choice" and not all(question.get(f"option_{label}") for label in "abcd")
    statements = question.get("statements") if isinstance(question.get("statements"), list) else []
    needs_tf_statements = q_type == "true_false" and (len(statements) < 4 or any((stmt.get("text") or "").strip() == "..." for stmt in statements if isinstance(stmt, dict)))
    if not needs_mc_options and not needs_tf_statements:
        return

    chunk = question_chunk_from_native(native_window(source_file, page_number), question_number)
    if not chunk:
        return
    before_solution = re.split(r"\bLời\s+giải\b", chunk, maxsplit=1, flags=re.IGNORECASE)[0]
    raw_text = question.setdefault("raw_text", {})
    if needs_mc_options:
        option_matches = list(
            re.finditer(
                r"(?ms)(?:^|\n)\s*([ABCD])\.\s*(.*?)(?=(?:\n\s*[ABCD]\.)|\n\s*Lời\s+giải|$)",
                before_solution,
            )
        )
        if len(option_matches) >= 4:
            for match in option_matches[:4]:
                label = match.group(1).lower()
                question[f"option_{label}"] = normalize_inline(match.group(2))
                raw_text[f"option_{label}"] = question[f"option_{label}"]
            answer = parse_source_answer(chunk, "ABCD")
            if answer:
                question["correct_answer"] = answer
                question["answer_source"] = "source_extracted"
                raw_text["correct_answer"] = answer
            raw_text["native_gap_repair"] = "filled_missing_multiple_choice_options"

    if needs_tf_statements:
        statement_matches = list(
            re.finditer(
                r"(?ms)(?:^|\n)\s*([abcd])\)\s*(.*?)(?=(?:\n\s*[abcd]\))|\n\s*Lời\s+giải|$)",
                before_solution,
            )
        )
        if len(statement_matches) >= 4:
            answer_by_label = {
                match.group(1).lower(): match.group(2).casefold().startswith("đ")
                for match in re.finditer(r"(?im)^([abcd])\)\s*(Đúng|Sai)", chunk)
            }
            clean_statements = []
            for match in statement_matches[:4]:
                label = match.group(1).lower()
                clean_statements.append(
                    {
                        "label": label,
                        "text": normalize_inline(match.group(2)),
                        "answer": answer_by_label.get(label),
                    }
                )
            question["statements"] = clean_statements
            raw_text["statements"] = clean_statements
            if all(isinstance(stmt.get("answer"), bool) for stmt in clean_statements):
                question["answer_source"] = "source_extracted"
            raw_text["native_gap_repair"] = "filled_missing_true_false_statements"


def load_env_file(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


def find_json_for_exam(preview_dir: Path, exam_index: int) -> Path:
    candidates: list[Path] = []
    for path in preview_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if int(data.get("exam", {}).get("exam_index") or -999999) == exam_index:
            candidates.append(path)
    if not candidates:
        raise FileNotFoundError(f"No preview JSON found for exam_index={exam_index}")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def refresh_audit(data: dict[str, Any]) -> None:
    questions = data.get("questions") if isinstance(data.get("questions"), list) else []
    failures: list[dict[str, Any]] = []
    ingest.apply_quality_audit(questions, failures)
    audit = ingest.audit_exam(questions, failures)
    data["audit"] = audit
    data["sections"] = ingest.build_sections(questions)
    exam = data.setdefault("exam", {})
    exam["audit_json"] = audit
    exam["status"] = audit["status"]
    exam["extracted_question_count"] = audit["extracted_question_count"]


def repair_file(path: Path, client: anthropic.Anthropic, commit: bool, preview: bool) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    questions = data.get("questions")
    if not isinstance(questions, list):
        raise ValueError(f"Invalid questions in {path}")

    for question in questions:
        hydrate_missing_native_parts(question)
    ingest.enrich_questions(client, questions, mode="all")
    refresh_audit(data)

    source_file = str(data.get("exam", {}).get("source_file") or "")
    json_path, html_path = ingest.write_outputs(data, source_file, preview)
    result = {
        "exam_index": data.get("exam", {}).get("exam_index"),
        "status": data.get("audit", {}).get("status"),
        "needs_review": data.get("audit", {}).get("needs_review"),
        "missing_answers": data.get("audit", {}).get("missing_answers"),
        "json": str(json_path),
        "html": str(html_path) if html_path else None,
    }
    if preview and html_path:
        standard_exam_preview_html.render_html(data, html_path)
    if commit:
        result["commit"] = ingest.commit_to_supabase(data, upload_visuals=True, replace_existing=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair already-scanned standard exam JSON artifacts.")
    parser.add_argument("--exam-index", type=int, action="append", required=True)
    parser.add_argument("--preview-dir", default=str(ingest.PREVIEW_DIR))
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--env-file", default=r"D:\Projects\ngocthaigiasu-app\.env.local")
    args = parser.parse_args()

    load_env_file(args.env_file)
    client = anthropic.Anthropic(
        api_key=ingest.CLAUDE_API_KEY or "local-9router",
        base_url=ingest.normalize_anthropic_base_url(ingest.CLAUDE_BASE_URL),
    )
    preview_dir = Path(args.preview_dir)
    results = []
    for exam_index in args.exam_index:
        path = find_json_for_exam(preview_dir, exam_index)
        ingest.log.info("Repair exam_index=%s from %s", exam_index, path)
        results.append(repair_file(path, client, commit=args.commit, preview=args.preview))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
