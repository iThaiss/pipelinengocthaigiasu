"""
scan_theory.py — Scan 156 file lý thuyết → tạo learning map nodes.

Bước 1 (Haiku): Đọc toàn bộ nội dung → extract structure
  - lesson_title, knowledge_subtopic_code, concepts, prerequisites, confidence

Bước 2 (Sonnet): Viết lại thành bài học cho học sinh
  - markdown, dễ hiểu, có mẹo nhớ, ví dụ cụ thể

Output:
  local_curriculum_english/output_json/learning_map_nodes.json
  local_curriculum_english/cache/scan_theory/{sha1}.json

Usage:
  python scan_theory.py --root local_curriculum_english --save --preview
  python scan_theory.py --limit 5          # test 5 file đầu
  python scan_theory.py --skip-rewrite     # chỉ chạy Haiku (extract), không Sonnet
  python scan_theory.py --resume           # bỏ qua file đã có cache
"""

import argparse
import hashlib
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

# ─── Config ───────────────────────────────────────────────────────────────────
DEFAULT_ROOT = Path("local_curriculum_english")
FAST_MODEL  = os.getenv("CLAUDE_FAST_MODEL")  or "cc/claude-haiku-4-5-20251001"
SMART_MODEL = os.getenv("CLAUDE_SMART_MODEL") or "cc/claude-sonnet-4-6"
BASE_URL    = os.getenv("CLAUDE_BASE_URL", "http://localhost:20128").rstrip("/")
API_KEY     = os.getenv("NINEROUTER_API_KEY") or os.getenv("CLAUDE_API_KEY") or "local-9router"
AI_RETRIES  = int(os.getenv("SCAN_THEORY_AI_RETRIES", "6"))
AI_RETRY_BASE_SECONDS = float(os.getenv("SCAN_THEORY_AI_RETRY_BASE_SECONDS", "8"))
AI_TIMEOUT_SECONDS = float(os.getenv("SCAN_THEORY_AI_TIMEOUT_SECONDS", "120"))
AI_REQUEST_DELAY_SECONDS = float(os.getenv("SCAN_THEORY_AI_REQUEST_DELAY_SECONDS", "0.1"))
ERROR_PREFIX = "[Lỗi tạo nội dung:"

# ─── Taxonomy (cached vào system prompt) ──────────────────────────────────────
TAXONOMY_TEXT = """
TAXONOMY TIẾNG ANH THPT:

TOPICS:
EN01 - Grammar Foundation
EN02 - Verb Tenses and Verb Forms
EN03 - Sentence Structures and Clauses
EN04 - Vocabulary and Word Formation
EN05 - Reading Comprehension
EN06 - Cloze and Gap Filling
EN07 - Writing and Sentence Transformation
EN08 - Pronunciation and Phonetics
EN09 - Communication and Functional Language
EN10 - Test Practice and Mixed Skills

SUBTOPICS:
EN01.01 Parts of Speech
EN01.02 Articles, Determiners, Quantifiers
EN01.03 Prepositions and Phrasal Verbs
EN01.04 Comparisons
EN02.01 Present Tenses
EN02.02 Past Tenses
EN02.03 Future Forms
EN02.04 Sequence of Tenses
EN02.05 Gerunds and Infinitives
EN03.01 Passive Voice
EN03.02 Reported Speech
EN03.03 Conditional Sentences
EN03.04 Relative Clauses
EN03.05 Inversion
EN03.06 Conjunctions and Adverbial Clauses
EN03.07 Cleft Sentences and Emphasis
EN04.01 Word Formation
EN04.02 Collocations
EN04.03 Synonyms and Antonyms in Context
EN04.04 Semantic Fields
EN04.05 Topic Vocabulary
EN05.01 Main Idea and Title
EN05.02 Detail Questions
EN05.03 Reference Questions
EN05.04 Vocabulary in Context
EN05.05 True False Not Given and Except
EN05.06 Inference Questions
EN05.07 Paragraph Matching and Sentence Insertion
EN05.08 Summary and Paraphrase
EN06.01 Grammar Gap Filling
EN06.02 Vocabulary Gap Filling
EN06.03 Advertisement and Notice Cloze
EN06.04 Long Passage Cloze
EN07.01 Sentence Transformation
EN07.02 Sentence Combination
EN07.03 Paragraph and Letter Ordering
EN07.04 Writing Prompts
EN08.01 Stress
EN08.02 Sound Identification
EN08.03 Ending Sounds
EN09.01 Everyday Conversation
EN09.02 Agreement, Disagreement, Suggestions
EN09.03 Requests, Offers, Invitations
EN10.01 Basic Mock Exams
EN10.02 Applied Mock Exams
EN10.03 Advanced Mock Exams
EN10.04 HSA/DGNL Practice
EN10.99 Needs Review or Mixed
""".strip()

HAIKU_SYSTEM = f"""Bạn là chuyên gia phân tích tài liệu học tiếng Anh THPT Việt Nam.
Nhiệm vụ: Đọc nội dung file lý thuyết và extract cấu trúc kiến thức.

{TAXONOMY_TEXT}

Trả lời JSON duy nhất theo schema sau (không giải thích thêm):
{{
  "lesson_title": "tên bài học ngắn gọn bằng tiếng Việt",
  "knowledge_subtopic_code": "ENxx.xx (chọn 1 code phù hợp nhất từ taxonomy)",
  "concepts": ["khái niệm chính 1", "khái niệm chính 2", ...],
  "prerequisites": ["ENxx.xx", ...],
  "lesson_summary": "tóm tắt 1-2 câu nội dung bài học",
  "confidence": "high|medium|low"
}}

Lưu ý:
- Nếu bài covers nhiều subtopic, chọn subtopic chính nhất
- prerequisites là code các subtopic cần biết trước (có thể rỗng [])
- Chỉ dùng code từ taxonomy đã cho, không tự tạo code mới"""

SONNET_SYSTEM = """Bạn là giáo viên tiếng Anh giỏi, chuyên dạy học sinh THPT chuẩn bị thi tốt nghiệp.
Nhiệm vụ: Viết lại bài học lý thuyết thành tài liệu học sinh dễ hiểu, súc tích, hữu ích.

Yêu cầu format (Markdown):
1. **Mục tiêu** — 2-3 gạch đầu dòng, học sinh sẽ học được gì
2. **Lý thuyết cốt lõi** — trình bày kiến thức rõ ràng, có ví dụ tiếng Anh kèm nghĩa
3. **Mẹo thi** — 2-3 mẹo nhớ nhanh hoặc tránh lỗi phổ biến
4. **Ví dụ minh họa** — 3-5 câu ví dụ điển hình (có thể lấy từ nội dung gốc)

Nguyên tắc:
- Giữ kiến thức chính xác từ tài liệu gốc, không bịa
- Ngôn ngữ: tiếng Việt giải thích, tiếng Anh ví dụ
- Ngắn gọn nhưng đủ (300-600 từ)
- Dùng markdown headers (##), bullet points, **bold** cho terms quan trọng
- KHÔNG viết phần mở đầu chào hỏi hay kết luận xã giao"""


# ─── Helpers ──────────────────────────────────────────────────────────────────
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


def read_all_pages(path: Path, max_pages: int = 30) -> str:
    """Đọc toàn bộ nội dung PDF (tối đa max_pages trang)."""
    try:
        with fitz.open(path) as doc:
            pages = []
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                text = page.get_text("text", sort=True).strip()
                if text:
                    pages.append(f"[Trang {i+1}]\n{text}")
            return "\n\n".join(pages)
    except Exception as e:
        return f"[Lỗi đọc file: {e}]"


def extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def slugify(text: str) -> str:
    """Tạo slug từ tiếng Việt/Anh."""
    text = text.lower()
    replacements = {
        "à": "a", "á": "a", "ả": "a", "ã": "a", "ạ": "a",
        "ă": "a", "ắ": "a", "ặ": "a", "ằ": "a", "ẳ": "a", "ẵ": "a",
        "â": "a", "ấ": "a", "ầ": "a", "ẩ": "a", "ẫ": "a", "ậ": "a",
        "đ": "d",
        "è": "e", "é": "e", "ẻ": "e", "ẽ": "e", "ẹ": "e",
        "ê": "e", "ế": "e", "ề": "e", "ể": "e", "ễ": "e", "ệ": "e",
        "ì": "i", "í": "i", "ỉ": "i", "ĩ": "i", "ị": "i",
        "ò": "o", "ó": "o", "ỏ": "o", "õ": "o", "ọ": "o",
        "ô": "o", "ố": "o", "ồ": "o", "ổ": "o", "ỗ": "o", "ộ": "o",
        "ơ": "o", "ớ": "o", "ờ": "o", "ở": "o", "ỡ": "o", "ợ": "o",
        "ù": "u", "ú": "u", "ủ": "u", "ũ": "u", "ụ": "u",
        "ư": "u", "ứ": "u", "ừ": "u", "ử": "u", "ữ": "u", "ự": "u",
        "ỳ": "y", "ý": "y", "ỷ": "y", "ỹ": "y", "ỵ": "y",
    }
    for v, r in replacements.items():
        text = text.replace(v, r)
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text.strip())
    text = re.sub(r"-+", "-", text)
    return text[:50].strip("-")


def make_node_code(subtopic_code: str, lesson_title: str) -> str:
    slug = slugify(lesson_title)
    return f"{subtopic_code}-{slug}" if slug else subtopic_code



def is_error_content(value: Any) -> bool:
    return isinstance(value, str) and value.strip().startswith(ERROR_PREFIX)

def is_retryable_ai_error(exc: Exception) -> bool:
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    return status in {408, 409, 429, 500, 502, 503, 504} or any(
        token in text for token in ("429", "rate_limit", "rate limit", "timeout", "temporarily", "overloaded")
    )

def reset_after_seconds(exc: Exception) -> float | None:
    text = str(exc).lower()
    match = re.search(r"reset after\s+(?:(\d+)m\s*)?(\d+)?s?", text)
    if not match:
        return None
    minutes = int(match.group(1) or 0)
    seconds = int(match.group(2) or 0)
    total = minutes * 60 + seconds
    return float(total + 10) if total > 0 else None

def ai_call_with_retry(label: str, fn):
    last_exc: Exception | None = None
    for attempt in range(1, AI_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= AI_RETRIES or not is_retryable_ai_error(exc):
                raise
            wait = reset_after_seconds(exc) or (AI_RETRY_BASE_SECONDS * attempt)
            print(f"  ↳ {label} retry {attempt}/{AI_RETRIES} sau {wait:.0f}s: {exc}", flush=True)
            time.sleep(wait)
    if last_exc:
        raise last_exc
    raise RuntimeError(f"{label} failed without exception")

def uniquify_node_codes(nodes: list[dict[str, Any]]) -> None:
    seen: dict[str, int] = {}
    for node in nodes:
        base = node.get("node_code") or make_node_code(
            node.get("knowledge_subtopic_code", "EN10.99"),
            node.get("node_title") or node.get("file_name") or "node",
        )
        count = seen.get(base, 0) + 1
        seen[base] = count
        node["node_code"] = base if count == 1 else f"{base}-{count}"

def merge_with_existing_nodes(updated_nodes: list[dict[str, Any]], existing_path: Path) -> list[dict[str, Any]]:
    if not existing_path.exists():
        return updated_nodes
    existing_payload = json.loads(existing_path.read_text(encoding="utf-8"))
    existing_nodes = existing_payload.get("nodes", [])
    by_sha1 = {node.get("sha1"): node for node in updated_nodes if node.get("sha1")}
    merged = [by_sha1.get(node.get("sha1"), node) for node in existing_nodes]
    existing_sha1 = {node.get("sha1") for node in existing_nodes}
    merged.extend(node for node in updated_nodes if node.get("sha1") not in existing_sha1)
    uniquify_node_codes(merged)
    return merged

# ─── AI calls ─────────────────────────────────────────────────────────────────
def haiku_extract_structure(
    client: anthropic.Anthropic,
    file_name: str,
    folder_path: str,
    content: str,
) -> dict[str, Any]:
    user = f"""Folder: {folder_path}
File: {file_name}

---NỘI DUNG FILE---
{content[:6000]}
"""
    try:
        resp = ai_call_with_retry(
            "Haiku extract",
            lambda: client.messages.create(
                model=FAST_MODEL,
                max_tokens=500,
                temperature=0,
                system=[{
                    "type": "text",
                    "text": HAIKU_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user}],
            ),
        )
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        )
        data = extract_json(text)
        # Validate subtopic_code format
        code = data.get("knowledge_subtopic_code", "")
        if not re.match(r"^EN\d{2}\.\d{2}$", code):
            data["knowledge_subtopic_code"] = "EN10.99"
        return data
    except Exception as e:
        return {
            "lesson_title": file_name,
            "knowledge_subtopic_code": "EN10.99",
            "concepts": [],
            "prerequisites": [],
            "lesson_summary": "",
            "confidence": "low",
            "_error": str(e),
        }


def sonnet_rewrite_lesson(
    client: anthropic.Anthropic,
    lesson_title: str,
    subtopic_code: str,
    content: str,
) -> str:
    user = f"""Bài học: {lesson_title} ({subtopic_code})

---NỘI DUNG GỐC---
{content[:8000]}

---YÊU CẦU---
Viết lại thành bài học cho học sinh THPT theo format đã hướng dẫn."""
    try:
        resp = ai_call_with_retry(
            "Sonnet rewrite",
            lambda: client.messages.create(
                model=SMART_MODEL,
                max_tokens=1500,
                temperature=0.3,
                system=[{
                    "type": "text",
                    "text": SONNET_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user}],
            ),
        )
        return "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        )
    except Exception as e:
        return f"[Lỗi tạo nội dung: {e}]"


# ─── Process single file ──────────────────────────────────────────────────────
def process_theory_file(
    path: Path,
    input_dir: Path,
    client: anthropic.Anthropic,
    cache_dir: Path,
    skip_rewrite: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    relative = path.relative_to(input_dir)
    folder_str = relative.parent.as_posix() if relative.parent != Path(".") else ""
    parts = folder_str.split("/") if folder_str else []

    sha1 = file_sha1(path)
    cache_path = cache_dir / f"{sha1}.json"
    cached_structure: dict[str, Any] | None = None

    # Cache hit (và không force)
    if cache_path.exists() and not force:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        # Cache lỗi/rỗng sẽ được chạy lại, nhất là lỗi 429 từ 9Router/provider.
        if skip_rewrite or (cached.get("theory_content") and not is_error_content(cached.get("theory_content"))):
            cached["_from_cache"] = True
            cached["relative_path"] = relative.as_posix()
            cached["folder_path"] = folder_str
            cached["section"] = parts[0] if parts else ""
            cached["subsection"] = parts[1] if len(parts) > 1 else ""
            cached["file_name"] = path.name
            return cached
        cached_structure = cached

    # Đọc toàn bộ nội dung
    content = read_all_pages(path)

    # Bước 1: Haiku extract structure. Nếu cache chỉ lỗi rewrite, giữ metadata cũ và chỉ gọi lại Sonnet.
    if cached_structure:
        structure = cached_structure
    else:
        structure = haiku_extract_structure(client, path.name, folder_str, content)
        time.sleep(AI_REQUEST_DELAY_SECONDS)

    lesson_title = structure.get("lesson_title") or path.stem
    subtopic_code = structure.get("knowledge_subtopic_code", "EN10.99")
    node_code = make_node_code(subtopic_code, lesson_title)

    # Bước 2: Sonnet viết lại (trừ khi --skip-rewrite)
    theory_content = ""
    if not skip_rewrite:
        theory_content = sonnet_rewrite_lesson(client, lesson_title, subtopic_code, content)
        time.sleep(AI_REQUEST_DELAY_SECONDS)

    node = {
        "sha1": sha1,
        "node_code": node_code,
        "node_title": lesson_title,
        "knowledge_subtopic_code": subtopic_code,
        "concepts": structure.get("concepts", []),
        "prerequisites": structure.get("prerequisites", []),
        "lesson_summary": structure.get("lesson_summary", ""),
        "confidence": structure.get("confidence", "medium"),
        "theory_content": theory_content,
        "source_file": path.name,
        "relative_path": relative.as_posix(),
        "folder_path": folder_str,
        "section": parts[0] if parts else "",
        "subsection": parts[1] if len(parts) > 1 else "",
        "file_name": path.name,
        "practice_files": [],
        "exercise_types": [],
        "estimated_question_count": 0,
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "_from_cache": False,
    }

    # Lưu cache
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(node, ensure_ascii=False, indent=2), encoding="utf-8")

    return node


# ─── Main scan ────────────────────────────────────────────────────────────────
def scan_theory_files(
    manifest_path: Path,
    input_dir: Path,
    client: anthropic.Anthropic,
    cache_dir: Path,
    limit: int | None = None,
    skip_rewrite: bool = False,
    force: bool = False,
    only_errors: bool = False,
) -> list[dict]:
    # Đọc manifest
    if not manifest_path.exists():
        print(f"❌ Không tìm thấy manifest: {manifest_path}")
        print("   Hãy chạy classify_files.py --save trước.")
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    theory_files = [f for f in manifest["files"] if f["file_type"] == "theory"]

    if only_errors:
        filtered = []
        for file_info in theory_files:
            path = input_dir / file_info["relative_path"]
            sha1 = file_sha1(path) if path.exists() else ""
            cache_path = cache_dir / f"{sha1}.json"
            if not cache_path.exists():
                filtered.append(file_info)
                continue
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                filtered.append(file_info)
                continue
            if is_error_content(cached.get("theory_content")) or not cached.get("theory_content"):
                filtered.append(file_info)
        theory_files = filtered

    if limit:
        theory_files = theory_files[:limit]

    total = len(theory_files)
    print(f"Theory files: {total}")
    print(f"9Router/API:  {normalize_base_url(BASE_URL)}/v1")
    print(f"Fast model:   {FAST_MODEL}")
    print(f"Smart model:  {SMART_MODEL if not skip_rewrite else '(skip)'}")
    print(f"Retries:      {AI_RETRIES} x backoff {AI_RETRY_BASE_SECONDS:.0f}s")
    print(f"Timeout:      {AI_TIMEOUT_SECONDS:.0f}s/request")
    print(f"Delay:        {AI_REQUEST_DELAY_SECONDS:.1f}s/request\n", flush=True)

    nodes = []
    cached_count = 0
    haiku_count = 0
    sonnet_count = 0
    error_count = 0

    for i, file_info in enumerate(theory_files, start=1):
        path = input_dir / file_info["relative_path"]
        if not path.exists():
            print(f"[{i:>3}/{total}] ⚠️  Không tìm thấy: {path.name[:60]}", flush=True)
            error_count += 1
            continue

        node = process_theory_file(
            path, input_dir, client, cache_dir,
            skip_rewrite=skip_rewrite, force=force
        )

        from_cache = node.pop("_from_cache", False)
        if from_cache:
            cached_count += 1
            tag = "💾"
        else:
            haiku_count += 1
            if not skip_rewrite:
                sonnet_count += 1
            tag = "🤖"

        conf = node.get("confidence", "?")[0].upper()
        code = node.get("knowledge_subtopic_code", "?")
        print(f"[{i:>3}/{total}] {tag} [{code}] [{conf}] {node['node_title'][:55]}", flush=True)

        nodes.append(node)

    uniquify_node_codes(nodes)
    print(f"\n🤖 Haiku: {haiku_count} | ✍️  Sonnet: {sonnet_count} | 💾 Cache: {cached_count} | ⚠️  Error: {error_count}")
    return nodes


# ─── Output ───────────────────────────────────────────────────────────────────
def save_nodes_json(nodes: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_nodes": len(nodes),
        "nodes": nodes,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {path}")


def save_nodes_html(nodes: list[dict], path: Path) -> None:
    import html as html_lib
    path.parent.mkdir(parents=True, exist_ok=True)

    conf_color = {"high": "#d4edda", "medium": "#fff3cd", "low": "#f8d7da"}
    rows = ""
    for n in nodes:
        conf = n.get("confidence", "medium")
        concepts = ", ".join(n.get("concepts", [])[:4])
        prereqs = ", ".join(n.get("prerequisites", []))
        has_content = "✅" if n.get("theory_content") and not is_error_content(n.get("theory_content")) else "❌"
        rows += (
            f'<tr style="background:{conf_color.get(conf,"#fff")}">'
            f'<td style="font-size:11px;color:#666">{html_lib.escape(n.get("knowledge_subtopic_code",""))}</td>'
            f'<td><b>{html_lib.escape(n.get("node_title",""))}</b></td>'
            f'<td style="font-size:11px">{html_lib.escape(concepts)}</td>'
            f'<td style="font-size:11px;color:#888">{html_lib.escape(prereqs)}</td>'
            f'<td style="text-align:center">{conf}</td>'
            f'<td style="text-align:center">{has_content}</td>'
            f'<td style="font-size:11px;color:#666">{html_lib.escape(n.get("section",""))}</td>'
            f'<td style="font-size:11px">{html_lib.escape(n.get("file_name","")[:50])}</td>'
            f"</tr>\n"
        )

    html_content = f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8"><title>Learning Map Nodes</title>
<style>
  body{{font-family:sans-serif;font-size:12px;padding:20px}}
  table{{border-collapse:collapse;width:100%}}
  th,td{{border:1px solid #ccc;padding:5px 8px;text-align:left;vertical-align:top}}
  th{{background:#333;color:white;position:sticky;top:0}}
  .sum{{margin-bottom:16px;padding:12px;background:#f0f0f0;border-radius:6px}}
</style></head><body>
<h2>Learning Map Nodes — Tiếng Anh THPT</h2>
<div class="sum">
  Tổng nodes: <b>{len(nodes)}</b> &nbsp;|&nbsp;
  <span style="background:#d4edda;padding:2px 8px">High confidence: {sum(1 for n in nodes if n.get('confidence')=='high')}</span>&nbsp;
  <span style="background:#fff3cd;padding:2px 8px">Medium: {sum(1 for n in nodes if n.get('confidence')=='medium')}</span>&nbsp;
  <span style="background:#f8d7da;padding:2px 8px">Low: {sum(1 for n in nodes if n.get('confidence')=='low')}</span>&nbsp;
  &nbsp;|&nbsp; Có theory_content: {sum(1 for n in nodes if n.get('theory_content') and not is_error_content(n.get('theory_content')))}
</div>
<table>
<tr><th>Code</th><th>Node title</th><th>Concepts</th><th>Prerequisites</th><th>Confidence</th><th>Content</th><th>Section</th><th>File</th></tr>
{rows}</table></body></html>"""
    path.write_text(html_content, encoding="utf-8")
    print(f"Saved: {path}")


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--save", action="store_true", help="Ghi learning_map_nodes.json")
    parser.add_argument("--preview", action="store_true", help="Tạo HTML preview")
    parser.add_argument("--limit", type=int, help="Chỉ xử lý N file đầu")
    parser.add_argument("--skip-rewrite", action="store_true", help="Bỏ qua bước Sonnet viết lại")
    parser.add_argument("--force", action="store_true", help="Bỏ qua cache, xử lý lại tất cả")
    parser.add_argument("--only-errors", action="store_true", help="Chỉ chạy lại cache thiếu/lỗi theory_content")
    args = parser.parse_args()

    root = Path(args.root)
    input_dir = root / "input_sources"
    manifest_path = root / "output_json" / "file_manifest.json"
    cache_dir = root / "cache" / "scan_theory"
    out_json = root / "output_json" / "learning_map_nodes.json"
    out_html = root / "previews" / "learning_map_nodes_preview.html"

    base = normalize_base_url(BASE_URL)
    client = anthropic.Anthropic(api_key=API_KEY, base_url=f"{base}/v1", timeout=AI_TIMEOUT_SECONDS)

    nodes = scan_theory_files(
        manifest_path, input_dir, client, cache_dir,
        limit=args.limit,
        skip_rewrite=args.skip_rewrite,
        force=args.force,
        only_errors=args.only_errors,
    )

    if args.only_errors:
        nodes = merge_with_existing_nodes(nodes, out_json)

    if args.save:
        save_nodes_json(nodes, out_json)
    if args.preview:
        save_nodes_html(nodes, out_html)

    if not args.save and not args.preview:
        print("\n(Dùng --save để ghi JSON, --preview để xem HTML)")


if __name__ == "__main__":
    main()
