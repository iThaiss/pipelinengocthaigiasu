import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any

import anthropic
from supabase import create_client

from ingest_pipeline import (
    CLAUDE_API_KEY,
    CLAUDE_BASE_URL,
    CLAUDE_MODEL,
    SUPABASE_KEY,
    SUPABASE_URL,
    normalize_anthropic_base_url,
    normalize_optional_text,
)
from standard_exam_ingest import (
    create_message_with_model_fallback,
    extract_json_object,
    question_has_answer,
    router_response_text,
    strip_internal_solution_audit,
)


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")


LOG_FILE = os.getenv("STANDARD_EXAM_REWRITE_LOG_FILE", "logs/standard_exam_rewrite_solutions.log")
REWRITE_MODEL = os.getenv("STANDARD_EXAM_REWRITE_MODEL", CLAUDE_MODEL)
REWRITE_SLEEP_SECONDS = float(os.getenv("STANDARD_EXAM_REWRITE_SLEEP_SECONDS", "1.0"))
STYLE_VERSION = "student_solution_v1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("standard_exam_rewrite_solutions")


def chunked(values: list[str], size: int = 80) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def answer_text(question: dict[str, Any]) -> str:
    q_type = question.get("question_type")
    if q_type == "multiple_choice":
        return str(question.get("correct_answer") or "")
    if q_type == "short_answer":
        return str(question.get("numeric_answer") or "")
    if q_type == "true_false":
        statements = question.get("statements") if isinstance(question.get("statements"), list) else []
        chars = []
        for statement in statements:
            if not isinstance(statement, dict) or not isinstance(statement.get("answer"), bool):
                chars.append("?")
            else:
                chars.append("D" if statement.get("answer") else "S")
        return "".join(chars)
    return ""


def options_text(question: dict[str, Any]) -> str:
    if question.get("question_type") != "multiple_choice":
        return ""
    lines = []
    for key, label in (("option_a", "A"), ("option_b", "B"), ("option_c", "C"), ("option_d", "D")):
        value = normalize_optional_text(question.get(key))
        if value:
            lines.append(f"{label}. {value}")
    return "\n".join(lines)


def statements_text(question: dict[str, Any]) -> str:
    if question.get("question_type") != "true_false":
        return ""
    lines = []
    statements = question.get("statements") if isinstance(question.get("statements"), list) else []
    for index, statement in enumerate(statements):
        if not isinstance(statement, dict):
            continue
        label = statement.get("label") or chr(97 + index)
        verdict = ""
        if isinstance(statement.get("answer"), bool):
            verdict = "Đúng" if statement["answer"] else "Sai"
        text = normalize_optional_text(statement.get("text")) or ""
        old_exp = normalize_optional_text(statement.get("explanation")) or ""
        lines.append(f"{label}. {text}\nĐáp án hiện tại: {verdict}\nGiải thích cũ: {old_exp}")
    return "\n\n".join(lines)


def rewrite_system_prompt(q_type: str) -> str:
    base = (
        "Bạn là giáo viên Toán THPT viết lời giải cho học sinh lớp 12 ôn thi tốt nghiệp. "
        "Nhiệm vụ là VIẾT LẠI lời giải cho dễ hiểu hơn, không phải đổi đáp án. "
        "Giữ nguyên đáp án hiện tại trừ khi dữ kiện hoàn toàn thiếu; nếu thấy mâu thuẫn thì xử lý nội bộ bằng needs_review, không nói trong lời giải cho học sinh. "
        "Mọi công thức phải bọc bằng $...$ hoặc $$...$$. Không viết LaTeX trần. "
        "Không dùng đoạn văn quá dài; chia ý rõ, có tiêu đề ngắn. "
        "Lời giải hiển thị tuyệt đối không nhắc PDF, đáp án gốc, nguồn gốc, đối chiếu, trùng khớp, nguồn sai, hay kết quả tự giải. "
        "Chỉ trả JSON hợp lệ, không markdown ngoài JSON."
    )
    if q_type == "multiple_choice":
        return (
            base
            + "\nVới trắc nghiệm A/B/C/D: lời giải cần giúp học sinh nhớ công thức hoặc lý thuyết cơ bản. "
            "Không phân tích lan man cả 4 phương án nếu không cần. Format nên có: Ý tưởng, Công thức/lý thuyết cần nhớ, Áp dụng, Kết luận."
            '\nSchema: {"explanation":"bản lời giải đẹp để hiển thị cho học sinh",'
            '"solution_steps":[{"title":"Ý tưởng","content":"..."},{"title":"Công thức cần nhớ","content":"..."},{"title":"Áp dụng","content":"..."},{"title":"Kết luận","content":"..."}],'
            '"needs_review":false,"review_reason":null}.'
        )
    if q_type == "true_false":
        return (
            base
            + "\nVới câu đúng/sai: phải có một mạch giải chung liên kết các ý, sau đó giải thích từng ý a,b,c,d. "
            "Mỗi ý cần nêu vì sao đúng/sai, không chỉ nhắc lại đáp án. Các ý sau nên tận dụng kết quả hoặc nhận xét từ ý trước nếu có."
            '\nSchema: {"explanation":"mạch giải chung ngắn gọn và kết luận đáp án",'
            '"solution_steps":[{"title":"Mạch giải chung","content":"..."},{"title":"Kết luận","content":"..."}],'
            '"statement_explanations":[{"label":"a","answer":true,"exp":"..."},{"label":"b","answer":false,"exp":"..."},{"label":"c","answer":true,"exp":"..."},{"label":"d","answer":false,"exp":"..."}],'
            '"needs_review":false,"review_reason":null}.'
        )
    return (
        base
        + "\nVới câu trả lời ngắn: đặc biệt phải giải thích nền tảng cơ bản và các bước tính. "
        "Format nên có: Dữ kiện, Kiến thức/công thức, Tính toán từng bước, Kết quả. Đáp án cuối chỉ nhắc lại số hiện có, không thêm đơn vị."
        '\nSchema: {"explanation":"bản lời giải từng bước dễ hiểu",'
        '"solution_steps":[{"title":"Dữ kiện","content":"..."},{"title":"Kiến thức cần dùng","content":"..."},{"title":"Tính toán","content":"..."},{"title":"Kết quả","content":"..."}],'
        '"needs_review":false,"review_reason":null}.'
    )


def rewrite_user_prompt(question: dict[str, Any]) -> str:
    q_type = question.get("question_type")
    raw = question.get("raw_text") if isinstance(question.get("raw_text"), dict) else {}
    pieces = [
        f"Loại câu: {q_type}",
        f"Đề bài:\n{question.get('question_text') or ''}",
        f"Đáp án hiện tại cần giữ: {answer_text(question)}",
    ]
    if q_type == "multiple_choice":
        pieces.append(f"Các lựa chọn:\n{options_text(question)}")
    if q_type == "true_false":
        pieces.append(f"Các mệnh đề và giải thích cũ:\n{statements_text(question)}")
    old_explanation = normalize_optional_text(question.get("explanation"))
    if old_explanation:
        pieces.append(f"Lời giải cũ:\n{old_explanation}")
    source_solution = normalize_optional_text(raw.get("source_solution") or raw.get("explanation") or raw.get("solution_text"))
    if source_solution:
        pieces.append(
            "Lời giải/đáp án gốc từ file nếu có, chỉ dùng để kiểm tra nội bộ; không nhắc nguồn gốc hoặc đối chiếu trong lời giải:\n"
            f"{source_solution[:5000]}"
        )
    pieces.append(
        "Hãy viết lại lời giải để học sinh đọc một lần là hiểu hướng làm. "
        "Không đổi đáp án hiện tại. Nếu thiếu hình/dữ kiện làm lời giải không chắc, vẫn viết phần chắc chắn và đặt needs_review=true."
    )
    return "\n\n".join(pieces)


def clean_solution_text(value: Any) -> str | None:
    text = strip_internal_solution_audit(value)
    if not text:
        return None
    text = text.replace("**", "")
    text = text.replace("__", "")
    return text


def rewrite_solution(client: anthropic.Anthropic, question: dict[str, Any]) -> dict[str, Any] | None:
    response = create_message_with_model_fallback(
        client,
        primary_model=REWRITE_MODEL,
        max_tokens=3000,
        temperature=0,
        system=rewrite_system_prompt(str(question.get("question_type"))),
        messages=[{"role": "user", "content": rewrite_user_prompt(question)}],
        extra_headers={"User-Agent": "curl/8.7.1"},
    )
    return extract_json_object(router_response_text(response))


def build_update(question: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    update: dict[str, Any] = {}
    explanation = clean_solution_text(result.get("explanation"))
    if explanation:
        update["explanation"] = explanation

    raw = question.get("raw_text") if isinstance(question.get("raw_text"), dict) else {}
    raw = dict(raw)
    raw["solution_style_version"] = STYLE_VERSION
    raw["solution_rewritten_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    if isinstance(result.get("solution_steps"), list):
        cleaned_steps = []
        for step in result["solution_steps"]:
            if not isinstance(step, dict):
                continue
            cleaned_step = dict(step)
            cleaned_step["title"] = clean_solution_text(cleaned_step.get("title")) or cleaned_step.get("title")
            cleaned_step["content"] = clean_solution_text(cleaned_step.get("content")) or cleaned_step.get("content")
            cleaned_steps.append(cleaned_step)
        raw["solution_steps"] = cleaned_steps
    review_reason = normalize_optional_text(result.get("review_reason"))
    if review_reason:
        raw["solution_review_reason"] = review_reason
    update["raw_text"] = raw

    if question.get("question_type") == "true_false" and isinstance(result.get("statement_explanations"), list):
        statements = question.get("statements") if isinstance(question.get("statements"), list) else []
        by_label = {
            str(item.get("label") or "").strip().lower(): item
            for item in result["statement_explanations"]
            if isinstance(item, dict)
        }
        for index, statement in enumerate(statements):
            if not isinstance(statement, dict):
                continue
            label = str(statement.get("label") or chr(97 + index)).strip().lower()
            detail = by_label.get(label)
            if not detail:
                continue
            if isinstance(detail.get("answer"), bool):
                statement["answer"] = bool(detail["answer"])
            exp = clean_solution_text(detail.get("exp"))
            if exp:
                statement["explanation"] = exp
        update["statements"] = statements

    if bool(result.get("needs_review")):
        update["needs_review"] = True
    return update


def fetch_question_ids_for_exams(client: Any, exam_indexes: list[int] | None) -> list[str]:
    if not exam_indexes:
        rows = client.table("questions").select("id").order("created_at").execute().data or []
        return [row["id"] for row in rows if row.get("id")]
    exam_rows = (
        client.table("exam_sets")
        .select("id,exam_index")
        .in_("exam_index", exam_indexes)
        .execute()
        .data
        or []
    )
    exam_ids = [row["id"] for row in exam_rows if row.get("id")]
    if not exam_ids:
        return []
    relation_rows: list[dict[str, Any]] = []
    for batch in chunked(exam_ids, 20):
        relation_rows.extend(
            client.table("exam_questions").select("question_id").in_("exam_set_id", batch).execute().data or []
        )
    seen: set[str] = set()
    ids: list[str] = []
    for row in relation_rows:
        qid = row.get("question_id")
        if qid and qid not in seen:
            seen.add(qid)
            ids.append(qid)
    return ids


def fetch_questions(client: Any, ids: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    fields = (
        "id,question_type,question_text,option_a,option_b,option_c,option_d,"
        "correct_answer,numeric_answer,statements,explanation,raw_text,needs_review,"
        "answer_source,needs_visual,image_url"
    )
    for batch in chunked(ids, 80):
        rows.extend(client.table("questions").select(fields).in_("id", batch).execute().data or [])
    order = {qid: index for index, qid in enumerate(ids)}
    return sorted(rows, key=lambda row: order.get(row.get("id"), 10**9))


def main() -> None:
    parser = argparse.ArgumentParser(description="Rewrite standard exam explanations into student-friendly structured solutions.")
    parser.add_argument("--exam-index", type=int, action="append", help="Only rewrite questions from this exam index. Can be repeated.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of questions for a test run.")
    parser.add_argument("--only-needs-review", action="store_true", help="Only rewrite questions currently marked needs_review.")
    parser.add_argument("--skip-styled", action="store_true", help="Skip questions already rewritten with the current style version.")
    parser.add_argument("--dry-run", action="store_true", help="Do not update Supabase.")
    args = parser.parse_args()

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY).schema("standard_exam")
    base_url = normalize_anthropic_base_url(CLAUDE_BASE_URL)
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY or "local-9router", base_url=base_url)

    ids = fetch_question_ids_for_exams(supabase, args.exam_index)
    questions = fetch_questions(supabase, ids)
    if args.only_needs_review:
        questions = [question for question in questions if question.get("needs_review")]
    if args.skip_styled:
        questions = [
            question
            for question in questions
            if not isinstance(question.get("raw_text"), dict)
            or question["raw_text"].get("solution_style_version") != STYLE_VERSION
        ]
    questions = [question for question in questions if question_has_answer(question)]
    if args.limit:
        questions = questions[: args.limit]

    log.info("Rewrite solutions: questions=%s model=%s endpoint=%s dry_run=%s", len(questions), REWRITE_MODEL, base_url, args.dry_run)
    for index, question in enumerate(questions, 1):
        qid = question["id"]
        try:
            result = rewrite_solution(client, question)
            if not result:
                log.warning("No rewrite result for %s", qid)
                continue
            update = build_update(question, result)
            if args.dry_run:
                log.info("Dry-run %s/%s %s: %s", index, len(questions), qid, json.dumps(update, ensure_ascii=False)[:500])
            else:
                supabase.table("questions").update(update).eq("id", qid).execute()
                log.info("Updated %s/%s %s type=%s", index, len(questions), qid, question.get("question_type"))
        except Exception as exc:
            log.exception("Failed rewrite %s/%s %s: %s", index, len(questions), qid, exc)
        if REWRITE_SLEEP_SECONDS > 0:
            time.sleep(REWRITE_SLEEP_SECONDS)


if __name__ == "__main__":
    main()
