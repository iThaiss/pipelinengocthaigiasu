import argparse
import html
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import fitz
except ModuleNotFoundError:  # Preview can still render existing text/image URLs without local crop support.
    fitz = None


INTERNAL_SOLUTION_AUDIT_RE = re.compile(
    r"(?is)(?:\n\s*\n|^)\s*(?:\*\*)?"
    r"(?:Đối chiếu|Doi chieu|Kiểm tra lại|Kiem tra lai|Lưu ý|Luu y|Nguồn gốc|Nguon goc|Có thể nguồn gốc|Co the nguon goc)"
    r".*?(?=\n\s*\n|$)"
)


def escape(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def fold_text(value: Any) -> str:
    text = str(value or "").casefold()
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn").replace("đ", "d")


def strip_internal_solution_audit(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    previous = None
    while previous != text:
        previous = text
        text = INTERNAL_SOLUTION_AUDIT_RE.sub("\n\n", text)
    lines = []
    for line in text.splitlines():
        folded = fold_text(line)
        if any(
            marker in folded
            for marker in [
                "doi chieu dap an",
                "doi chieu nguon",
                "dap an pdf",
                "dap an goc",
                "nguon goc cho dap an",
                "nguon goc co the",
                "co the nguon goc",
                "trung voi ket qua tu giai",
                "trung khop",
                "source_answer",
            ]
        ):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def render_math_text(value: Any) -> str:
    if value is None:
        return ""
    text = strip_internal_solution_audit(value)
    text = text.replace("**", "")
    text = re.sub(r"(?<!\d)\*(?!\d)", "", text)

    def render_plain_segment(segment: str) -> str:
        rendered_lines: list[str] = []
        latex_command = re.compile(r"\\[A-Za-z]+")
        math_symbols = re.compile(r"(?:[A-Za-z][A-Za-z0-9_]*\s*=|[A-Za-z]\^\d|[A-Za-z]_\d|\\|√|⇒|⇔|≤|≥|∈|∞|\^|_\{|_\d|\bfrac\b)")
        prose_markers = re.compile(
            r"^(?:Bước|Kết luận|Kiểm tra|Áp dụng|Ta có|Vì|Do đó|Suy ra|Từ|Vậy|Lưu ý|Đáp án|Chọn|Xét)\b",
            re.IGNORECASE,
        )
        operator_chars = re.compile(r"[=+\-*/<>]")
        vietnamese_chars = re.compile(r"[À-ỹ]")

        def looks_like_math_line(line: str) -> bool:
            stripped = line.strip()
            if not stripped:
                return False
            words = re.findall(r"[A-Za-zÀ-ỹ]+", stripped)
            has_vietnamese_prose = bool(vietnamese_chars.search(stripped))
            is_short_math_like = len(words) <= 4
            if has_vietnamese_prose and not is_short_math_like:
                return False
            if prose_markers.search(stripped) and not latex_command.search(stripped):
                return False
            if latex_command.search(stripped):
                return True
            if math_symbols.search(stripped):
                return not has_vietnamese_prose or is_short_math_like
            if operator_chars.search(stripped) and re.search(r"[A-Za-z0-9][_^]?\d?|[A-Za-z]_[A-Za-z0-9]", stripped):
                # Avoid wrapping normal prose containing hyphens; require a compact equation-like line.
                return len(words) <= 8
            return False

        for line in segment.split("\n"):
            stripped = line.strip()
            if looks_like_math_line(stripped):
                rendered_lines.append(r"\(" + html.escape(stripped, quote=False) + r"\)")
            else:
                rendered_lines.append(escape(line))
        return "<br>".join(rendered_lines)

    token_pattern = re.compile(r"(\$\$.*?\$\$|\$.*?\$)", re.DOTALL)
    rendered: list[str] = []
    cursor = 0
    for match in token_pattern.finditer(text):
        if match.start() > cursor:
            rendered.append(render_plain_segment(text[cursor : match.start()]))
        token = match.group(0)
        if token.startswith("$$") and token.endswith("$$"):
            content = token[2:-2].strip()
            rendered.append(r"\[" + html.escape(content, quote=False) + r"\]")
        else:
            content = token[1:-1].strip()
            rendered.append(r"\(" + html.escape(content, quote=False) + r"\)")
        cursor = match.end()
    if cursor < len(text):
        rendered.append(render_plain_segment(text[cursor:]))
    output = "".join(rendered)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a standard exam ingest preview.")
    parser.add_argument("json_path", help="Path to a standard exam dry-run JSON file.")
    parser.add_argument("--output", help="Output HTML path. Defaults to JSON path with .html suffix.")
    return parser.parse_args()


def statement_answer(value: Any) -> str:
    if value is True:
        return "Đúng"
    if value is False:
        return "Sai"
    return "?"


def render_options(question: dict[str, Any]) -> str:
    if question.get("question_type") != "multiple_choice":
        return ""
    rows = []
    for letter in "abcd":
        rows.append(f"<li><strong>{letter.upper()}.</strong> {render_math_text(question.get(f'option_{letter}'))}</li>")
    return f"<ol class=\"options\">{''.join(rows)}</ol>"


def render_statements(question: dict[str, Any]) -> str:
    if question.get("question_type") != "true_false":
        return ""
    statements = question.get("statements")
    if not isinstance(statements, list):
        statements = []
    rows = []
    for index, statement in enumerate(statements, start=1):
        if not isinstance(statement, dict):
            continue
        label = statement.get("label") or chr(96 + index)
        rows.append(
            "<tr>"
            f"<th>{escape(label)}</th>"
            f"<td>{render_math_text(statement.get('text'))}</td>"
            f"<td>{escape(statement_answer(statement.get('answer')))}</td>"
            f"<td>{render_math_text(statement.get('explanation'))}</td>"
            "</tr>"
        )
    if not rows:
        return "<p class=\"warn-text\">No statements extracted.</p>"
    return (
        "<table class=\"tf-table\">"
        "<thead><tr><th>Ý</th><th>Mệnh đề</th><th>Đ/S</th><th>Giải thích</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_answer(question: dict[str, Any]) -> str:
    if question.get("question_type") == "true_false":
        statements = question.get("statements")
        if isinstance(statements, list) and statements:
            return "".join("D" if item.get("answer") is True else "S" if item.get("answer") is False else "?" for item in statements if isinstance(item, dict))
    answer = question.get("correct_answer")
    if answer in (None, ""):
        answer = question.get("numeric_answer")
    return escape(answer if answer not in (None, "") else "missing")


def render_solution_steps(question: dict[str, Any]) -> str:
    raw = question.get("raw_text") if isinstance(question.get("raw_text"), dict) else {}
    steps = raw.get("solution_steps")
    if not isinstance(steps, list) or not steps:
        return ""
    cards = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        title = step.get("title") or f"Bước {index}"
        content = step.get("content") or ""
        cards.append(
            "<div class=\"solution-step\">"
            f"<div class=\"step-index\">{index}</div>"
            "<div>"
            f"<h4>{escape(title)}</h4>"
            f"<div>{render_math_text(content)}</div>"
            "</div>"
            "</div>"
        )
    if not cards:
        return ""
    return "<div class=\"solution-steps\">" + "".join(cards) + "</div>"


def review_reason(question: dict[str, Any]) -> str:
    raw = question.get("raw_text") if isinstance(question.get("raw_text"), dict) else {}
    reason = raw.get("review_reason") or raw.get("taxonomy_review_reason")
    if reason:
        return str(reason)
    conflict = raw.get("answer_conflict")
    if isinstance(conflict, dict):
        return f"Đáp án trích xuất ban đầu: {conflict.get('before')}; đáp án sau kiểm tra: {conflict.get('after')}."
    return ""


def normalized_asset_name(value: str) -> str:
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d").replace("Đ", "D")
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower() or "exam"


def resolve_local_pdf_path(value: Any) -> Path:
    raw = str(value or "")
    path = Path(raw)
    if path.exists():
        return path
    normalized = raw.replace("\\", "/")
    marker = "Projects/pipeline/"
    if marker in normalized:
        candidate = Path(__file__).resolve().parent / normalized.split(marker, 1)[1]
        if candidate.exists():
            return candidate
    return path


def render_visual_crop(pdf_path: Path, question: dict[str, Any], image_dir: Path, index: int) -> str | None:
    bbox = question.get("visual_bbox")
    page_number = int(question.get("page_number") or 1)
    image_dir.mkdir(parents=True, exist_ok=True)
    output = image_dir / f"visual_{index:02d}_page_{page_number}.png"
    if output.exists():
        return str(output.resolve())
    if fitz is None or not bbox or len(bbox) != 4 or not pdf_path.exists():
        return None
    with fitz.open(pdf_path) as pdf:
        if page_number < 1 or page_number > pdf.page_count:
            return None
        page = pdf[page_number - 1]
        rect = page.rect
        x1, y1, x2, y2 = [float(item) for item in bbox]
        x1 = max(0.0, x1 - 0.08)
        y1 = max(0.0, y1 - 0.12)
        x2 = min(1.0, x2 + 0.08)
        y2 = min(1.0, y2 + 0.06)
        clip = fitz.Rect(
            rect.x0 + rect.width * x1,
            rect.y0 + rect.height * y1,
            rect.x0 + rect.width * x2,
            rect.y0 + rect.height * y2,
        )
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False, colorspace=fitz.csRGB, clip=clip)
        output.write_bytes(pix.tobytes("png"))
    return str(output.resolve())


def render_question_card(question: dict[str, Any], image_src: str | None) -> str:
    review_class = "warn" if question.get("needs_review") else "ok"
    reason = review_reason(question)
    internal_preview = os.getenv("STANDARD_EXAM_INTERNAL_PREVIEW", "0") == "1"
    badge = f'<span class="badge {review_class}">{escape(question.get("answer_source") or "missing")}</span>' if internal_preview else ""
    review_row = f'<div><strong>Review</strong><span>{escape(question.get("needs_review"))}</span></div>' if internal_preview else ""
    explanation_block = ""
    if question.get("explanation") and question.get("question_type") != "true_false":
        explanation_block = (
            "<details open><summary>Lời giải</summary>"
            f"{render_solution_steps(question)}"
            f"<div class=\"explanation\">{render_math_text(question.get('explanation'))}</div>"
            "</details>"
        )
    return f"""
    <article class="question-card">
      <header>
        <div>
          <div class="eyebrow">{escape(question.get("section_code"))} / Câu {escape(question.get("question_number"))} / trang {escape(question.get("page_number"))}</div>
          <h3>Câu {escape(question.get("question_number"))}</h3>
        </div>
        {badge}
      </header>
      {f'<div class="review-box"><strong>Cần kiểm tra</strong><span>{render_math_text(reason)}</span></div>' if question.get("needs_review") and reason else ""}
      <div class="question-text">{render_math_text(question.get("question_text"))}</div>
      {render_options(question)}
      {render_statements(question)}
      {f'<img class="visual" src="{escape(image_src)}" alt="visual crop">' if image_src else ""}
      <section class="meta-grid">
        <div><strong>Đáp án</strong><span>{render_answer(question)}</span></div>
        <div><strong>Điểm tối đa</strong><span>{escape(question.get("max_score"))}</span></div>
        <div><strong>Loại câu</strong><span>{escape(question.get("question_type"))}</span></div>
        {review_row}
        <div><strong>Chủ đề</strong><span>{escape(question.get("canonical_topic_title") or question.get("topic"))}</span></div>
        <div><strong>Dạng bài</strong><span>{escape(question.get("canonical_subtopic_title") or question.get("subtopic"))}</span></div>
        <div><strong>Mã dạng</strong><span>{escape(question.get("canonical_subtopic_code"))}</span></div>
        <div><strong>Độ khó</strong><span>{escape(question.get("difficulty"))}</span></div>
      </section>
      {explanation_block}
    </article>
    """


def render_section(section: dict[str, Any], questions: list[dict[str, Any]], images: dict[str, str]) -> str:
    cards = []
    for question in questions:
        cards.append(render_question_card(question, images.get(str(question.get("id")))))
    return f"""
    <section class="section-block">
      <h2>{escape(section.get("title"))}</h2>
      <div class="section-meta">
        <span>{escape(section.get("section_code"))}</span>
        <span>{escape(section.get("question_type"))}</span>
        <span>{escape(section.get("extracted_count"))}/{escape(section.get("expected_count"))} câu</span>
        <span>{escape(section.get("max_score"))} điểm</span>
      </div>
      {''.join(cards)}
    </section>
    """


def render_html(data: dict[str, Any], output: Path) -> None:
    exam = data.get("exam") or {}
    audit = data.get("audit") or {}
    sections = data.get("sections") if isinstance(data.get("sections"), list) else []
    questions = data.get("questions") if isinstance(data.get("questions"), list) else []
    pdf_path = resolve_local_pdf_path(exam.get("source_file"))
    image_dir = output.with_suffix("").with_name(output.stem + "_assets")
    images: dict[str, str] = {}
    for index, question in enumerate(questions, start=1):
        if question.get("needs_visual"):
            visual = render_visual_crop(pdf_path, question, image_dir, index)
            if visual:
                images[str(question.get("id"))] = os.path.relpath(visual, output.parent.resolve()).replace("\\", "/")

    by_section: dict[str, list[dict[str, Any]]] = {}
    for question in questions:
        by_section.setdefault(str(question.get("section_code") or ""), []).append(question)

    rendered_sections = []
    for section in sections:
        section_code = str(section.get("section_code") or "")
        rendered_sections.append(render_section(section, by_section.get(section_code, []), images))

    document = f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(exam.get("title") or "Standard Exam Preview")}</title>
  <script>
    window.MathJax = {{
      tex: {{ inlineMath: [['\\\\(', '\\\\)'], ['$', '$']], displayMath: [['\\\\[', '\\\\]'], ['$$', '$$']] }},
      svg: {{ fontCache: 'global' }}
    }};
  </script>
  <script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
  <style>
    :root {{
      --bg: #f5f7fb;
      --card: #fff;
      --text: #1b2533;
      --muted: #667085;
      --line: #d8dee8;
      --accent: #175cd3;
      --ok: #087443;
      --warn: #b54708;
      --warn-bg: #fff7ed;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      color: var(--text);
      background: var(--bg);
      line-height: 1.5;
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 56px; }}
    .hero {{ margin-bottom: 24px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    h2 {{ margin: 0; font-size: 22px; }}
    h3 {{ margin: 4px 0 0; font-size: 18px; }}
    .sub {{ color: var(--muted); }}
    .metrics, .section-meta, .meta-grid {{
      display: grid;
      gap: 10px;
    }}
    .metrics {{ grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); margin-top: 18px; }}
    .metric, .question-card, .section-block {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .metric {{ padding: 14px; }}
    .metric strong {{ display: block; font-size: 22px; }}
    .section-block {{ padding: 18px; margin-top: 18px; }}
    .section-meta {{ grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); margin: 10px 0 12px; color: var(--muted); }}
    .question-card {{ padding: 16px; margin-top: 12px; }}
    .question-card header {{ display: flex; justify-content: space-between; gap: 12px; align-items: start; }}
    .eyebrow {{ color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: .04em; }}
    .badge {{ border-radius: 999px; padding: 4px 10px; color: #fff; font-weight: 600; font-size: 12px; white-space: nowrap; }}
    .badge.ok {{ background: var(--ok); }}
    .badge.warn {{ background: var(--warn); }}
    .question-text {{ margin-top: 12px; white-space: normal; }}
    .review-box {{
      margin-top: 12px;
      padding: 10px 12px;
      border: 1px solid #fed7aa;
      border-left: 4px solid var(--warn);
      background: var(--warn-bg);
      border-radius: 6px;
    }}
    .review-box strong {{ display: block; color: var(--warn); margin-bottom: 4px; }}
    .options {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; padding-left: 0; list-style: none; }}
    .options li {{ border: 1px solid var(--line); border-radius: 6px; padding: 8px; background: #fbfcfe; }}
    .tf-table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    .tf-table th, .tf-table td {{ border: 1px solid var(--line); padding: 8px; vertical-align: top; }}
    .tf-table th {{ width: 48px; background: #f2f5f9; }}
    .visual {{ display: block; max-width: 100%; margin-top: 12px; border: 1px solid var(--line); border-radius: 6px; }}
    .meta-grid {{ grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin-top: 12px; }}
    .meta-grid div {{ border-top: 1px solid var(--line); padding-top: 8px; }}
    .meta-grid strong {{ display: block; color: var(--muted); font-size: 13px; }}
    details {{ margin-top: 12px; }}
    .explanation {{ margin-top: 8px; color: #344054; }}
    .solution-steps {{ display: grid; gap: 10px; margin-top: 10px; }}
    .solution-step {{
      display: grid;
      grid-template-columns: 30px 1fr;
      gap: 10px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
    }}
    .step-index {{
      width: 24px;
      height: 24px;
      border-radius: 999px;
      display: grid;
      place-items: center;
      background: #e8f0fe;
      color: #1d4ed8;
      font-weight: 700;
      font-size: 13px;
    }}
    .solution-step h4 {{ margin: 0 0 4px; font-size: 14px; color: #101828; }}
    .warn-text {{ color: var(--warn); }}
    pre {{ white-space: pre-wrap; }}
  </style>
</head>
<body>
<main>
  <section class="hero">
    <h1>{escape(exam.get("title"))}</h1>
    <div class="sub">Generated {escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))} / status {escape(audit.get("status"))}</div>
    <div class="metrics">
      <div class="metric"><strong>{escape(audit.get("extracted_question_count"))}/{escape(audit.get("expected_question_count"))}</strong><span>Câu lớn</span></div>
      <div class="metric"><strong>{escape(audit.get("extracted_item_count"))}/{escape(audit.get("expected_item_count"))}</strong><span>Lệnh hỏi</span></div>
      <div class="metric"><strong>{escape(audit.get("max_score"))}</strong><span>Điểm tối đa</span></div>
      <div class="metric"><strong>{escape(audit.get("needs_review"))}</strong><span>Cần review</span></div>
      <div class="metric"><strong>{escape(audit.get("ai_solved_answers"))}</strong><span>AI solved</span></div>
    </div>
  </section>
  {''.join(rendered_sections)}
  <section class="section-block">
    <h2>Raw audit</h2>
    <pre>{escape(json.dumps(audit, ensure_ascii=False, indent=2))}</pre>
  </section>
</main>
</body>
</html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")


def main() -> None:
    args = parse_args()
    json_path = Path(args.json_path)
    output = Path(args.output) if args.output else json_path.with_suffix(".html")
    render_html(json.loads(json_path.read_text(encoding="utf-8")), output)
    print(str(output.resolve()))


if __name__ == "__main__":
    main()
