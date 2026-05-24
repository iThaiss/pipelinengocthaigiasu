from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import anthropic

import repair_standard_exam_artifacts as repair
import standard_exam_ingest as ingest


SYSTEM = """Bạn là giáo viên Toán THPT và kiểm định viên đề thi.
Nhiệm vụ: xử lý các câu đang needs_review trong pipeline.
Hãy tự giải lại từ đầu, dùng hình/trích nguồn nếu có, rồi ra quyết định cuối cùng.
Chỉ giữ needs_review=true nếu đề thật sự thiếu dữ kiện/hình không đủ đọc hoặc không có đáp án nào hợp lệ.
Nếu đáp án/lời giải gốc sai, trả đáp án đúng của AI và source_answer_status="source_wrong".
Nếu đáp án gốc đúng, trả source_answer_status="matches".
Giữ phong cách lời giải giống đề 1/10: gọn, mạch lạc, có công thức/lý thuyết cần nhớ, không rườm rà.
Với đúng/sai phải giải thích từng ý a,b,c,d. Với hình học không gian tính góc/khoảng cách, ưu tiên gắn hệ trục tọa độ nếu tự nhiên.
Nếu có mẹo Casio 580VN X hữu ích, thêm một câu ngắn cuối exp.
Trả JSON thuần, không markdown ngoài JSON."""


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


def answer_text(question: dict[str, Any]) -> str | None:
    return ingest.answer_text_for(question)


def build_prompt(question: dict[str, Any]) -> str:
    raw = question.get("raw_text") if isinstance(question.get("raw_text"), dict) else {}
    source_file = str(question.get("source_file") or "")
    page_number = int(question.get("page_number") or 0)
    source_window = repair.native_window(source_file, page_number, page_span=4)[:9000] if source_file and page_number else ""
    parts = [
        f"Vị trí: {question.get('section_code')} câu {question.get('question_number')}",
        f"Loại: {question.get('question_type')}",
        f"Đề bài:\n{question.get('question_text') or ''}",
    ]
    if question.get("question_type") == "multiple_choice":
        parts.append(
            "Lựa chọn:\n"
            f"A. {question.get('option_a') or ''}\n"
            f"B. {question.get('option_b') or ''}\n"
            f"C. {question.get('option_c') or ''}\n"
            f"D. {question.get('option_d') or ''}"
        )
    if question.get("question_type") == "true_false":
        lines = ["Mệnh đề:"]
        for statement in question.get("statements") or []:
            if isinstance(statement, dict):
                lines.append(f"{statement.get('label')}. {statement.get('text')}")
        parts.append("\n".join(lines))
    current_answer = answer_text(question)
    if current_answer:
        parts.append(f"Đáp án hiện tại/source: {current_answer}")
    if raw.get("source_answer_conflict"):
        parts.append(f"Conflict hiện tại: {json.dumps(raw.get('source_answer_conflict'), ensure_ascii=False)}")
    if raw.get("review_reason"):
        parts.append(f"Lý do review hiện tại: {raw.get('review_reason')}")
    source_solution = ingest.source_solution_text(question)
    if source_solution:
        parts.append(f"Lời giải/nguồn gốc trong PDF:\n<source_solution>\n{source_solution[:6000]}\n</source_solution>")
    if source_window:
        parts.append(f"Trích text PDF quanh câu, có thể nhiễu OCR/watermark:\n<pdf_text>\n{source_window}\n</pdf_text>")
    parts.append(
        "Yêu cầu JSON:\n"
        '{"exp":"lời giải cuối cùng", "ans":"A|B|C|D hoặc DSSD hoặc số", '
        '"source_answer_status":"matches|source_wrong|ai_uncertain|null", '
        '"canonical_subtopic_id":123, "difficulty":"Nhận biết|Thông hiểu|Vận dụng|Vận dụng cao|null", '
        '"needs_review":false, "review_reason":null, '
        '"statement_explanations":[{"label":"a","answer":true,"exp":"..."},...]}'
    )
    return "\n\n".join(parts)


def call_model(client: anthropic.Anthropic, question: dict[str, Any]) -> dict[str, Any]:
    content: list[dict[str, Any]] | str = build_prompt(question)
    if question.get("needs_visual") and question.get("source_file") and question.get("page_number"):
        try:
            image_b64 = ingest.render_page_from_file_png_base64(
                str(question["source_file"]),
                int(question["page_number"]) - 1,
                zoom=1.9,
            )
            content = [
                {"type": "text", "text": build_prompt(question) + "\n\nHãy đọc ảnh trang kèm theo nếu text PDF thiếu hình/bảng."},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
            ]
        except Exception:
            content = build_prompt(question)
    response = client.messages.create(
        model=os.getenv("CLAUDE_MODEL", "gwai/claude-sonnet-4-6"),
        max_tokens=4500,
        temperature=0,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        extra_headers={"User-Agent": "curl/8.7.1"},
    )
    return ingest.extract_json_object(ingest.router_response_text(response))


def find_json(preview_dir: Path, exam_index: int) -> Path:
    return repair.find_json_for_exam(preview_dir, exam_index)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exam-index", type=int, action="append", required=True)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--env-file", default=r"D:\Projects\ngocthaigiasu-app\.env.local")
    args = parser.parse_args()

    load_env_file(args.env_file)
    client = anthropic.Anthropic(
        api_key=ingest.CLAUDE_API_KEY or "local-9router",
        base_url=ingest.normalize_anthropic_base_url(ingest.CLAUDE_BASE_URL),
    )
    results = []
    for exam_index in args.exam_index:
        path = find_json(ingest.PREVIEW_DIR, exam_index)
        data = json.loads(path.read_text(encoding="utf-8"))
        changed = 0
        for question in data.get("questions", []):
            if not question.get("needs_review"):
                continue
            result = call_model(client, question)
            ingest.apply_enrichment(question, result)
            changed += 1
        repair.refresh_audit(data)
        source_file = str(data.get("exam", {}).get("source_file") or "")
        json_path, html_path = ingest.write_outputs(data, source_file, args.preview)
        commit_result = None
        if args.commit:
            commit_result = ingest.commit_to_supabase(data, upload_visuals=True, replace_existing=True)
        results.append(
            {
                "exam_index": exam_index,
                "changed": changed,
                "status": data.get("audit", {}).get("status"),
                "needs_review": data.get("audit", {}).get("needs_review"),
                "missing_answers": data.get("audit", {}).get("missing_answers"),
                "json": str(json_path),
                "html": str(html_path) if html_path else None,
                "commit": commit_result,
            }
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
