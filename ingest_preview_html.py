import argparse
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz

SVG_STYLE = """
.svg-fill{fill:#fff}
.svg-line{fill:none;stroke:#111827;stroke-width:1.15;vector-effect:non-scaling-stroke}
.svg-line.strong{stroke-width:1.25}
.svg-trend{stroke:#111827;stroke-width:1.8;marker-end:url(#arrowhead);vector-effect:non-scaling-stroke}
.svg-arrow-head{fill:#111827}
.svg-math,.svg-label{fill:#111827;dominant-baseline:middle;text-anchor:middle;font-family:"Cambria Math",Cambria,"Times New Roman",serif;font-size:21px}
.svg-label{font-size:22px;font-style:italic;font-weight:600}
"""


def escape(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def render_options(item: dict[str, Any]) -> str:
    if item.get("question_type") != "multiple_choice":
        return ""
    return f"""
    <ol class="options" type="A">
      <li>{escape(item.get("option_a"))}</li>
      <li>{escape(item.get("option_b"))}</li>
      <li>{escape(item.get("option_c"))}</li>
      <li>{escape(item.get("option_d"))}</li>
    </ol>
    """


def parse_statements(item: dict[str, Any]) -> list[dict[str, Any]]:
    statements = item.get("statements")
    if isinstance(statements, str):
        try:
            statements = json.loads(statements)
        except json.JSONDecodeError:
            return []
    if not isinstance(statements, list):
        raw_item = raw_item_from_row(item)
        statements = raw_item.get("statements")
    if not isinstance(statements, list):
        return []
    return [stmt for stmt in statements if isinstance(stmt, dict)]


def render_true_false_statements(item: dict[str, Any]) -> str:
    if item.get("question_type") != "true_false":
        return ""
    statements = parse_statements(item)
    if not statements:
        return '<div class="muted">No statements extracted</div>'
    rows = []
    for index, stmt in enumerate(statements, start=1):
        label = stmt.get("label") or chr(96 + index)
        answer = stmt.get("answer")
        answer_text = "Đúng" if answer is True else "Sai" if answer is False else "?"
        rows.append(
            f"""
            <tr>
              <th>{escape(label)}</th>
              <td>{escape(stmt.get("text"))}</td>
              <td>{escape(answer_text)}</td>
            </tr>
            """
        )
    return f"""
    <table class="tf-table">
      <thead><tr><th>Ý</th><th>Mệnh đề</th><th>Đ/S</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def render_answer(item: dict[str, Any]) -> str:
    answer = item.get("correct_answer")
    if answer in (None, ""):
        answer = item.get("numeric_answer")
    if answer in (None, ""):
        return '<span class="muted">Chua co</span>'
    return escape(answer)


def is_markdown_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def parse_markdown_table(lines: list[str], start: int) -> tuple[str | None, int]:
    if start + 1 >= len(lines) or "|" not in lines[start] or not is_markdown_table_separator(lines[start + 1]):
        return None, start

    table_lines = [lines[start]]
    cursor = start + 2
    while cursor < len(lines) and "|" in lines[cursor] and lines[cursor].strip():
        table_lines.append(lines[cursor])
        cursor += 1

    rows = []
    for line in table_lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append(cells)

    max_cols = max((len(row) for row in rows), default=0)
    rendered_rows = []
    for row_index, row in enumerate(rows):
        tag = "th" if row_index == 0 else "td"
        cells = row + [""] * (max_cols - len(row))
        rendered_cells = "".join(f"<{tag}>{escape(cell)}</{tag}>" for cell in cells)
        rendered_rows.append(f"<tr>{rendered_cells}</tr>")

    return f'<div class="table-wrap"><table class="math-table">{"".join(rendered_rows)}</table></div>', cursor


def item_visual_table(item: dict[str, Any]) -> dict[str, Any] | None:
    raw_item = raw_item_from_row(item)
    table = raw_item.get("visual_table")
    if not isinstance(table, dict):
        table = item.get("visual_table")
    return table if isinstance(table, dict) else None


def render_question_content(text: Any, skip_tables: bool = False) -> str:
    if text is None:
        return ""
    lines = str(text).splitlines()
    output: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            output.append(f'<p>{escape(" ".join(part.strip() for part in paragraph if part.strip()))}</p>')
            paragraph.clear()

    cursor = 0
    while cursor < len(lines):
        table_html, next_cursor = parse_markdown_table(lines, cursor)
        if table_html:
            flush_paragraph()
            if not skip_tables:
                output.append(table_html)
            cursor = next_cursor
            continue
        if not lines[cursor].strip():
            flush_paragraph()
        else:
            paragraph.append(lines[cursor])
        cursor += 1

    flush_paragraph()
    return "\n".join(output)


def raw_item_from_row(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("raw_text")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def render_visual_table(item: dict[str, Any]) -> str:
    table = item_visual_table(item)
    if not isinstance(table, dict):
        return ""
    rows = table.get("rows")
    if not isinstance(rows, list) or not rows:
        return ""

    kind = str(table.get("kind") or "table")
    if kind in {"sign_table", "variation_table"}:
        return render_calculus_table(table)

    rendered_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = row.get("label") or ""
        cells = row.get("cells")
        if not isinstance(cells, list):
            continue
        rendered_cells = "".join(f"<td>{escape(cell)}</td>" for cell in cells)
        rendered_rows.append(f"<tr><th>{escape(label)}</th>{rendered_cells}</tr>")

    if not rendered_rows:
        return ""
    kind = escape(kind)
    return f"""
      <div class="clean-visual">
        <div class="clean-visual-label">Rendered {kind}</div>
        <div class="table-wrap"><table class="math-table visual-table">{"".join(rendered_rows)}</table></div>
      </div>
    """


def render_visual_table_asset(item: dict[str, Any], image_dir: Path, index: int) -> str | None:
    table = item_visual_table(item)
    if not isinstance(table, dict):
        return None
    kind = str(table.get("kind") or "table")
    if kind == "sign_table":
        svg = render_sign_table_svg(table)
    elif kind == "variation_table":
        svg = render_variation_table_svg(table)
    else:
        svg = ""
    if not svg:
        return None
    svg = svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ')
    first_close = svg.find(">")
    svg = svg[: first_close + 1] + f"<style>{SVG_STYLE}</style>" + svg[first_close + 1 :]
    image_dir.mkdir(parents=True, exist_ok=True)
    output = image_dir / f"rendered_table_{index:02d}_{kind}.svg"
    output.write_text(svg, encoding="utf-8")
    return output.as_posix()


def render_calculus_cell(value: Any) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if lowered in {"up", "up to", "increase", "increasing", "↗"}:
        return '<span class="trend up">↗</span>'
    if lowered in {"down", "down to", "decrease", "decreasing", "↘"}:
        return '<span class="trend down">↘</span>'
    return escape(text)


def math_text(value: Any) -> str:
    text = str(value or "").strip().strip("$")
    replacements = {
        "\\infty": "∞",
        "-\\infty": "-∞",
        "+\\infty": "+∞",
        "\\prime": "'",
        "f'(x)": "f′(x)",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.replace("-", "−")
    return text


def svg_text(x: float, y: float, value: Any, class_name: str = "svg-math") -> str:
    return f'<text class="{class_name}" x="{x:g}" y="{y:g}">{escape(math_text(value))}</text>'


def svg_line(x1: float, y1: float, x2: float, y2: float, class_name: str = "svg-line") -> str:
    return f'<line class="{class_name}" x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}"/>'


def render_sign_table_svg(table: dict[str, Any]) -> str:
    rows = table.get("rows", [])
    x_row = next((row for row in rows if str(row.get("label", "")).strip("$") == "x"), None)
    sign_row = next((row for row in rows if "f'" in str(row.get("label", "")) or "f′" in str(row.get("label", ""))), None)
    if not x_row or not sign_row:
        return ""

    x_cells = list(x_row.get("cells", []))
    signs = list(sign_row.get("cells", []))
    domain_w = 500
    label_w = 72
    row_h = 36
    width = label_w + domain_w
    height = row_h * 2
    parts = [
        f'<svg class="calc-svg sign-svg" viewBox="0 0 {width:g} {height:g}" role="img">',
        '<rect class="svg-fill" x="0" y="0" width="100%" height="100%"/>',
        f'<rect class="svg-line" x="0" y="0" width="{width:g}" height="{height:g}"/>',
        svg_line(0, row_h, width, row_h),
        svg_line(label_w, 0, label_w, height),
    ]
    parts.append(svg_text(label_w / 2, 24, x_row.get("label"), "svg-label"))
    parts.append(svg_text(label_w / 2, row_h + 24, sign_row.get("label"), "svg-label"))

    if len(x_cells) == 1:
        x_positions = [label_w + domain_w / 2]
    else:
        x_positions = [
            label_w + domain_w * (0.08 + 0.84 * index / (len(x_cells) - 1))
            for index in range(len(x_cells))
        ]
    for x, cell in zip(x_positions, x_cells):
        parts.append(svg_text(x, 24, cell))

    if len(signs) == 2 * len(x_cells) - 3 and len(x_positions) >= 2:
        sign_positions: list[float] = []
        for index in range(len(x_positions) - 1):
            sign_positions.append((x_positions[index] + x_positions[index + 1]) / 2)
            if index < len(x_positions) - 2:
                sign_positions.append(x_positions[index + 1])
    elif len(signs) == 2 * len(x_cells) - 1 and len(x_positions) >= 2:
        sign_positions = []
        for index in range(len(x_positions) - 1):
            sign_positions.append((x_positions[index] + x_positions[index + 1]) / 2)
            sign_positions.append(x_positions[index + 1])
        sign_positions = sign_positions[: len(signs)]
    else:
        sign_positions = [
            label_w + domain_w * (0.08 + 0.84 * index / max(1, len(signs) - 1))
            for index in range(len(signs))
        ]
    for x, sign in zip(sign_positions, signs):
        parts.append(svg_text(x, row_h + 24, sign))
    parts.append("</svg>")
    return "".join(parts)


def render_variation_table_svg(table: dict[str, Any]) -> str:
    rows = table.get("rows", [])
    x_row = next((row for row in rows if str(row.get("label", "")).strip("$") == "x"), None)
    sign_row = next((row for row in rows if "f'" in str(row.get("label", "")) or "f′" in str(row.get("label", ""))), None)
    value_row = next((row for row in rows if str(row.get("label", "")).replace("'", "").strip("$") == "f(x)"), None)
    if not x_row or not sign_row or not value_row:
        return ""

    x_cells = list(x_row.get("cells", []))
    signs = list(sign_row.get("cells", []))
    values = list(value_row.get("cells", []))
    if len(x_cells) != 4:
        return render_generic_variation_table_svg(x_row, sign_row, value_row)

    label_w = 52
    width = 470
    h1 = 32
    h2 = 32
    height = 153
    y2 = h1 + h2
    x_left = label_w
    x0 = 86
    x_asym1 = 212
    x_asym2 = 342
    x_right = width - 30
    x_positions = [x0, x_asym1, x_asym2, x_right]
    sign_positions = [(x0 + x_asym1) / 2, (x_asym1 + x_asym2) / 2, (x_asym2 + x_right) / 2]
    parts = [
        f'<svg class="calc-svg variation-svg" viewBox="0 0 {width:g} {height:g}" role="img">',
        '<defs><marker id="arrowhead" markerWidth="8" markerHeight="8" refX="6" refY="3.5" orient="auto"><polygon points="0 0, 7 3.5, 0 7" class="svg-arrow-head"/></marker></defs>',
        '<rect class="svg-fill" x="0" y="0" width="100%" height="100%"/>',
        f'<rect class="svg-line" x="0" y="0" width="{width:g}" height="{height:g}"/>',
        svg_line(0, h1, width, h1),
        svg_line(0, y2, width, y2),
        svg_line(label_w, 0, label_w, height),
    ]
    for x in (x_asym1, x_asym2):
        parts.append(svg_line(x, 0, x, h1))
        parts.append(svg_line(x - 3, h1, x - 3, height, "svg-line strong"))
        parts.append(svg_line(x + 3, h1, x + 3, height, "svg-line strong"))

    parts.append(svg_text(label_w / 2, 23, x_row.get("label"), "svg-label"))
    parts.append(svg_text(label_w / 2, h1 + 23, sign_row.get("label"), "svg-label"))
    parts.append(svg_text(label_w / 2, y2 + 50, value_row.get("label"), "svg-label"))

    for x, cell in zip(x_positions, x_cells):
        parts.append(svg_text(x, 22, cell))
    visible_signs = [sign for sign in signs if "||" not in str(sign)]
    for x, sign in zip(sign_positions, visible_signs):
        parts.append(svg_text(x, h1 + 22, sign))

    value_items = [cell for cell in values if str(cell).lower() not in {"down to", "down", "up to", "up"}]
    if len(value_items) >= 5:
        parts.append(svg_text(x_left + 24, y2 + 20, value_items[0]))
        parts.append(svg_text(x_asym1 - 28, height - 14, value_items[1]))
        parts.append(svg_text(x_asym1 + 32, y2 + 20, value_items[2]))
        parts.append(svg_text(x_asym2 - 28, height - 14, value_items[3]))
        parts.append(svg_text(x_right - 2, y2 + 20, value_items[4]))

    parts.append(svg_line(x_left + 42, y2 + 24, x_asym1 - 42, height - 28, "svg-trend"))
    parts.append(svg_line(x_asym1 + 46, y2 + 24, x_asym2 - 42, height - 28, "svg-trend"))
    parts.append(svg_line(x_asym2 + 42, height - 28, x_right - 42, y2 + 24, "svg-trend"))
    parts.append("</svg>")
    return "".join(parts)


def render_generic_variation_table_svg(x_row: dict[str, Any], sign_row: dict[str, Any], value_row: dict[str, Any]) -> str:
    return ""


def calculus_cell_class(value: Any) -> str:
    text = str(value or "").strip().lower()
    classes = []
    if text in {"0", "+0", "-0"}:
        classes.append("zero")
    if "||" in text or "asymptote" in text:
        classes.append("asymptote")
    return f' class="{" ".join(classes)}"' if classes else ""


def render_calculus_table(table: dict[str, Any]) -> str:
    rows = [row for row in table.get("rows", []) if isinstance(row, dict) and isinstance(row.get("cells"), list)]
    if not rows:
        return ""
    kind = str(table.get("kind") or "table")
    if kind == "sign_table":
        svg = render_sign_table_svg(table)
        return f'<div class="clean-visual clean-svg">{svg}</div>' if svg else ""
    if kind == "variation_table":
        svg = render_variation_table_svg(table)
        return f'<div class="clean-visual clean-svg">{svg}</div>' if svg else ""

    rendered_rows = []
    for row in rows:
        label = row.get("label") or ""
        cells = [cell for cell in row.get("cells", [])]
        rendered_cells = "".join(f"<td{calculus_cell_class(cell)}>{render_calculus_cell(cell)}</td>" for cell in cells)
        rendered_rows.append(f"<tr><th>{escape(label)}</th>{rendered_cells}</tr>")

    kind = escape(kind)
    return f"""
      <div class="clean-visual">
        <div class="clean-visual-label">Rendered {kind}</div>
        <div class="table-wrap"><table class="calculus-table {kind}">{"".join(rendered_rows)}</table></div>
      </div>
    """


def find_pdf_path(filename: str) -> Path | None:
    candidates = [Path(filename), Path("D:/Database/MAPSTUDY") / filename]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    for candidate in Path("D:/Database").rglob(filename):
        return candidate
    return None


def render_visual_crop(pdf_path: Path, item: dict[str, Any], image_dir: Path, index: int) -> str | None:
    raw_item = raw_item_from_row(item)
    bbox = raw_item.get("visual_bbox")
    page_number = int(item.get("page_number") or 1)
    image_dir.mkdir(parents=True, exist_ok=True)
    suffix = "crop" if bbox and len(bbox) == 4 else "page"
    output = image_dir / f"visual_{index:02d}_page_{page_number}_{suffix}.png"
    with fitz.open(pdf_path) as pdf:
        page = pdf[page_number - 1]
        rect = page.rect
        if not bbox or len(bbox) != 4:
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False, colorspace=fitz.csRGB)
            output.write_bytes(pix.tobytes("png"))
            return output.as_posix()
        x1, y1, x2, y2 = bbox
        # Expand upward/sideways so the crop includes nearby question context, not just the plot.
        x1 = max(0.0, x1 - 0.08)
        y1 = max(0.0, y1 - 0.12)
        x2 = min(1.0, x2 + 0.08)
        y2 = min(1.0, y2 + 0.05)
        clip = fitz.Rect(
            rect.x0 + rect.width * x1,
            rect.y0 + rect.height * y1,
            rect.x0 + rect.width * x2,
            rect.y0 + rect.height * y2,
        )
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False, colorspace=fitz.csRGB, clip=clip)
        output.write_bytes(pix.tobytes("png"))
    return output.as_posix()


def render_card(
    item: dict[str, Any],
    index: int,
    visual_src: str | None = None,
    rendered_table_src: str | None = None,
) -> str:
    needs_visual = bool(item.get("needs_visual"))
    visual_label = f"{escape(item.get('visual_type'))}" if needs_visual else "none"
    page = item.get("page_number")
    source = item.get("source") or ""
    return f"""
    <article class="card {'visual' if needs_visual else ''}">
      <header>
        <div>
          <div class="eyebrow">#{index} · {escape(source)} · page {escape(page)} · {escape(item.get("question_type"))}</div>
          <h2>{escape(item.get("source_hint") or item.get("id") or "Question")}</h2>
        </div>
        <span class="badge {'warn' if needs_visual else 'ok'}">{'Needs visual' if needs_visual else 'Text only'}</span>
      </header>
      <section>
        <h3>De bai</h3>
        <div class="question">{render_question_content(item.get("question_text"), skip_tables=item_visual_table(item) is not None)}</div>
        {render_options(item)}
        {render_true_false_statements(item)}
        {f'<img class="rendered-table-img" src="{escape(rendered_table_src)}" alt="rendered math table">' if rendered_table_src else render_visual_table(item)}
        {f'<details class="source-visual"><summary>Source image check</summary><img class="visual-img" src="{escape(visual_src)}" alt="visual crop"></details>' if visual_src and item_visual_table(item) else ''}
        {f'<img class="visual-img" src="{escape(visual_src)}" alt="visual crop">' if visual_src and not item_visual_table(item) else ''}
      </section>
      <section class="grid">
        <div>
          <h3>Dap an ingest</h3>
          <div class="answer">{render_answer(item)}</div>
        </div>
        <div>
          <h3>Metadata</h3>
          <div class="meta-line">Topic: {escape(item.get("topic"))}</div>
          <div class="meta-line">Difficulty: {escape(item.get("difficulty"))}</div>
          <div class="meta-line">Visual: {visual_label}</div>
          <div class="meta-line">File: {escape(item.get("source_file"))}</div>
        </div>
      </section>
      <details>
        <summary>Raw row</summary>
        <pre>{escape(json.dumps(item, ensure_ascii=False, indent=2))}</pre>
      </details>
    </article>
    """


def render_html(data: dict[str, Any], output: Path) -> None:
    questions = data.get("questions", [])
    filename = data.get("filename", "")
    pdf_path = find_pdf_path(filename)
    image_dir = output.with_suffix("").with_name(output.stem + "_assets")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    visual_count = sum(1 for item in questions if item.get("needs_visual"))
    rendered_cards: list[str] = []
    for idx, item in enumerate(questions, start=1):
        visual_src = None
        rendered_table_src = render_visual_table_asset(item, image_dir, idx) if item_visual_table(item) else None
        if pdf_path and item.get("needs_visual"):
            visual_src = render_visual_crop(pdf_path, item, image_dir, idx)
        elif item.get("source_path") and item.get("needs_visual") and Path(item["source_path"]).exists():
            visual_src = render_visual_crop(Path(item["source_path"]), item, image_dir, idx)
        rendered_cards.append(render_card(item, idx, visual_src, rendered_table_src))
    cards = "\n".join(rendered_cards)
    document = f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ingest Preview</title>
  <script>
    window.MathJax = {{ tex: {{ inlineMath: [['$', '$'], ['\\\\(', '\\\\)']] }} }};
  </script>
  <script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
  <style>
    :root {{
      --bg: #f4f6f8;
      --text: #182230;
      --muted: #667085;
      --line: #d0d5dd;
      --card: #fff;
      --accent: #1769aa;
      --ok: #157347;
      --warn: #b54708;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Segoe UI, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.55;
    }}
    .wrap {{ max-width: 1160px; margin: 0 auto; padding: 28px 20px 56px; }}
    .hero {{
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: flex-end;
      border-bottom: 1px solid var(--line);
      padding-bottom: 18px;
      margin-bottom: 18px;
    }}
    h1 {{ margin: 0 0 6px; font-size: 30px; }}
    .sub {{ color: var(--muted); }}
    .stats {{ display: grid; grid-template-columns: repeat(2, minmax(120px, 1fr)); gap: 8px; min-width: 260px; }}
    .stat {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; }}
    .stat strong {{ display: block; font-size: 24px; }}
    .stat span {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-left: 5px solid var(--accent);
      border-radius: 8px;
      margin: 14px 0;
      padding: 18px;
    }}
    .card.visual {{ border-left-color: var(--warn); }}
    header {{ display: flex; justify-content: space-between; gap: 16px; }}
    .eyebrow {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
    h2 {{ margin: 2px 0 0; font-size: 17px; overflow-wrap: anywhere; }}
    h3 {{ margin: 18px 0 8px; color: var(--muted); text-transform: uppercase; font-size: 13px; }}
    .question {{ font-size: 17px; }}
    .question p {{ margin: 0 0 10px; }}
    .table-wrap {{ overflow-x: auto; margin: 12px 0 14px; }}
    .math-table {{
      border-collapse: collapse;
      min-width: 520px;
      background: white;
      font-size: 16px;
    }}
    .math-table th,
    .math-table td {{
      border: 1px solid #344054;
      padding: 8px 12px;
      text-align: center;
      min-width: 62px;
      height: 38px;
    }}
    .math-table th:first-child,
    .math-table td:first-child {{
      font-weight: 700;
      background: #f8fafc;
      min-width: 72px;
    }}
    .tf-table {{
      width: 100%;
      border-collapse: collapse;
      margin: 12px 0 8px;
      background: #fff;
      font-size: 15px;
    }}
    .tf-table th,
    .tf-table td {{
      border: 1px solid var(--line);
      padding: 8px 10px;
      vertical-align: top;
    }}
    .tf-table thead th {{
      background: #f8fafc;
      color: var(--muted);
      text-transform: uppercase;
      font-size: 12px;
    }}
    .tf-table tbody th {{
      width: 44px;
      text-align: center;
    }}
    .tf-table td:last-child {{
      width: 64px;
      text-align: center;
      font-weight: 700;
      color: var(--accent);
    }}
    .clean-visual {{
      margin-top: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfcfe;
    }}
    .clean-svg {{
      width: fit-content;
      max-width: 100%;
      background: white;
      padding: 0;
      border: 0;
      border-radius: 0;
      overflow-x: auto;
    }}
    .calc-svg {{
      display: block;
      max-width: 100%;
      height: auto;
      background: white;
      color: #111827;
      font-family: "Cambria Math", Cambria, "Times New Roman", serif;
    }}
    .variation-svg {{
      width: 472px;
    }}
    .sign-svg {{
      width: 572px;
    }}
    .svg-fill {{
      fill: #fff;
    }}
    .svg-line {{
      fill: none;
      stroke: #111827;
      stroke-width: 1.2;
      vector-effect: non-scaling-stroke;
    }}
    .svg-line.light {{
      stroke-width: 0.9;
    }}
    .svg-line.strong {{
      stroke-width: 1.35;
    }}
    .svg-trend {{
      stroke: #111827;
      stroke-width: 1.9;
      marker-end: url(#arrowhead);
      vector-effect: non-scaling-stroke;
    }}
    .svg-arrow-head {{
      fill: #111827;
    }}
    .svg-math,
    .svg-label {{
      fill: #111827;
      dominant-baseline: middle;
      text-anchor: middle;
      font-size: 21px;
    }}
    .svg-label {{
      font-size: 22px;
      font-style: italic;
      font-weight: 600;
    }}
    .source-visual {{
      margin-top: 12px;
    }}
    .source-visual .visual-img {{
      max-width: 720px;
    }}
    .rendered-table-img {{
      display: block;
      max-width: 720px;
      width: auto;
      margin: 14px 0 0;
      background: white;
    }}
    .clean-visual-label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      margin-bottom: 6px;
    }}
    .visual-table {{
      min-width: 620px;
    }}
    .calculus-table {{
      border-collapse: collapse;
      background: white;
      color: #101828;
      font-family: "Cambria Math", Cambria, "Times New Roman", serif;
      font-size: 22px;
      min-width: 560px;
      table-layout: fixed;
    }}
    .calculus-table th,
    .calculus-table td {{
      border: 1.5px solid #101828;
      min-width: 72px;
      height: 46px;
      padding: 4px 12px;
      text-align: center;
      vertical-align: middle;
      font-style: italic;
      background: white;
    }}
    .calculus-table th {{
      width: 72px;
      font-weight: 700;
    }}
    .calculus-table td {{
      font-style: normal;
    }}
    .calculus-table tr:first-child td,
    .calculus-table tr:first-child th {{
      font-style: italic;
    }}
    .variation_table {{
      min-width: 620px;
    }}
    .variation_table tr:nth-child(2) td.zero,
    .variation_table tr:nth-child(2) td.asymptote {{
      border-left-width: 4px;
      border-right-width: 4px;
    }}
    .trend {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 34px;
      line-height: 1;
      min-width: 44px;
      color: #101828;
    }}
    .visual-img {{ display: block; max-width: 720px; width: 100%; margin-top: 14px; border: 1px solid var(--line); border-radius: 8px; background: white; }}
    .options {{ margin: 10px 0 0 22px; padding: 0; }}
    .options li {{ padding: 3px 0 3px 4px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .answer {{ font-weight: 800; font-size: 22px; color: var(--accent); }}
    .badge {{ border-radius: 999px; padding: 6px 10px; color: white; font-size: 12px; font-weight: 700; white-space: nowrap; }}
    .badge.ok {{ background: var(--ok); }}
    .badge.warn {{ background: var(--warn); }}
    .muted, .meta-line {{ color: var(--muted); }}
    summary {{ margin-top: 16px; cursor: pointer; color: var(--accent); font-weight: 700; }}
    pre {{ white-space: pre-wrap; background: #eef2f6; border: 1px solid var(--line); border-radius: 8px; padding: 12px; overflow: auto; }}
    @media (max-width: 760px) {{
      .hero {{ display: block; }}
      .stats {{ min-width: 0; margin-top: 14px; }}
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div>
        <h1>Ingest Preview</h1>
        <div class="sub">{escape(filename)}</div>
        <div class="sub">Generated {escape(generated_at)} · source {escape(data.get("source"))}</div>
      </div>
      <div class="stats">
        <div class="stat"><strong>{len(questions)}</strong><span>Questions</span></div>
        <div class="stat"><strong>{visual_count}</strong><span>Need Visual</span></div>
      </div>
    </section>
    {cards}
  </main>
</body>
</html>
"""
    output.write_text(document, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output or input_path.with_suffix(".html"))
    data = json.loads(input_path.read_text(encoding="utf-8"))
    render_html(data, output_path)
    print(output_path.resolve())


if __name__ == "__main__":
    main()
