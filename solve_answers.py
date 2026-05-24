import argparse
import json
import logging
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
from supabase import Client, create_client

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

# ============================================================
# CONFIG
# ============================================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://eqrrjarsnrtvlsdfjhph.supabase.co")
SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVxcnJqYXJzbnJ0dmxzZGZqaHBoIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODIxNDAzOSwiZXhwIjoyMDkzNzkwMDM5fQ.4fxCziaZpYcqMlsDm8BxPgCdMFX7mbIoene1XI2z7Yw",
)
CLAUDE_API_KEY = os.getenv("NINEROUTER_API_KEY") or os.getenv("CLAUDE_API_KEY", "")
CLAUDE_BASE_URL = os.getenv("CLAUDE_BASE_URL", "http://localhost:20128")
MODEL_NAME = os.getenv("SOLVE_MODEL", "cc/claude-sonnet-4-6")
RETRY_COUNT = int(os.getenv("SOLVE_RETRY_COUNT", "3"))
RATE_LIMIT_DELAY = float(os.getenv("SOLVE_RATE_LIMIT_DELAY", "2"))
SOLVE_RUN_DIR = Path(os.getenv("SOLVE_RUN_DIR", "artifacts/runs/solve_runs"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def normalize_anthropic_base_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1"):
        return base_url[:-3]
    return base_url


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    if not value or value.lower() in {"null", "none", "n/a"}:
        return None
    return value


def normalize_numeric_answer(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else value

    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a"}:
        return None

    compact = text.replace(" ", "").replace(",", ".")
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", compact):
        number = float(compact)
        return int(number) if number.is_integer() else number

    fraction_match = re.fullmatch(r"([-+]?\d+)/(\d+)", compact)
    if fraction_match:
        numerator = int(fraction_match.group(1))
        denominator = int(fraction_match.group(2))
        if denominator:
            number = numerator / denominator
            return int(number) if number.is_integer() else number

    single_number = re.fullmatch(r".*?([-+]?\d+(?:[\.,]\d+)?).*", text)
    if single_number:
        number = float(single_number.group(1).replace(",", "."))
        return int(number) if number.is_integer() else number

    return None


def parse_statements(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def extract_json_object(raw_text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw_text):
        try:
            data, _ = decoder.raw_decode(raw_text[match.start() :].strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue

    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if match:
        json_str = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r"\\\\", match.group(0))
        data = json.loads(json_str)
        if isinstance(data, dict):
            return data

    raise ValueError(f"Không parse được JSON từ Claude: {raw_text[:300]!r}")


def get_response_text(response: Any) -> str:
    raw_text = ""
    for block in response.content:
        if getattr(block, "type", "") == "text":
            raw_text += block.text
    return raw_text.strip()


def system_prompt(q_type: str) -> str:
    base = (
        "Bạn là giáo viên Toán THPT. Hãy giải chính xác, ngắn gọn nhưng đủ bước, dùng LaTeX chuẩn. "
        "Chỉ trả về JSON hợp lệ, không markdown, không thêm chữ ngoài JSON. "
        "Nếu đề thiếu dữ kiện hoặc có khả năng cần hình nhưng không có hình, vẫn giải theo dữ kiện text và đặt needs_review=true."
    )

    if q_type == "multiple_choice":
        return (
            base
            + '\nSchema: {"exp": "lời giải", "ans": "A|B|C|D|None|null", "val": "giá trị đúng nếu không có lựa chọn nào đúng", "needs_review": false, "note": null}. '
            "Nếu không có A/B/C/D nào đúng, trả ans=\"None\" và điền val."
        )
    if q_type == "true_false":
        return (
            base
            + '\nSchema: {"exp": "lời giải", "ans": "DSSD", "needs_review": false, "note": null}. '
            "ans gồm đúng 4 ký tự D/S theo thứ tự a,b,c,d; D là đúng, S là sai."
        )
    if q_type == "short_answer":
        return (
            base
            + '\nSchema: {"exp": "lời giải", "ans": "đáp án cuối", "needs_review": false, "note": null}. '
            "ans ưu tiên số nguyên/thập phân dùng dấu chấm, ví dụ 10.5."
        )
    return base + '\nSchema: {"exp": "lời giải", "ans": null, "needs_review": true, "note": "lý do"}'


def question_field(question: dict[str, Any], key: str) -> Any:
    value = question.get(key)
    if value not in (None, "", [], {}):
        return value
    return parse_raw_item(question.get("raw_text")).get(key)


def is_placeholder_question_text(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    normalized = unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("ascii").lower()
    if re.fullmatch(r"cau\s*\d+[:.]?", normalized):
        return True
    return bool(re.fullmatch(r"(?i)c[âa]u\s*\d+[:.]?", text))


def has_answerable_content(question: dict[str, Any]) -> bool:
    q_type = question.get("question_type")
    question_text = question_field(question, "question_text")
    has_question = not is_placeholder_question_text(question_text)

    if q_type == "multiple_choice":
        options = [normalize_optional_text(question_field(question, f"option_{letter}")) for letter in "abcd"]
        return has_question and sum(1 for option in options if option) >= 2

    if q_type == "true_false":
        statements = parse_statements(question_field(question, "statements"))
        return has_question or any(normalize_optional_text(stmt.get("text")) for stmt in statements)

    if q_type == "short_answer":
        return has_question

    return False


def build_user_content(question: dict[str, Any]) -> str:
    q_type = question["question_type"]
    question_text = question_field(question, "question_text")
    content = [f"ĐỀ:\n{question.get('question_text') or ''}"]

    if q_type == "multiple_choice":
        content.append(
            "LỰA CHỌN:\n"
            f"A. {question.get('option_a') or ''}\n"
            f"B. {question.get('option_b') or ''}\n"
            f"C. {question.get('option_c') or ''}\n"
            f"D. {question.get('option_d') or ''}"
        )
        if question.get("correct_answer"):
            content.append(f"Đáp án hiện có trong DB để tham khảo/kiểm tra: {question['correct_answer']}")

        content[-1] = (
            "LUA CHON:\n"
            f"A. {question_field(question, 'option_a') or ''}\n"
            f"B. {question_field(question, 'option_b') or ''}\n"
            f"C. {question_field(question, 'option_c') or ''}\n"
            f"D. {question_field(question, 'option_d') or ''}"
        )
        correct_answer = question_field(question, "correct_answer")
        if correct_answer and not question.get("correct_answer"):
            content.append(f"Dap an hien co trong DB de tham khao/kiem tra: {correct_answer}")

    elif q_type == "true_false":
        statements = parse_statements(question.get("statements"))
        if not statements:
            statements = parse_statements(question_field(question, "statements"))
        lines = ["CÁC MỆNH ĐỀ:"]
        for index, stmt in enumerate(statements):
            label = stmt.get("label") or chr(97 + index)
            lines.append(f"{label}. {stmt.get('text') or ''}")
        content.append("\n".join(lines))

    elif q_type == "short_answer":
        if question.get("numeric_answer") is not None:
            content.append(f"Đáp án số hiện có trong DB để tham khảo/kiểm tra: {question['numeric_answer']}")

    content[0] = f"DE:\n{question_text or ''}"
    return "\n\n".join(content)


def solve_with_claude(client: anthropic.Anthropic, question: dict[str, Any]) -> dict[str, Any] | None:
    q_type = question["question_type"]
    last_error: Exception | None = None

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            response = client.messages.create(
                model=MODEL_NAME,
                max_tokens=2500,
                temperature=0,
                system=system_prompt(q_type),
                messages=[{"role": "user", "content": build_user_content(question)}],
                extra_headers={"User-Agent": "curl/8.7.1"},
            )
            return extract_json_object(get_response_text(response))
        except Exception as exc:
            last_error = exc
            log.warning("Gọi Claude lỗi lần %s/%s cho %s: %s", attempt, RETRY_COUNT, question["id"], exc)
            if attempt < RETRY_COUNT:
                time.sleep(min(30, 2**attempt + random.random()))

    log.error("Bỏ qua %s sau %s lần lỗi: %s", question["id"], RETRY_COUNT, last_error)
    return None


def normalize_tf_answer(ans: Any) -> str | None:
    if ans is None:
        return None
    text = str(ans).strip().upper()
    text = (
        text.replace("Đ", "D")
        .replace("TRUE", "D")
        .replace("FALSE", "S")
        .replace(" ", "")
        .replace("-", "")
        .replace(",", "")
    )
    if re.fullmatch(r"[DS]{4}", text):
        return text
    return None


def update_statements_answers(statements_value: Any, ans: str) -> list[dict[str, Any]]:
    statements = parse_statements(statements_value)
    while len(statements) < 4:
        statements.append({"label": chr(97 + len(statements)), "text": "", "answer": None})
    for index, char in enumerate(ans[:4]):
        statements[index]["answer"] = char == "D"
    return statements


def build_update(question: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
    q_type = question["question_type"]
    exp = normalize_optional_text(result.get("exp"))
    needs_review = bool(result.get("needs_review", False))
    update_data: dict[str, Any] = {
        "explanation": exp,
        "answer_source": "claude_ai",
        "needs_review": needs_review,
    }

    ans = result.get("ans")
    if q_type == "multiple_choice":
        ans_text = str(ans).strip().upper() if ans is not None else None
        if ans_text in {"A", "B", "C", "D"}:
            existing = normalize_optional_text(question.get("correct_answer"))
            update_data["correct_answer"] = ans_text
            if existing and existing != ans_text:
                update_data["needs_review"] = True
        elif ans_text == "NONE":
            correct_value = normalize_optional_text(result.get("val"))
            if correct_value:
                update_data["option_a"] = correct_value
                update_data["correct_answer"] = "A"
                update_data["answer_source"] = "claude_ai_fixed_option"
                update_data["explanation"] = (
                    "[THAY THẾ ĐÁP ÁN A DO ĐỀ/INGEST KHÔNG CÓ LỰA CHỌN ĐÚNG]\n\n"
                    + (exp or "")
                )
                update_data["needs_review"] = False
            else:
                update_data["needs_review"] = True
        else:
            update_data["needs_review"] = True

    elif q_type == "short_answer":
        numeric_answer = normalize_numeric_answer(ans)
        if numeric_answer is None:
            update_data["needs_review"] = True
        else:
            update_data["numeric_answer"] = numeric_answer

    elif q_type == "true_false":
        tf_ans = normalize_tf_answer(ans)
        if not tf_ans:
            update_data["needs_review"] = True
        else:
            update_data["correct_answer"] = tf_ans
            update_data["statements"] = json.dumps(
                update_statements_answers(question.get("statements"), tf_ans),
                ensure_ascii=False,
            )

    else:
        update_data["needs_review"] = True

    return update_data


def fetch_questions(supabase: Client, limit: int, include_visual: bool) -> list[dict[str, Any]]:
    query = (
        supabase.table("questions")
        .select(
            "id, question_type, question_text, option_a, option_b, option_c, option_d, "
            "correct_answer, statements, numeric_answer, explanation, needs_visual, raw_text"
        )
        .in_("question_type", ["multiple_choice", "short_answer", "true_false"])
        .is_("explanation", "null")
    )
    if not include_visual:
        query = query.eq("needs_visual", False)
    if limit > 0:
        query = query.limit(limit)
    return query.execute().data or []


def parse_raw_item(raw_text: Any) -> dict[str, Any]:
    if isinstance(raw_text, dict):
        return raw_text
    if not raw_text:
        return {}
    try:
        data = json.loads(raw_text)
        return data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def strip_old_option_replacement_marker(explanation: str | None) -> str | None:
    if not explanation:
        return explanation
    marker = "[THAY THẾ ĐÁP ÁN A DO ĐỀ LỖI]"
    if explanation.startswith(marker):
        return explanation[len(marker) :].lstrip()
    return explanation


def build_repair_update(row: dict[str, Any]) -> dict[str, Any]:
    q_type = row.get("question_type")
    raw_item = parse_raw_item(row.get("raw_text"))
    update_data: dict[str, Any] = {}

    if q_type == "short_answer":
        wrong_answer = row.get("correct_answer")
        if wrong_answer is not None and row.get("numeric_answer") is None:
            numeric_answer = normalize_numeric_answer(wrong_answer)
            update_data["numeric_answer"] = numeric_answer
            update_data["correct_answer"] = None
            if isinstance(numeric_answer, str):
                update_data["needs_review"] = True

    elif q_type == "true_false":
        tf_ans = normalize_tf_answer(row.get("correct_answer"))
        if tf_ans:
            update_data["correct_answer"] = tf_ans
            update_data["statements"] = json.dumps(
                update_statements_answers(row.get("statements"), tf_ans),
                ensure_ascii=False,
            )

    elif q_type == "multiple_choice":
        # Giữ nguyên các câu bản cũ đã sửa option A. Đây là workflow chủ ý:
        # nếu đề/ingest sai lựa chọn, thay option A bằng giá trị đúng để câu vẫn dùng được.
        pass

    return update_data


def fetch_old_solved_rows(supabase: Client, limit: int) -> list[dict[str, Any]]:
    query = (
        supabase.table("questions")
        .select(
            "id, question_type, option_a, correct_answer, statements, numeric_answer, "
            "explanation, raw_text, answer_source, needs_review"
        )
        .eq("answer_source", "claude_ai")
        .in_("question_type", ["multiple_choice", "short_answer", "true_false"])
    )
    if limit > 0:
        query = query.limit(limit)
    return query.execute().data or []


def repair_old_run(supabase: Client, limit: int, dry_run: bool) -> None:
    rows = fetch_old_solved_rows(supabase, limit)
    log.info("🧯 Kiểm tra %s dòng answer_source=claude_ai từ lần chạy cũ.", len(rows))
    fixed = 0
    skipped = 0

    for index, row in enumerate(rows, start=1):
        update_data = build_repair_update(row)
        if not update_data:
            skipped += 1
            continue

        fixed += 1
        log.info("[%s/%s] Repair %s: %s", index, len(rows), row["id"], json.dumps(update_data, ensure_ascii=False))
        if not dry_run:
            supabase.table("questions").update(update_data).eq("id", row["id"]).execute()

    log.info("✅ Repair xong. Cần sửa: %s | Bỏ qua: %s | dry_run=%s", fixed, skipped, dry_run)


def make_run_log_path() -> Path:
    SOLVE_RUN_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return SOLVE_RUN_DIR / f"solve_run_{timestamp}.jsonl"


def append_run_log(path: Path, row: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_run_ids(path: Path) -> list[str]:
    ids: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            q_id = data.get("id")
            if q_id:
                ids.append(q_id)
    return ids


def publish_question_ids(supabase: Client, ids: list[str], dry_run: bool) -> None:
    unique_ids = list(dict.fromkeys(ids))
    if not unique_ids:
        log.info("Không có câu nào để public.")
        return

    log.info("📢 Public %s câu đã solve.", len(unique_ids))
    if dry_run:
        log.info("DRY RUN publish ids: %s", unique_ids[:50])
        return

    batch_size = 100
    for start in range(0, len(unique_ids), batch_size):
        batch = unique_ids[start : start + batch_size]
        supabase.table("questions").update({"is_published": True}).in_("id", batch).execute()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Giải/điền đáp án cho questions chưa có explanation.")
    parser.add_argument("--limit", type=int, default=0, help="Giới hạn số câu xử lý.")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ gọi Claude và log kết quả, không ghi DB.")
    parser.add_argument("--include-visual", action="store_true", help="Bao gồm cả câu needs_visual=true.")
    parser.add_argument("--repair-old-run", action="store_true", help="Sửa dữ liệu đã ghi bởi bản solve_answers.py cũ.")
    parser.add_argument("--publish-solved", action="store_true", help="Public đúng các câu giải thành công trong lần chạy này.")
    parser.add_argument("--publish-from-run", help="Public các câu trong một file solve_runs/*.jsonl đã lưu trước đó.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_url = normalize_anthropic_base_url(CLAUDE_BASE_URL)
    log.info("Router endpoint: %s | model: %s", base_url, MODEL_NAME)
    log.info("🚀 BẮT ĐẦU GIẢI TOÁN (Claude %s)", MODEL_NAME)

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    if args.publish_from_run:
        publish_question_ids(supabase, read_run_ids(Path(args.publish_from_run)), args.dry_run)
        return

    if args.repair_old_run:
        repair_old_run(supabase, args.limit, args.dry_run)
        return

    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY or "local-9router", base_url=base_url)
    questions = fetch_questions(supabase, args.limit, args.include_visual)
    log.info("📚 Có %s câu cần xử lý.", len(questions))
    run_log_path = make_run_log_path()
    solved_ids: list[str] = []
    log.info("🧾 Run log: %s", run_log_path)

    solved = 0
    failed = 0
    for index, question in enumerate(questions, start=1):
        q_id = question["id"]
        q_type = question["question_type"]
        log.info("[%s/%s] ID: %s | Loại: %s", index, len(questions), q_id, q_type)

        if not has_answerable_content(question):
            failed += 1
            log.warning("Bo qua %s vi thieu noi dung de/option trong question_text/raw_text.", q_id)
            continue

        result = solve_with_claude(client, question)
        if not result:
            failed += 1
            continue

        update_data = build_update(question, result)
        if not update_data:
            failed += 1
            continue

        if args.dry_run:
            log.info("   DRY RUN update: %s", json.dumps(update_data, ensure_ascii=False)[:1000])
        else:
            try:
                supabase.table("questions").update(update_data).eq("id", q_id).execute()
            except Exception as exc:
                failed += 1
                log.error("Update DB lỗi cho %s: %s | data=%s", q_id, exc, json.dumps(update_data, ensure_ascii=False))
                continue
            solved_ids.append(q_id)
            append_run_log(
                run_log_path,
                {
                    "id": q_id,
                    "question_type": q_type,
                    "answer_source": update_data.get("answer_source"),
                    "needs_review": update_data.get("needs_review"),
                    "time": datetime.now().isoformat(),
                },
            )
        solved += 1
        time.sleep(RATE_LIMIT_DELAY)

    if args.publish_solved and not args.dry_run:
        publish_question_ids(supabase, solved_ids, dry_run=False)

    log.info("✅ Xong. Thành công: %s | Lỗi/bỏ qua: %s", solved, failed)


if __name__ == "__main__":
    main()
