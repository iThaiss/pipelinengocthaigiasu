import argparse
import html
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import fitz  # PyMuPDF

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

DATABASE_FOLDER = os.getenv("DATABASE_FOLDER", r"D:\Database")
WORKDIR = Path(__file__).resolve().parent
MINERU_EXE = Path(
    os.getenv(
        "MINERU_EXE",
        r"C:\Users\ITHAISS\AppData\Local\Programs\Python\Python312\Scripts\mineru.exe",
    )
)
MINERU_CONFIG = Path(os.getenv("MINERU_TOOLS_CONFIG_JSON", str(WORKDIR / "mineru_local.json")))
MINERU_OUTPUT_ROOT = Path(os.getenv("MINERU_PREVIEW_WORK", str(WORKDIR / "artifacts" / "runs" / "mineru_preview_runs")))
DEFAULT_OUTPUT_HTML = os.getenv("MINERU_PREVIEW_HTML", "artifacts/previews/mineru_preview_10_questions.html")
DEFAULT_OUTPUT_JSON = os.getenv("MINERU_PREVIEW_JSON", "artifacts/previews/mineru_preview_10_questions.json")
DEFAULT_LIMIT = int(os.getenv("MINERU_PREVIEW_LIMIT", "10"))
DEFAULT_METHOD = os.getenv("MINERU_PREVIEW_METHOD", "txt")
LOG_FILE = os.getenv("MINERU_PREVIEW_LOG_FILE", "logs/mineru_preview.log")
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

QUESTION_SPLIT_PATTERN = re.compile(r"(?=\[IT\d{6,}\])", re.MULTILINE)
QUESTION_CODE_PATTERN = re.compile(r"^\[IT(?P<code>\d{6,})\]\s*", re.MULTILINE)
TAG_PATTERN = re.compile(r"<[^>]+>")
INTERVAL_STACK_PATTERN = re.compile(
    r"\)\s*\n\s*([0-9]+(?:[.,][0-9]+)?\s*;\s*[0-9]+(?:[.,][0-9]+)?)\s*\n\s*[\[]\s*\n\s*[\]]",
    re.MULTILINE,
)
NOISE_LINE_PATTERNS = [
    re.compile(r"^Trang\s+\d+\s*$", re.IGNORECASE),
    re.compile(r"^Shared By Fanpage:.*$", re.IGNORECASE),
    re.compile(r"^Đăng Ký Khóa Học Online.*$", re.IGNORECASE),
    re.compile(r"^IT\d{5,}\s*$"),
    re.compile(r"^KHOẢNG BIẾN THIÊN.*$", re.IGNORECASE),
    re.compile(r"^VỊ CỦA MẪU SỐ LIỆU GHÉP NHÓM\s*$", re.IGNORECASE),
    re.compile(r"^MẪU SỐ LIỆU GHÉP NHÓM\s*$", re.IGNORECASE),
]
SYMBOL_FIXES = {
    "": "≤",
    "": "≥",
    "": "[",
    "": "]",
}


@dataclass
class QuestionItem:
    ordinal: int
    code: str | None
    source_file: str
    parser: str
    question_html: str
    question_text: str
    raw_block: str


def pick_default_files(folder: str, limit: int) -> list[Path]:
    keywords = ("đề", "thi", "test", "bttl")
    scored: list[tuple[int, Path]] = []
    for path in Path(folder).rglob("*.pdf"):
        score = sum(1 for keyword in keywords if keyword in path.name.lower())
        if score:
            scored.append((score, path))
    scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
    needed = max(1, min(3, (limit + 19) // 20))
    return [path for _, path in scored[:needed]]


def strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", TAG_PATTERN.sub(" ", text)).strip()


def normalize_native_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for source, target in SYMBOL_FIXES.items():
        text = text.replace(source, target)
    text = INTERVAL_STACK_PATTERN.sub(lambda match: f"[{match.group(1).replace(' ', '')})", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if any(pattern.match(line) for pattern in NOISE_LINE_PATTERNS):
            continue
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_native_text(filepath: Path) -> str:
    doc = fitz.open(filepath)
    try:
        parts = [doc.load_page(page_number).get_text("text") for page_number in range(doc.page_count)]
    finally:
        doc.close()
    return normalize_native_text("\n\n".join(parts))


def run_mineru(filepath: Path, output_root: Path, method: str) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["MINERU_MODEL_SOURCE"] = "local"
    env["MINERU_TOOLS_CONFIG_JSON"] = str(MINERU_CONFIG)
    env["PYTHONIOENCODING"] = "utf-8"

    command = [
        str(MINERU_EXE),
        "-p",
        str(filepath),
        "-o",
        str(output_root),
        "-b",
        "pipeline",
        "-m",
        method,
        "-l",
        "ch",
    ]
    log.info("Chạy MinerU fallback: %s", filepath)
    subprocess.run(command, check=True, env=env, cwd=WORKDIR)
    base_dir = output_root / filepath.stem / method
    if not base_dir.exists():
        raise FileNotFoundError(f"Không thấy output MinerU tại {base_dir}")
    return base_dir


def render_block(block: str) -> tuple[str, str]:
    pieces: list[str] = []
    plain_parts: list[str] = []

    for segment in re.split(r"(<table>.*?</table>)", block, flags=re.DOTALL):
        if not segment.strip():
            continue
        if segment.lstrip().startswith("<table>"):
            pieces.append(segment)
            plain_parts.append(strip_tags(segment))
            continue

        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", segment) if part.strip()]
        for paragraph in paragraphs:
            safe = html.escape(paragraph).replace("\n", "<br>")
            pieces.append(f"<p>{safe}</p>")
            plain_parts.append(paragraph.replace("\n", " "))

    return "\n".join(pieces), " ".join(plain_parts).strip()


def parse_questions(text: str, source_file: str, parser_name: str, start_ordinal: int, limit: int) -> list[QuestionItem]:
    items: list[QuestionItem] = []
    for raw_block in QUESTION_SPLIT_PATTERN.split(text):
        raw_block = raw_block.strip()
        if not raw_block.startswith("[IT"):
            continue
        code_match = QUESTION_CODE_PATTERN.search(raw_block)
        code = code_match.group("code") if code_match else None
        cleaned = QUESTION_CODE_PATTERN.sub("", raw_block, count=1).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        rendered_html, plain_text = render_block(cleaned)
        if len(strip_tags(plain_text)) < 12:
            continue
        items.append(
            QuestionItem(
                ordinal=start_ordinal + len(items),
                code=code,
                source_file=source_file,
                parser=parser_name,
                question_html=rendered_html,
                question_text=plain_text,
                raw_block=cleaned,
            )
        )
        if len(items) >= limit:
            break
    return items


def render_html(items: list[QuestionItem], title: str, json_filename: str) -> str:
    cards: list[str] = []
    for item in items:
        meta_parts = [f"#{item.ordinal}", html.escape(item.source_file), html.escape(item.parser)]
        if item.code:
            meta_parts.append(f"IT{html.escape(item.code)}")
        cards.append(
            "\n".join(
                [
                    "<article class='card'>",
                    f"<div class='meta'>{' · '.join(meta_parts)}</div>",
                    item.question_html,
                    "<details>",
                    "<summary>Raw block</summary>",
                    f"<pre>{html.escape(item.raw_block)}</pre>",
                    "</details>",
                    "</article>",
                ]
            )
        )

    payload = [asdict(item) for item in items]
    return f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f4efe6;
      --paper: #fffdfa;
      --ink: #1b1714;
      --muted: #6e6256;
      --line: #d8c8b7;
      --accent: #8f4e20;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "Segoe UI", Arial, sans-serif;
      background:
        radial-gradient(circle at top right, rgba(143,78,32,0.12), transparent 24%),
        linear-gradient(180deg, #fbf7f1 0%, var(--bg) 100%);
    }}
    main {{ max-width: 1160px; margin: 0 auto; padding: 28px 20px 72px; }}
    header {{
      padding: 26px;
      border: 1px solid var(--line);
      border-radius: 22px;
      background: rgba(255,255,255,0.82);
      box-shadow: 0 20px 60px rgba(56, 35, 16, 0.08);
    }}
    h1 {{ margin: 0 0 10px; font-size: clamp(28px, 4vw, 42px); }}
    p {{ line-height: 1.6; margin: 0 0 12px; }}
    .json-link {{
      display: inline-block;
      margin-top: 10px;
      color: var(--accent);
      text-decoration: none;
      font-weight: 700;
    }}
    .grid {{ display: grid; gap: 18px; margin-top: 24px; }}
    .card {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 20px;
      box-shadow: 0 12px 36px rgba(56, 35, 16, 0.06);
    }}
    .meta {{ color: var(--muted); font-size: 14px; margin-bottom: 10px; }}
    details {{ margin-top: 14px; }}
    summary {{ color: var(--muted); cursor: pointer; }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 13px;
      background: #fbf7f1;
      border: 1px dashed var(--line);
      border-radius: 12px;
      padding: 12px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 12px 0;
      font-size: 14px;
    }}
    td, th {{
      border: 1px solid var(--line);
      padding: 8px 10px;
      vertical-align: top;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{html.escape(title)}</h1>
      <p>Bản preview này ưu tiên text gốc từ PDF để giảm lỗi OCR. Chỉ khi text native không đủ tốt thì mới fallback sang MinerU.</p>
      <a class="json-link" href="./{html.escape(json_filename)}">Mở JSON song song</a>
    </header>
    <section class="grid">
      {''.join(cards)}
    </section>
    <script id="payload" type="application/json">{html.escape(json.dumps(payload, ensure_ascii=False, indent=2))}</script>
  </main>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview câu hỏi từ PDF và render ra HTML.")
    parser.add_argument("--folder", default=DATABASE_FOLDER, help="Thư mục gốc chứa PDF.")
    parser.add_argument("--file", action="append", help="PDF cụ thể cần scan. Có thể truyền nhiều lần.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Số câu tối đa cần xuất.")
    parser.add_argument("--method", default=DEFAULT_METHOD, choices=["txt", "ocr", "auto"], help="Method MinerU khi cần fallback.")
    parser.add_argument("--output-html", default=DEFAULT_OUTPUT_HTML, help="File HTML đầu ra.")
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON, help="File JSON đầu ra.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    files = [Path(value) for value in args.file] if args.file else pick_default_files(args.folder, args.limit)
    if not files:
        raise SystemExit("Không tìm thấy PDF phù hợp.")
    if not MINERU_EXE.exists():
        raise SystemExit(f"Không tìm thấy mineru executable: {MINERU_EXE}")
    if not MINERU_CONFIG.exists():
        raise SystemExit(f"Không tìm thấy cấu hình MinerU local: {MINERU_CONFIG}")

    all_items: list[QuestionItem] = []

    for filepath in files:
        if not filepath.exists():
            log.warning("Bỏ qua file không tồn tại: %s", filepath)
            continue
        remaining = args.limit - len(all_items)
        if remaining <= 0:
            break

        native_text = extract_native_text(filepath)
        extracted = parse_questions(native_text, filepath.name, "native", len(all_items) + 1, remaining)
        if extracted:
            all_items.extend(extracted)
            log.info("Lấy được %s câu từ native text của %s. Lũy kế %s/%s", len(extracted), filepath.name, len(all_items), args.limit)
            continue

        log.info("Native text không đủ tốt, fallback sang MinerU cho %s", filepath.name)
        base_dir = run_mineru(filepath, MINERU_OUTPUT_ROOT, args.method)
        md_path = next(base_dir.glob("*.md"), None)
        if md_path is None:
            log.warning("Không tìm thấy Markdown output cho %s", filepath)
            continue
        md_text = md_path.read_text(encoding="utf-8", errors="ignore")
        extracted = parse_questions(md_text, filepath.name, f"mineru:{args.method}", len(all_items) + 1, remaining)
        all_items.extend(extracted)
        log.info("Lấy được %s câu từ MinerU của %s. Lũy kế %s/%s", len(extracted), filepath.name, len(all_items), args.limit)

    if not all_items:
        raise SystemExit("Không trích xuất được câu hỏi nào từ PDF.")

    html_path = WORKDIR / args.output_html
    json_path = WORKDIR / args.output_json
    json_path.write_text(json.dumps([asdict(item) for item in all_items], ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_html(all_items, f"Preview - {len(all_items)} câu hỏi", json_path.name), encoding="utf-8")
    log.info("Đã ghi HTML: %s", html_path)
    log.info("Đã ghi JSON: %s", json_path)


if __name__ == "__main__":
    main()
