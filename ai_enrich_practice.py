"""AI Enrich Practice — re-parse rejected OCR questions + 2 failed files.

Usage:
    .venv/bin/python ai_enrich_practice.py [--root local_curriculum_english] [--dry-run]

Outputs (relative to project root):
    practice_questions_ai_enriched.json   — only new questions from this run
    practice_ai_enrich_report.html        — coverage table + parse stats
    practice_questions.json               — updated in-place
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

# ── 9Router config (copied from scan_practice_meta.py) ───────────────────────
BASE_URL = os.getenv("CLAUDE_BASE_URL", "http://localhost:20128").rstrip("/")
API_KEY  = os.getenv("NINEROUTER_API_KEY") or os.getenv("CLAUDE_API_KEY") or "local-9router"
MODEL    = "gz-prod/claude-haiku-4-5"

AI_RETRIES             = int(os.getenv("PRACTICE_AI_RETRIES", "3"))
AI_RETRY_BASE_SECONDS  = float(os.getenv("PRACTICE_AI_RETRY_BASE_SECONDS", "8"))
AI_TIMEOUT_SECONDS     = float(os.getenv("PRACTICE_AI_TIMEOUT_SECONDS", "180"))
AI_REQUEST_DELAY       = float(os.getenv("PRACTICE_AI_REQUEST_DELAY_SECONDS", "0.35"))

OCR_CHUNK_MAX = 3000   # chars per AI call

SYSTEM_PROMPT = """Bạn là parser câu hỏi trắc nghiệm tiếng Anh. Từ raw OCR text,
hãy trích xuất tất cả câu hỏi trắc nghiệm. Trả về CHỈ JSON array thuần, không markdown,
không giải thích, không backtick. Mỗi object có dạng:
{"question": "nội dung câu hỏi", "options": {"A": "...", "B": "...", "C": "...", "D": "..."}, "answer": "A" hoặc null, "source": "ocr_ai_enriched"}
Với bài điền từ (Word Formation / fill-in-blank), dùng "options": {} và "answer": null.
Nếu không tìm được câu hỏi nào, trả về []."""

# subtopic/format mapping for failed Word Formation files
WF_SUBTOPIC = "E2X.01"
WF_FORMAT   = "spt_word_formation"
WF_EXAM     = ["HSA_ENGLISH", "SPT_ENGLISH"]

DEFAULT_ROOT  = Path("local_curriculum_english")
PIPELINE_ROOT = Path(".")


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


# ── AI helpers (from scan_practice_meta.py pattern) ──────────────────────────

def is_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    return status in {408, 409, 429, 500, 502, 503, 504} or any(
        tok in text for tok in ["429", "rate_limit", "rate limit", "timeout", "overloaded", "temporarily"]
    )


def reset_after_seconds(exc: Exception) -> float | None:
    m = re.search(r"reset after\s+(?:(\d+)m\s*)?(\d+)?s?", str(exc).lower())
    if not m:
        return None
    total = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    return float(total + 10) if total else None


def ai_call_with_retry(label: str, fn):
    last_exc: Exception | None = None
    for attempt in range(1, AI_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= AI_RETRIES or not is_retryable(exc):
                raise
            wait = reset_after_seconds(exc) or (AI_RETRY_BASE_SECONDS * attempt)
            print(f"  -> {label} retry {attempt}/{AI_RETRIES} after {wait:.0f}s: {exc}", flush=True)
            time.sleep(wait)
    if last_exc:
        raise last_exc
    raise RuntimeError(f"{label} failed without exception")


def strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers if present."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def call_ai(client: anthropic.Anthropic, label: str, text: str) -> list[dict]:
    """Send OCR text chunk to AI, return list of parsed question dicts."""
    def _fn():
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps({"text": text}, ensure_ascii=False)}],
        )
        raw = "".join(b.text for b in resp.content if b.type == "text")
        raw = strip_markdown_fences(raw)
        return json.loads(raw)

    try:
        result = ai_call_with_retry(label, _fn)
        if not isinstance(result, list):
            print(f"  [WARN] {label}: AI returned non-list, skipping", flush=True)
            return []
        return result
    except json.JSONDecodeError as e:
        print(f"  [ERROR] {label}: json.loads failed — {e}", flush=True)
        return []
    except Exception as e:
        print(f"  [ERROR] {label}: {e}", flush=True)
        return []


def chunk_text(text: str, max_chars: int = OCR_CHUNK_MAX) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        if end < len(text):
            # try to break at newline
            nl = text.rfind("\n", start, end)
            if nl > start:
                end = nl + 1
        chunks.append(text[start:end])
        start = end
    return chunks


# ── ID generation ─────────────────────────────────────────────────────────────

def make_question_id(sha1_prefix: str, num: int, text: str) -> str:
    digest = hashlib.sha1(f"{sha1_prefix}:{num}:{text}".encode()).hexdigest()[:12]
    return f"en-practice-{sha1_prefix[:12]}-{num:03d}-{digest}"


# ── Dedup helpers ─────────────────────────────────────────────────────────────

def dedup_key(q: dict) -> str:
    text = (q.get("question_text") or q.get("question") or "").strip()
    return " ".join(text.split())[:60]


def build_existing_keys(questions: list[dict]) -> set[str]:
    return {dedup_key(q) for q in questions}


# ── Subtopic/format inference ─────────────────────────────────────────────────

def infer_subtopic_from_file(relative_path: str, question_text: str) -> tuple[str, str, list[str]]:
    path_up = relative_path.upper()
    q_up = question_text.upper()
    combined = f"{path_up} {q_up}"

    if "WORD FORMATION" in combined or "WORDFORMATION" in path_up:
        return WF_SUBTOPIC, WF_FORMAT, WF_EXAM
    if "COLLOCATION" in combined:
        return "E2X.03", "hsa_sentence_completion", ["THPT_2025_CORE"]
    if re.search(r"CLOZE|ĐỌC ĐIỀN|ĐIỀN KHUYẾT", combined):
        return "E2C.05", "hsa_cloze_text", ["THPT_2025_CORE", "HSA_ENGLISH"]
    if re.search(r"READING|ĐỌC HIỂU|PASSAGE", combined):
        return "E2R.02", "thpt_reading_passage", ["THPT_2025_CORE"]
    if re.search(r"LINEAR THINKING|TƯ DUY TUYẾN TÍNH", combined):
        return "E2R.05", "thpt_reading_passage", ["THPT_2025_CORE"]
    if re.search(r"SẮP XẾP|ARRANGEMENT", combined):
        return "E2O.02", "thpt_arrangement_text", ["THPT_2025_CORE"]
    if re.search(r"DIALOGUE", combined):
        return "E2F.01", "hsa_dialogue_completion", ["HSA_ENGLISH"]
    if re.search(r"SYNONYM|ĐỒNG NGHĨA", combined):
        return "E2X.05", "hsa_synonym", ["THPT_2025_CORE"]
    if re.search(r"ANTONYM|TRÁI NGHĨA", combined):
        return "E2X.05", "hsa_antonym", ["THPT_2025_CORE"]
    return "E2X.07", "hsa_sentence_completion", ["THPT_2025_CORE"]


# ── Build full-schema question from AI output ─────────────────────────────────

def build_question_from_ai(
    ai_q: dict,
    file_info: dict,
    sha1: str,
    seq_num: int,
    passage_text: str | None = None,
    passage_id: str | None = None,
    meta_override: dict | None = None,
) -> dict:
    raw_text  = (ai_q.get("question") or "").strip()
    options   = ai_q.get("options") or {}
    answer    = ai_q.get("answer")
    relative  = file_info.get("relative_path") or ""
    subtopic, fmt, exam_profiles = infer_subtopic_from_file(relative, raw_text)

    if meta_override:
        subtopic      = meta_override.get("knowledge_subtopic_code_v2") or subtopic
        fmt           = meta_override.get("question_format") or fmt
        exam_profiles = meta_override.get("exam_profiles") or exam_profiles
        passage_text  = meta_override.get("passage_text") or passage_text
        passage_id    = meta_override.get("passage_id") or passage_id

    q_id = make_question_id(sha1, seq_num, raw_text)

    return {
        "question_id": q_id,
        "source_file": file_info.get("file_name") or file_info.get("stem", ""),
        "relative_path": relative,
        "file_sha1": sha1,
        "question_number": seq_num,
        "page_start": None,
        "page_end": None,
        "question_text": raw_text,
        "options": options,
        "correct_answer": answer,
        "answer_source": "ai_solved" if answer else "missing",
        "explanation": "",
        "passage_id": passage_id,
        "passage_text": passage_text,
        "question_format": fmt,
        "knowledge_subtopic_code_v2": subtopic,
        "exam_profiles": exam_profiles,
        "linked_node_codes_v2": [],
        "difficulty": "basic",
        "confidence": "medium",
        "needs_review": True,
        "review_reason": "ocr_ai_enriched",
        "ready_for_ai_solve": False,
        "ai_model": "gz-prod/claude-haiku-4-5",
        "practice_item_type": "mcq" if options else "fill_blank",
        "raw_extract": None,
    }


# ── Process rejected questions ────────────────────────────────────────────────

def process_rejected(
    client: anthropic.Anthropic,
    rejected: list[dict],
    existing_keys: set[str],
    dry_run: bool = False,
) -> tuple[list[dict], dict[str, Any]]:
    """Re-parse rejected OCR questions via AI. Returns (new_questions, stats)."""
    new_questions: list[dict] = []
    stats: dict[str, Any] = {
        "files_processed": 0,
        "chunks_sent": 0,
        "ai_questions_parsed": 0,
        "new_accepted": 0,
        "dedup_skipped": 0,
        "files_failed": [],
    }

    # Group by source_file
    groups: dict[str, list[dict]] = defaultdict(list)
    for q in rejected:
        groups[q.get("source_file") or "unknown"].append(q)

    for fname, qs in groups.items():
        stats["files_processed"] += 1

        # Collect passage text from first question that has one
        passage_text = next((q.get("passage_text") for q in qs if q.get("passage_text")), None)
        passage_id   = next((q.get("passage_id") for q in qs if q.get("passage_id")), None)
        file_info = {
            "file_name": fname,
            "relative_path": qs[0].get("relative_path", ""),
        }
        sha1 = qs[0].get("file_sha1", "unknown")

        # Build text: short passage context + all raw blocks
        raw_blocks = []
        for q in qs:
            block = (q.get("raw_extract") or {}).get("block") or q.get("question_text") or ""
            if block.strip():
                raw_blocks.append(block.strip())

        passage_prefix = ""
        if passage_text:
            passage_prefix = f"CONTEXT PASSAGE:\n{passage_text[:600]}\n\n"

        full_text = passage_prefix + "QUESTIONS TO PARSE:\n" + "\n\n".join(raw_blocks)

        for chunk_idx, chunk in enumerate(chunk_text(full_text)):
            label = f"rejected/{fname[:40]}/chunk{chunk_idx+1}"
            print(f"  AI call: {label} ({len(chunk)} chars)", flush=True)
            stats["chunks_sent"] += 1

            if dry_run:
                print(f"    [DRY-RUN] skipping actual API call", flush=True)
                continue

            time.sleep(AI_REQUEST_DELAY)
            parsed = call_ai(client, label, chunk)
            stats["ai_questions_parsed"] += len(parsed)

            # Build meta_override from first rejected question of this file
            first_q = qs[0]
            meta_override = {
                "knowledge_subtopic_code_v2": first_q.get("knowledge_subtopic_code_v2"),
                "question_format": first_q.get("question_format"),
                "exam_profiles": first_q.get("exam_profiles"),
                "passage_text": passage_text,
                "passage_id": passage_id,
            }

            for seq, ai_q in enumerate(parsed, start=1):
                q_text = (ai_q.get("question") or "").strip()
                if not q_text or len(q_text) < 15:
                    continue
                dk = dedup_key(ai_q)
                if dk in existing_keys:
                    stats["dedup_skipped"] += 1
                    continue

                new_q = build_question_from_ai(
                    ai_q, file_info, sha1, seq, passage_text, passage_id, meta_override
                )
                new_questions.append(new_q)
                existing_keys.add(dk)
                stats["new_accepted"] += 1

    return new_questions, stats


# ── Process failed files ──────────────────────────────────────────────────────

def process_failed_files(
    client: anthropic.Anthropic,
    failed_files: list[dict],
    ocr_cache_dir: Path,
    existing_keys: set[str],
    dry_run: bool = False,
) -> tuple[list[dict], dict[str, Any]]:
    new_questions: list[dict] = []
    stats: dict[str, Any] = {
        "files_processed": 0,
        "chunks_sent": 0,
        "ai_questions_parsed": 0,
        "new_accepted": 0,
        "dedup_skipped": 0,
        "files_failed": [],
    }

    for entry in failed_files:
        file_info = entry.get("file", {})
        sha1 = entry.get("file_sha1", "")
        fname = file_info.get("file_name", "")
        stats["files_processed"] += 1

        cache_path = ocr_cache_dir / f"{sha1}.json"
        if not cache_path.exists():
            print(f"  [WARN] OCR cache missing: {cache_path}", flush=True)
            stats["files_failed"].append(fname)
            continue

        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        ocr_text = cache.get("text", "")
        if not ocr_text:
            stats["files_failed"].append(fname)
            continue

        print(f"  Processing failed file: {fname} ({len(ocr_text)} chars)", flush=True)

        seq = 1
        for chunk_idx, chunk in enumerate(chunk_text(ocr_text)):
            label = f"failed/{fname[:40]}/chunk{chunk_idx+1}"
            print(f"    AI call: {label} ({len(chunk)} chars)", flush=True)
            stats["chunks_sent"] += 1

            if dry_run:
                print(f"      [DRY-RUN] skipping actual API call", flush=True)
                continue

            time.sleep(AI_REQUEST_DELAY)
            parsed = call_ai(client, label, chunk)
            stats["ai_questions_parsed"] += len(parsed)

            for ai_q in parsed:
                q_text = (ai_q.get("question") or "").strip()
                if not q_text or len(q_text) < 15:
                    continue
                dk = dedup_key(ai_q)
                if dk in existing_keys:
                    stats["dedup_skipped"] += 1
                    continue
                new_q = build_question_from_ai(ai_q, file_info, sha1, seq)
                new_questions.append(new_q)
                existing_keys.add(dk)
                stats["new_accepted"] += 1
                seq += 1

    return new_questions, stats


# ── HTML report ───────────────────────────────────────────────────────────────

def build_html_report(
    rejected_stats: dict,
    failed_stats: dict,
    new_questions: list[dict],
    final_total: int,
    subtopic_coverage: dict[str, int],
    taxonomy_subtopics: list[dict],
    target: int = 60,
) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_new = len(new_questions)

    by_subtopic = Counter(q.get("knowledge_subtopic_code_v2") for q in new_questions)

    # coverage table rows
    rows = []
    for st in taxonomy_subtopics:
        code  = st["subtopic_code"]
        final = subtopic_coverage.get(code, 0)
        added = by_subtopic.get(code, 0)
        need  = max(0, target - final)
        cls   = "ok" if need == 0 else ("zero" if final == 0 else "low")
        rows.append(
            f"<tr class='{cls}'>"
            f"<td>{html.escape(code)}</td>"
            f"<td>{final - added}</td>"
            f"<td>{added}</td>"
            f"<td>{final}</td>"
            f"<td>{need}</td>"
            f"<td>{html.escape(st.get('subtopic_title',''))}</td>"
            f"</tr>"
        )

    parse_rows = ""
    by_file: dict[str, int] = defaultdict(int)
    for q in new_questions:
        by_file[q.get("source_file", "unknown")] += 1
    for fname, cnt in sorted(by_file.items(), key=lambda x: -x[1]):
        parse_rows += f"<tr><td>{html.escape(fname)}</td><td>{cnt}</td></tr>"

    # Still-failed files
    still_failed = rejected_stats.get("files_failed", []) + failed_stats.get("files_failed", [])
    sf_html = "".join(f"<li>{html.escape(f)}</li>" for f in still_failed) if still_failed else "<li>None</li>"

    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>AI Enrich Practice Report</title>
<style>
body{{font-family:Arial,sans-serif;padding:20px;max-width:1400px;margin:0 auto}}
h1{{color:#1a1a2e}} h2{{color:#16213e;border-bottom:2px solid #e0e0e0;padding-bottom:6px}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #ccc;padding:6px;font-size:13px}}
th{{background:#222;color:#fff;position:sticky;top:0}}
.ok{{background:#edf9ed}} .low{{background:#fff8d8}} .zero{{background:#ffe6e6}}
.stat-box{{display:inline-block;background:#f4f8ff;border:1px solid #c5d8f5;border-radius:8px;
  padding:12px 20px;margin:8px;text-align:center}}
.stat-num{{font-size:2em;font-weight:bold;color:#1a5276}}
.stat-label{{font-size:.85em;color:#555;margin-top:4px}}
</style></head><body>
<h1>AI Enrich Practice — Report</h1>
<p>Generated: {ts}</p>
<h2>Summary</h2>
<div>
  <div class='stat-box'><div class='stat-num'>{rejected_stats.get("files_processed",0)}</div>
    <div class='stat-label'>Rejected files processed</div></div>
  <div class='stat-box'><div class='stat-num'>{rejected_stats.get("chunks_sent",0) + failed_stats.get("chunks_sent",0)}</div>
    <div class='stat-label'>AI chunks sent</div></div>
  <div class='stat-box'><div class='stat-num'>{rejected_stats.get("ai_questions_parsed",0) + failed_stats.get("ai_questions_parsed",0)}</div>
    <div class='stat-label'>AI questions parsed</div></div>
  <div class='stat-box'><div class='stat-num'>{total_new}</div>
    <div class='stat-label'>New questions added</div></div>
  <div class='stat-box'><div class='stat-num'>{rejected_stats.get("dedup_skipped",0) + failed_stats.get("dedup_skipped",0)}</div>
    <div class='stat-label'>Dedup skipped</div></div>
  <div class='stat-box'><div class='stat-num'>{final_total}</div>
    <div class='stat-label'>Total questions (final)</div></div>
</div>

<h2>New Questions by File</h2>
<table><tr><th>File</th><th>Added</th></tr>
{parse_rows if parse_rows else "<tr><td colspan='2'>None</td></tr>"}
</table>

<h2>Subtopic Coverage</h2>
<p>Target: {target} per subtopic. Green = met, Yellow = low, Red = zero.</p>
<table>
<tr><th>Code</th><th>Before</th><th>Added</th><th>After</th><th>Still Need</th><th>Subtopic</th></tr>
{''.join(rows)}
</table>

<h2>Files Still Unparseable</h2>
<ul>{sf_html}</ul>
</body></html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--dry-run", action="store_true", help="Skip actual API calls")
    args = parser.parse_args()
    root = args.root

    client = anthropic.Anthropic(
        api_key=API_KEY,
        base_url=f"{BASE_URL}/v1",
        timeout=AI_TIMEOUT_SECONDS,
    )

    # ── Load data ─────────────────────────────────────────────────────────────
    ocr_rescan_path = root / "output_json" / "practice_questions_ocr_rescan.json"
    main_path       = root / "output_json" / "practice_questions.json"
    taxonomy_path   = root / "output_json" / "english_taxonomy_v2.json"
    ocr_cache_dir   = root / "cache" / "ocr_text_rapidocr"

    print("Loading data...", flush=True)
    ocr_rescan = json.loads(ocr_rescan_path.read_text(encoding="utf-8"))
    main_data  = json.loads(main_path.read_text(encoding="utf-8"))
    taxonomy   = json.loads(taxonomy_path.read_text(encoding="utf-8"))

    rejected   = ocr_rescan.get("rejected_questions", [])
    all_files  = ocr_rescan.get("files", [])
    failed_files = [f for f in all_files if f.get("status") == "failed"]
    existing_qs  = main_data.get("questions", [])
    taxonomy_subtopics = taxonomy.get("knowledge_subtopics", [])

    existing_keys = build_existing_keys(existing_qs)

    print(f"  Rejected questions: {len(rejected)}", flush=True)
    print(f"  Failed files: {len(failed_files)}", flush=True)
    print(f"  Existing questions: {len(existing_qs)}", flush=True)

    # ── Step 1: Process rejected questions ───────────────────────────────────
    print("\n── Step 1: Processing rejected OCR questions ──", flush=True)
    rej_new, rej_stats = process_rejected(client, rejected, existing_keys, args.dry_run)
    print(f"  New from rejected: {len(rej_new)}", flush=True)

    # ── Step 2: Process failed files ─────────────────────────────────────────
    print("\n── Step 2: Processing failed files ──", flush=True)
    fail_new, fail_stats = process_failed_files(
        client, failed_files, ocr_cache_dir, existing_keys, args.dry_run
    )
    print(f"  New from failed files: {len(fail_new)}", flush=True)

    all_new = rej_new + fail_new

    if args.dry_run:
        print("\n[DRY-RUN] No files written.", flush=True)
        print(f"Would add {len(all_new)} questions.", flush=True)
        return

    # ── Step 3: Save enriched-only artifact ──────────────────────────────────
    enriched_path = PIPELINE_ROOT / "practice_questions_ai_enriched.json"
    enriched_payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "ai_enrich_practice",
        "total_new_questions": len(all_new),
        "questions": all_new,
    }
    enriched_path.write_text(
        json.dumps(enriched_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nSaved: {enriched_path} ({len(all_new)} questions)", flush=True)

    # ── Step 4: Merge into practice_questions.json ────────────────────────────
    print("Merging into practice_questions.json...", flush=True)
    updated_questions = existing_qs + all_new
    updated = dict(main_data)
    updated["generated_at"] = datetime.now().isoformat(timespec="seconds")
    updated["total_questions"] = len(updated_questions)
    updated["questions"] = updated_questions
    updated["ocr_ai_enriched_added"] = len(all_new)

    main_path.write_text(
        json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Updated: {main_path} (total: {len(updated_questions)})", flush=True)

    # ── Step 5: Balance check ─────────────────────────────────────────────────
    subtopic_coverage: dict[str, int] = Counter(
        q.get("knowledge_subtopic_code_v2") for q in updated_questions
    )
    deficient = [
        (code, cnt)
        for code, cnt in subtopic_coverage.items()
        if cnt < 60
    ]
    deficient.sort(key=lambda x: x[1])

    print("\n── Balance check (non-backfill) ──", flush=True)
    non_bf = [q for q in updated_questions if q.get("ai_model") != "generated_backfill_v1"]
    nb_cov: dict[str, int] = Counter(q.get("knowledge_subtopic_code_v2") for q in non_bf)
    still_deficient = [(code, nb_cov.get(code, 0)) for s in taxonomy_subtopics
                       for code in [s["subtopic_code"]] if nb_cov.get(code, 0) < 60]
    still_deficient.sort(key=lambda x: x[1])
    if still_deficient:
        print(f"  Subtopics still < 60 (non-backfill): {len(still_deficient)}")
        for code, cnt in still_deficient[:10]:
            print(f"    {cnt:3d}  {code}")
        if len(still_deficient) > 10:
            print(f"    ... and {len(still_deficient)-10} more")
    else:
        print("  All subtopics >= 60.")

    # ── Step 6: HTML report ───────────────────────────────────────────────────
    report_path = PIPELINE_ROOT / "practice_ai_enrich_report.html"
    html_content = build_html_report(
        rej_stats, fail_stats, all_new,
        len(updated_questions), subtopic_coverage, taxonomy_subtopics
    )
    report_path.write_text(html_content, encoding="utf-8")
    print(f"Saved: {report_path}", flush=True)

    # ── Step 7: Terminal summary ──────────────────────────────────────────────
    print("\n" + "="*60, flush=True)
    source_counts: Counter = Counter()
    for q in updated_questions:
        if q.get("ai_model") == "generated_backfill_v1":
            source_counts["backfill"] += 1
        elif "rapidocr_extracted" in (q.get("review_reason") or ""):
            source_counts["ocr-regex"] += 1
        elif q.get("review_reason") == "ocr_ai_enriched":
            source_counts["ocr-ai"] += 1
        else:
            source_counts["text-pdf"] += 1
    total = len(updated_questions)
    print(f"Tổng câu: {total}", flush=True)
    print(
        f"text-PDF / OCR-regex / OCR-AI / backfill = "
        f"{source_counts['text-pdf']} / {source_counts['ocr-regex']} / "
        f"{source_counts['ocr-ai']} / {source_counts['backfill']}",
        flush=True,
    )
    if nb_cov:
        vals = list(nb_cov.values())
        print(
            f"Coverage subtopic (non-backfill): min={min(vals)} / max={max(vals)} / avg={sum(vals)//len(vals)}",
            flush=True,
        )
    if rej_stats.get("files_failed") or fail_stats.get("files_failed"):
        print("\nFiles still not parseable:", flush=True)
        for f in rej_stats.get("files_failed", []) + fail_stats.get("files_failed", []):
            print(f"  - {f}", flush=True)


if __name__ == "__main__":
    main()
