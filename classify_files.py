"""
classify_files.py — Phân loại file PDF bằng AI đọc trang đầu.

Thứ tự lọc:
  1. standard_exam  — đề thi chuẩn THPT/DGNL (dễ nhận nhất)
  2. vip90_bundle   — file tổng hợp cả tuần VIP90
  3. theory         — lý thuyết, giải thích, hướng dẫn
  4. practice       — bài tập, đề luyện chuyên đề

Cache theo sha1 → chạy lại không tốn token.

Usage:
  python classify_files.py              # dry run, in kết quả
  python classify_files.py --save       # ghi file_manifest.json
  python classify_files.py --preview    # tạo HTML preview
  python classify_files.py --no-ai      # chỉ dùng rule, không gọi AI
  python classify_files.py --limit 20   # chỉ xử lý 20 file đầu (test)
"""

import argparse
import hashlib
import html as html_lib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
import fitz

fitz.TOOLS.mupdf_display_errors(False)

DEFAULT_ROOT = Path("local_curriculum_english")
MODEL = os.getenv("CLAUDE_FAST_MODEL") or "cc/claude-haiku-4-5-20251001"
BASE_URL = os.getenv("CLAUDE_BASE_URL", "http://localhost:20128").rstrip("/")
API_KEY = os.getenv("NINEROUTER_API_KEY") or os.getenv("CLAUDE_API_KEY") or "local-9router"

VALID_TYPES = ("theory", "practice", "standard_exam", "vip90_bundle")

SYSTEM_PROMPT = """Bạn là chuyên gia phân loại tài liệu học tiếng Anh THPT Việt Nam.
Nhiệm vụ: đọc nội dung trang đầu của một file PDF và phân loại vào đúng 1 trong 4 loại.

CÁC LOẠI:

1. standard_exam — Đề thi chuẩn cấu trúc THPT/DGNL/HSA:
   - Có "Thời gian làm bài: X phút", "X câu trắc nghiệm", "MÃ ĐỀ"
   - Có "Bài thi: NGOẠI NGỮ" hoặc "Môn thi: TIẾNG ANH"
   - Là đề thi thật, đề thi thử, đề dự đoán kỳ thi quốc gia
   - Gồm NHIỀU dạng câu hỏi khác nhau trong 1 đề (cloze + reading + grammar + ...)
   - KHÔNG phải bài tập chuyên đề một chủ đề

2. vip90_bundle — File tổng hợp cả tuần của khóa VIP90:
   - Tiêu đề có "[VIP 90" hoặc "Tài liệu đầy đủ Tuần"
   - Gộp nhiều buổi học + bài thi online trong 1 file

3. theory — Lý thuyết, hướng dẫn kỹ thuật làm bài:
   - Có nhiều đoạn văn tiếng Việt giải thích ngữ pháp/kỹ năng
   - Có ví dụ minh họa với giải thích
   - Có mục I. II. III. hoặc cấu trúc bài giảng rõ ràng
   - Có thể có 1 số câu bài tập nhỏ ở cuối nhưng phần lý thuyết chiếm chủ đạo
   - Bao gồm: bài giảng ngữ pháp, hướng dẫn dạng bài, chiến thuật làm đề

4. practice — Bài tập luyện tập chuyên đề:
   - Chủ yếu là câu hỏi A/B/C/D
   - Tập trung vào 1 chủ đề ngữ pháp/kỹ năng cụ thể
   - Bao gồm: thi online chuyên đề, đề luyện theo chủ đề, bài tập viết lại câu
   - Có thể có đáp án hoặc không

Trả lời JSON duy nhất, không giải thích thêm:
{"file_type": "theory|practice|standard_exam|vip90_bundle", "reason": "1 câu ngắn lý do"}"""


def normalize_base_url(value: str) -> str:
    value = value.rstrip("/")
    if value.endswith("/v1"):
        return value[:-3]
    return value


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_first_pages(path: Path, n_pages: int = 2) -> str:
    try:
        with fitz.open(path) as doc:
            pages = []
            for i, page in enumerate(doc):
                if i >= n_pages:
                    break
                text = page.get_text("text", sort=True).strip()
                if text:
                    pages.append(f"[Trang {i+1}]\n{text}")
            return "\n\n".join(pages)
    except Exception:
        return ""


def extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def natural_sort_key(value: str) -> list:
    parts = re.split(r"(\d+)", value.casefold())
    return [int(p) if p.isdigit() else p for p in parts]


def path_sort_key(path: Path) -> list:
    key = []
    for part in path.parts:
        key.extend(natural_sort_key(part))
        key.append("")
    return key


def quick_classify(folder_path: str, file_name: str, page_text: str) -> str | None:
    """Trả về loại nếu chắc chắn qua rule, None nếu cần AI."""
    name_lower = file_name.lower()
    folder_upper = folder_path.upper()
    text_lower = page_text.lower()

    # VIP90 bundle — chắc chắn qua filename
    if "VIP90" in folder_upper and (
        "tài liệu đầy đủ" in name_lower
        or "tai lieu day du" in name_lower.replace(" ", "")
    ):
        return "vip90_bundle"

    # Standard exam — signal rõ trong text trang đầu
    exam_text_signals = [
        "thời gian làm bài",
        "câu trắc nghiệm",
        "mã đề",
        "bài thi: ngoại ngữ",
        "môn thi: tiếng anh",
        "đề thi tốt nghiệp thpt",
    ]
    if any(sig in text_lower for sig in exam_text_signals):
        return "standard_exam"

    return None  # Cần AI


def classify_with_ai(
    client: anthropic.Anthropic,
    file_name: str,
    folder_path: str,
    page_text: str,
) -> dict[str, str]:
    user = f"""Folder: {folder_path}
File: {file_name}

---NỘI DUNG TRANG ĐẦU---
{page_text[:3000]}
"""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=200,
            temperature=0,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in response.content
            if getattr(block, "type", None) == "text"
        )
        result = extract_json(text)
        ft = result.get("file_type", "")
        if ft not in VALID_TYPES:
            ft = "theory"
        return {"file_type": ft, "reason": result.get("reason", ""), "method": "ai"}
    except Exception as e:
        return {"file_type": "theory", "reason": f"ai_error: {e}", "method": "fallback"}


def process_file(
    path: Path,
    input_dir: Path,
    client: anthropic.Anthropic | None,
    cache_dir: Path,
) -> dict[str, Any]:
    relative = path.relative_to(input_dir)
    folder = relative.parent
    folder_str = "" if folder == Path(".") else folder.as_posix()
    parts = folder_str.split("/") if folder_str else []

    sha1 = file_sha1(path)
    cache_path = cache_dir / f"{sha1}.json"

    # Cache hit
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        return {
            **cached,
            "from_cache": True,
            "order": 0,
            "relative_path": relative.as_posix(),
            "folder_path": folder_str,
            "section": parts[0] if parts else "",
            "subsection": parts[1] if len(parts) > 1 else "",
            "file_name": path.name,
            "stem": path.stem,
        }

    page_text = read_first_pages(path, n_pages=2)

    quick = quick_classify(folder_str, path.name, page_text)
    if quick:
        result = {"file_type": quick, "reason": "rule-based", "method": "rule"}
    elif client is None:
        result = {"file_type": "theory", "reason": "no-ai-fallback", "method": "fallback"}
    else:
        result = classify_with_ai(client, path.name, folder_str, page_text)

    cache_entry = {
        "sha1": sha1,
        "file_type": result["file_type"],
        "reason": result["reason"],
        "method": result["method"],
        "classified_at": datetime.now().isoformat(timespec="seconds"),
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache_entry, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        **cache_entry,
        "from_cache": False,
        "order": 0,
        "relative_path": relative.as_posix(),
        "folder_path": folder_str,
        "section": parts[0] if parts else "",
        "subsection": parts[1] if len(parts) > 1 else "",
        "file_name": path.name,
        "stem": path.stem,
    }


def scan_and_classify(
    input_dir: Path,
    client: anthropic.Anthropic | None,
    cache_dir: Path,
    limit: int | None = None,
) -> list[dict]:
    files = sorted(
        [p for p in input_dir.rglob("*.pdf") if p.is_file()],
        key=lambda p: path_sort_key(p.relative_to(input_dir)),
    )
    if limit:
        files = files[:limit]

    results = []
    total = len(files)
    ai_calls = 0
    cache_hits = 0
    rule_hits = 0

    for order, path in enumerate(files, start=1):
        item = process_file(path, input_dir, client, cache_dir)
        item["order"] = order
        results.append(item)

        if item.get("from_cache"):
            cache_hits += 1
            tag = "💾"
        elif item.get("method") == "ai":
            ai_calls += 1
            tag = "🤖"
        else:
            rule_hits += 1
            tag = "⚡"

        print(
            f"[{order:>3}/{total}] {tag} [{item['file_type']:<13}] {path.name[:65]}"
        )

        if item.get("method") == "ai":
            time.sleep(0.05)

    print(f"\n🤖 AI: {ai_calls} | 💾 Cache: {cache_hits} | ⚡ Rule: {rule_hits}")
    return results


def print_summary(items: list[dict]) -> None:
    counts: dict[str, int] = {}
    for item in items:
        counts[item["file_type"]] = counts.get(item["file_type"], 0) + 1

    print(f"\n{'─'*55}")
    print(f"Tổng: {len(items)} file")
    print(f"  [A] theory:        {counts.get('theory', 0):>4}")
    print(f"  [B] practice:      {counts.get('practice', 0):>4}")
    print(f"  [C] standard_exam: {counts.get('standard_exam', 0):>4}")
    print(f"  [D] vip90_bundle:  {counts.get('vip90_bundle', 0):>4}")
    print(f"{'─'*55}\n")


def save_json(items: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for item in items:
        counts[item["file_type"]] = counts.get(item["file_type"], 0) + 1
    clean = [
        {
            "order": i["order"],
            "file_type": i["file_type"],
            "method": i.get("method", ""),
            "reason": i.get("reason", ""),
            "relative_path": i["relative_path"],
            "folder_path": i["folder_path"],
            "section": i["section"],
            "subsection": i["subsection"],
            "file_name": i["file_name"],
            "stem": i["stem"],
            "sha1": i.get("sha1", ""),
        }
        for i in items
    ]
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(items),
        "counts": counts,
        "files": clean,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {path}")


def save_html(items: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    type_color = {
        "theory": "#d4edda",
        "practice": "#fff3cd",
        "standard_exam": "#f8d7da",
        "vip90_bundle": "#d0e8ff",
    }
    type_label = {
        "theory": "[A] Lý thuyết",
        "practice": "[B] Bài tập",
        "standard_exam": "[C] Đề chuẩn",
        "vip90_bundle": "[D] VIP90 Bundle",
    }
    rows = ""
    for item in items:
        ft = item["file_type"]
        rows += (
            f'<tr style="background:{type_color.get(ft,"#fff")}">'
            f'<td>{item["order"]}</td>'
            f'<td><b>{type_label.get(ft, ft)}</b></td>'
            f'<td style="color:#666;font-size:11px">{item.get("method","")}</td>'
            f'<td>{html_lib.escape(item["section"])}</td>'
            f'<td>{html_lib.escape(item["subsection"])}</td>'
            f'<td>{html_lib.escape(item["file_name"])}</td>'
            f'<td style="color:#888;font-size:11px">{html_lib.escape(item.get("reason",""))}</td>'
            f"</tr>\n"
        )
    counts: dict[str, int] = {}
    for i in items:
        counts[i["file_type"]] = counts.get(i["file_type"], 0) + 1

    html_content = f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8"><title>File Manifest</title>
<style>
  body{{font-family:sans-serif;font-size:12px;padding:20px}}
  table{{border-collapse:collapse;width:100%}}
  th,td{{border:1px solid #ccc;padding:5px 8px;text-align:left}}
  th{{background:#333;color:white;position:sticky;top:0}}
  .sum{{margin-bottom:16px;padding:12px;background:#f0f0f0;border-radius:6px}}
</style></head><body>
<h2>File Manifest — Tiếng Anh Learning Map</h2>
<div class="sum">
  Tổng: <b>{len(items)}</b> &nbsp;|&nbsp;
  <span style="background:#d4edda;padding:2px 8px">[A] Lý thuyết: {counts.get('theory',0)}</span>&nbsp;
  <span style="background:#fff3cd;padding:2px 8px">[B] Bài tập: {counts.get('practice',0)}</span>&nbsp;
  <span style="background:#f8d7da;padding:2px 8px">[C] Đề chuẩn: {counts.get('standard_exam',0)}</span>&nbsp;
  <span style="background:#d0e8ff;padding:2px 8px">[D] VIP90 Bundle: {counts.get('vip90_bundle',0)}</span>
</div>
<table>
<tr><th>#</th><th>Loại</th><th>Method</th><th>Section</th><th>Subsection</th><th>File name</th><th>Reason</th></tr>
{rows}</table></body></html>"""
    path.write_text(html_content, encoding="utf-8")
    print(f"Saved: {path}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    root = Path(args.root)
    input_dir = root / "input_sources"
    cache_dir = root / "cache" / "classify"

    if not input_dir.exists():
        print(f"Không tìm thấy: {input_dir}")
        return

    client = None
    if not args.no_ai:
        base = normalize_base_url(BASE_URL)
        client = anthropic.Anthropic(api_key=API_KEY, base_url=f"{base}/v1")

    print(f"Scan: {input_dir}")
    print(f"Model: {MODEL if client else 'rule-only'}\n")

    items = scan_and_classify(input_dir, client, cache_dir, limit=args.limit)
    print_summary(items)

    if args.save:
        save_json(items, root / "output_json" / "file_manifest.json")
    if args.preview:
        save_html(items, root / "previews" / "file_manifest_preview.html")


if __name__ == "__main__":
    main()
