import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
from supabase import create_client

from solve_answers import (
    CLAUDE_API_KEY,
    CLAUDE_BASE_URL,
    MODEL_NAME,
    SUPABASE_KEY,
    SUPABASE_URL,
    build_update,
    build_user_content,
    fetch_questions,
    has_answerable_content,
    normalize_anthropic_base_url,
    solve_with_claude,
)


def escape(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def render_json(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return escape(value)
    return escape(json.dumps(value, ensure_ascii=False, indent=2))


def render_question_block(question: dict[str, Any]) -> str:
    q_type = question.get("question_type", "")
    options = ""
    if q_type == "multiple_choice":
        options = f"""
        <ol class="options" type="A">
          <li>{escape(question.get("option_a"))}</li>
          <li>{escape(question.get("option_b"))}</li>
          <li>{escape(question.get("option_c"))}</li>
          <li>{escape(question.get("option_d"))}</li>
        </ol>
        """
    elif q_type == "true_false":
        statements = render_json(question.get("statements"))
        options = f'<pre class="raw">{statements}</pre>' if statements else ""

    return f"""
      <div class="question-text">{escape(question.get("question_text"))}</div>
      {options}
    """


def status_badge(item: dict[str, Any]) -> str:
    status = item["status"]
    label = {
        "solved": "Solved",
        "review": "Needs review",
        "skipped": "Skipped",
        "failed": "Failed",
    }.get(status, status)
    return f'<span class="badge {escape(status)}">{escape(label)}</span>'


def render_item(item: dict[str, Any], index: int) -> str:
    question = item["question"]
    update = item.get("update") or {}
    result = item.get("result") or {}
    error = item.get("error")
    answer = (
        update.get("correct_answer")
        or update.get("numeric_answer")
        or result.get("ans")
        or ""
    )
    explanation = update.get("explanation") or result.get("exp") or ""
    prompt = item.get("prompt") or ""

    return f"""
    <article class="card {escape(item['status'])}">
      <header>
        <div>
          <div class="eyebrow">#{index} · {escape(question.get("question_type"))}</div>
          <h2>{escape(question.get("id"))}</h2>
        </div>
        {status_badge(item)}
      </header>

      <section>
        <h3>De bai</h3>
        {render_question_block(question)}
      </section>

      <section class="answer-grid">
        <div>
          <h3>Dap an</h3>
          <div class="answer">{escape(answer)}</div>
        </div>
        <div>
          <h3>Nguon</h3>
          <div class="muted">{escape(update.get("answer_source") or "")}</div>
        </div>
      </section>

      <section>
        <h3>Loi giai</h3>
        <div class="explanation">{escape(explanation) or '<span class="muted">Khong co loi giai.</span>'}</div>
      </section>

      {f'<section><h3>Loi</h3><pre class="raw error-text">{escape(error)}</pre></section>' if error else ''}

      <details>
        <summary>Prompt gui model</summary>
        <pre class="raw">{escape(prompt)}</pre>
      </details>
    </article>
    """


def render_html(items: list[dict[str, Any]], output_path: Path, model: str, base_url: str) -> None:
    solved = sum(1 for item in items if item["status"] == "solved")
    review = sum(1 for item in items if item["status"] == "review")
    skipped = sum(1 for item in items if item["status"] == "skipped")
    failed = sum(1 for item in items if item["status"] == "failed")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cards = "\n".join(render_item(item, idx) for idx, item in enumerate(items, start=1))
    document = f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Solve Preview</title>
  <script>
    window.MathJax = {{ tex: {{ inlineMath: [['$', '$'], ['\\\\(', '\\\\)']] }} }};
  </script>
  <script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --text: #172033;
      --muted: #637083;
      --line: #d9dee7;
      --card: #ffffff;
      --ok: #0f7b4f;
      --warn: #9a6200;
      --bad: #b3261e;
      --skip: #667085;
      --accent: #2457c5;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.55;
    }}
    .wrap {{ max-width: 1120px; margin: 0 auto; padding: 28px 20px 56px; }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: flex-end;
      border-bottom: 1px solid var(--line);
      padding-bottom: 18px;
      margin-bottom: 20px;
    }}
    h1 {{ margin: 0 0 6px; font-size: 30px; letter-spacing: 0; }}
    .meta {{ color: var(--muted); font-size: 14px; }}
    .stats {{ display: grid; grid-template-columns: repeat(4, minmax(90px, 1fr)); gap: 8px; min-width: 420px; }}
    .stat {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; }}
    .stat strong {{ display: block; font-size: 22px; }}
    .stat span {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-left: 5px solid var(--accent);
      border-radius: 8px;
      padding: 18px;
      margin: 14px 0;
    }}
    .card.skipped {{ border-left-color: var(--skip); }}
    .card.failed {{ border-left-color: var(--bad); }}
    .card.review {{ border-left-color: var(--warn); }}
    .card header {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }}
    .eyebrow {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
    h2 {{ margin: 2px 0 0; font-size: 16px; overflow-wrap: anywhere; }}
    h3 {{ margin: 18px 0 8px; font-size: 13px; text-transform: uppercase; color: var(--muted); }}
    .question-text, .explanation {{ font-size: 16px; }}
    .options {{ margin: 10px 0 0 22px; padding: 0; }}
    .options li {{ padding: 3px 0 3px 4px; }}
    .answer-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .answer {{ font-size: 22px; font-weight: 700; color: var(--accent); }}
    .muted {{ color: var(--muted); }}
    .badge {{ border-radius: 999px; padding: 6px 10px; color: white; font-size: 12px; font-weight: 700; white-space: nowrap; }}
    .badge.solved {{ background: var(--ok); }}
    .badge.review {{ background: var(--warn); }}
    .badge.skipped {{ background: var(--skip); }}
    .badge.failed {{ background: var(--bad); }}
    details {{ margin-top: 16px; }}
    summary {{ cursor: pointer; color: var(--accent); font-weight: 600; }}
    .raw {{ white-space: pre-wrap; background: #f0f3f8; border: 1px solid var(--line); border-radius: 8px; padding: 12px; overflow: auto; }}
    .error-text {{ color: var(--bad); }}
    @media (max-width: 760px) {{
      .topbar {{ display: block; }}
      .stats {{ min-width: 0; grid-template-columns: repeat(2, 1fr); margin-top: 14px; }}
      .answer-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <div class="topbar">
      <div>
        <h1>Solve Preview</h1>
        <div class="meta">Generated {escape(generated_at)} · model {escape(model)} · endpoint {escape(base_url)}</div>
        <div class="meta">File: {escape(output_path.name)}</div>
      </div>
      <div class="stats">
        <div class="stat"><strong>{solved}</strong><span>Solved</span></div>
        <div class="stat"><strong>{review}</strong><span>Review</span></div>
        <div class="stat"><strong>{skipped}</strong><span>Skipped</span></div>
        <div class="stat"><strong>{failed}</strong><span>Failed</span></div>
      </div>
    </div>
    {cards}
  </main>
</body>
</html>
"""
    output_path.write_text(document, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an HTML preview for solve_answers dry-run results.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--include-visual", action="store_true")
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_url = normalize_anthropic_base_url(CLAUDE_BASE_URL)
    output_path = Path(args.output or f"artifacts/previews/solve_preview_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY or "local-9router", base_url=base_url)
    questions = fetch_questions(supabase, args.limit, args.include_visual)

    items: list[dict[str, Any]] = []
    for question in questions:
        item: dict[str, Any] = {
            "question": question,
            "prompt": build_user_content(question) if has_answerable_content(question) else "",
        }
        if not has_answerable_content(question):
            item["status"] = "skipped"
            item["error"] = "Thieu noi dung de bai/options trong question_text va raw_text."
            items.append(item)
            continue

        result = solve_with_claude(client, question)
        if not result:
            item["status"] = "failed"
            item["error"] = "Model call failed or returned no result."
            items.append(item)
            continue

        update = build_update(question, result)
        item["result"] = result
        item["update"] = update
        item["status"] = "review" if update and update.get("needs_review") else "solved"
        items.append(item)

    render_html(items, output_path, MODEL_NAME, base_url)
    print(output_path.resolve())


if __name__ == "__main__":
    main()
