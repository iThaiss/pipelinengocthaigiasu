import argparse
import html
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

DEFAULT_ROOT = Path("local_curriculum_english")
MODEL = os.getenv("TAXONOMY_AI_MODEL", "gz-prod/claude-sonnet-4-6")
FALLBACK_MODEL = os.getenv("TAXONOMY_AI_FALLBACK_MODEL", "gz-prod/1m-claude-sonnet-4-6-max")
BASE_URL = os.getenv("CLAUDE_BASE_URL", "http://localhost:20128").rstrip("/")
API_KEY = os.getenv("NINEROUTER_API_KEY") or os.getenv("CLAUDE_API_KEY") or "local-9router"
TIMEOUT = float(os.getenv("TAXONOMY_AI_TIMEOUT_SECONDS", "60"))
RETRIES = int(os.getenv("TAXONOMY_AI_RETRIES", "2"))

LEGACY_MAP = {
    "EN01.01": "E2G.01",
    "EN01.02": "E2G.02",
    "EN01.03": "E2G.04",
    "EN01.04": "E2G.06",
    "EN02.01": "E2V.01",
    "EN02.02": "E2V.02",
    "EN02.03": "E2V.03",
    "EN02.04": "E2V.04",
    "EN02.05": "E2V.07",
    "EN03.01": "E2S.01",
    "EN03.02": "E2S.02",
    "EN03.03": "E2S.03",
    "EN03.04": "E2S.04",
    "EN03.05": "E2S.09",
    "EN03.06": "E2S.06",
    "EN03.07": "E2S.08",
    "EN04.01": "E2X.01",
    "EN04.02": "E2X.03",
    "EN04.03": "E2X.05",
    "EN04.04": "E2X.08",
    "EN04.05": "E2X.07",
    "EN05.01": "E2R.01",
    "EN05.02": "E2R.02",
    "EN05.03": "E2R.03",
    "EN05.04": "E2R.04",
    "EN05.05": "E2R.02",
    "EN05.06": "E2R.05",
    "EN05.07": "E2R.06",
    "EN05.08": "E2R.07",
    "EN06.01": "E2C.01",
    "EN06.02": "E2C.02",
    "EN06.03": "E2C.03",
    "EN06.04": "E2C.05",
    "EN07.01": "E2W.01",
    "EN07.02": "E2W.02",
    "EN07.03": "E2O.02",
    "EN07.04": "E2W.03",
    "EN09.01": "E2F.01",
    "EN09.02": "E2F.02",
    "EN09.03": "E2F.03",
    "EN10.04": "E2M.01",
    "EN10.99": "E2M.99",
}

RULES = [
    (r"PRESS RELEASE", "E2C.04", ["thpt_press_release_cloze"]),
    (r"ADVERTISEMENT|QUẢNG CÁO|THÔNG BÁO", "E2C.03", ["thpt_advertisement_cloze"]),
    (r"ĐỌC ĐIỀN|ĐIỀN KHUYẾT|CLOZE", "E2C.05", ["thpt_text_completion", "hsa_cloze_text", "spt_cloze"]),
    (r"TEXT COMPLETION|WINNING AND LOSING", "E2C.06", ["thpt_text_completion"]),
    (r"SẮP XẾP|ARRANGEMENT|ORDERING", "E2O.02", ["thpt_arrangement_text", "hsa_dialogue_arrangement", "spt_arrangement"]),
    (r"DIALOGUE COMPLETION", "E2F.01", ["hsa_dialogue_completion"]),
    (r"DIALOGUE", "E2O.01", ["thpt_arrangement_exchange", "hsa_dialogue_arrangement"]),
    (r"WORD FORMATION|CẤU TẠO TỪ|TỪ LOẠI", "E2X.01", ["spt_word_formation", "hsa_sentence_completion"]),
    (r"TRẬT TỰ TỪ", "E2X.02", ["hsa_sentence_completion", "thpt_press_release_cloze"]),
    (r"COLLOCATION", "E2X.03", ["hsa_sentence_completion", "spt_use_of_english"]),
    (r"BẢNG TỪ|TỪ VỰNG TRỌNG ĐIỂM|TOPIC VOCAB", "E2X.07", []),
    (r"ĐỒNG NGHĨA|TRÁI NGHĨA|SYNONYM|ANTONYM|TỪ - CỤM TỪ|TỪ/CỤM TỪ|VẬN DỤNG CAO", "E2X.05", ["thpt_reading_passage", "hsa_synonym", "hsa_antonym", "hsa_sentence_completion"]),
    (r"PARAPHRASE|PARAPHRASING", "E2R.07", ["thpt_reading_passage", "hsa_sentence_rewriting"]),
    (r"SUMMARY|TÓM TẮT|SUMMARISE|SUMMARIZES", "E2R.08", ["thpt_reading_passage", "hsa_reading_comprehension"]),
    (r"SUY LUẬN|INFERENCE|LINEAR THINKING|TƯ DUY TUYẾN TÍNH", "E2R.05", ["thpt_reading_passage", "hsa_reading_comprehension", "spt_reading"]),
    (r"ĐỌC HIỂU|READING", "E2R.02", ["thpt_reading_passage", "hsa_reading_comprehension", "spt_reading"]),
    (r"DANH ĐỘNG TỪ|ĐỘNG TỪ NGUYÊN MẪU", "E2V.07", ["thpt_press_release_cloze", "thpt_advertisement_cloze", "thpt_text_completion", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"ĐỘNG TỪ KHUYẾT THIẾU|MODAL", "E2V.06", ["thpt_text_completion", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"PHÂN TỪ", "E2V.08", ["thpt_press_release_cloze", "hsa_sentence_completion"]),
    (r"HIỆN TẠI", "E2V.01", ["thpt_press_release_cloze", "thpt_advertisement_cloze", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"QUÁ KHỨ", "E2V.02", ["thpt_press_release_cloze", "thpt_advertisement_cloze", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"TƯƠNG LAI", "E2V.03", ["thpt_press_release_cloze", "thpt_advertisement_cloze", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"PHỐI THÌ|HÒA HỢP GIỮA CÁC THÌ", "E2V.04", ["thpt_press_release_cloze", "thpt_text_completion", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"HÒA HỢP GIỮA CHỦ NGỮ|SUBJECT", "E2V.05", ["thpt_press_release_cloze", "thpt_advertisement_cloze", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"CÂU BỊ ĐỘNG", "E2S.01", ["thpt_text_completion", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"CÂU GIÁN TIẾP|REPORTED", "E2S.02", ["thpt_text_completion", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"CÂU ĐIỀU KIỆN|ĐIỀU ƯỚC|GIẢ ĐỊNH", "E2S.03", ["thpt_text_completion", "hsa_sentence_completion", "hsa_sentence_rewriting", "spt_use_of_english"]),
    (r"MENHDEQUANHE|MỆNH ĐỀ QUAN HỆ \(LÝ THUYẾT", "E2S.04", ["thpt_press_release_cloze", "thpt_text_completion", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"RÚT GỌN MỆNH ĐỀ QUAN HỆ", "E2S.05", ["thpt_text_completion", "hsa_sentence_completion", "hsa_sentence_combination", "spt_use_of_english"]),
    (r"MỆNH ĐỀ QUAN HỆ", "E2S.04", ["thpt_press_release_cloze", "thpt_text_completion", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"MỆNH ĐỀ TRẠNG NGỮ|LIÊN TỪ|TRẠNG TỪ LIÊN KẾT", "E2S.06", ["hsa_sentence_combination", "spt_use_of_english"]),
    (r"CÂU HỎI ĐUÔI", "E2S.07", ["hsa_sentence_completion", "spt_use_of_english"]),
    (r"CÂU CHẺ", "E2S.08", ["hsa_sentence_completion", "spt_use_of_english"]),
    (r"ĐẢO NGỮ", "E2S.09", ["hsa_sentence_completion", "spt_use_of_english"]),
    (r"CÁC LOẠI CÂU", "E2S.10", ["hsa_sentence_combination", "spt_use_of_english"]),
    (r"NGỮ ĐỒNG VỊ|PHÉP SONG HÀNH|YẾU TỐ CHÈN", "E2S.11", ["hsa_sentence_combination", "spt_use_of_english"]),
    (r"MẠO TỪ|TỪ HẠN ĐỊNH", "E2G.02", ["thpt_advertisement_cloze", "thpt_press_release_cloze", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"ĐẠI TỪ|LƯỢNG TỪ|TỪ CHỈ LƯỢNG", "E2G.03", ["thpt_advertisement_cloze", "thpt_press_release_cloze", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"GIỚI TỪ", "E2G.04", ["thpt_advertisement_cloze", "thpt_press_release_cloze", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"CỤM ĐỘNG TỪ|PHRASAL", "E2G.05", ["thpt_press_release_cloze", "thpt_advertisement_cloze", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"CẤP SO SÁNH", "E2G.06", ["thpt_text_completion", "hsa_sentence_completion", "spt_use_of_english"]),
    (r"VIẾT|ĐOẠN VĂN|WRITING", "E2W.03", ["spt_paragraph_writing"]),
    (r"SENTENCE REWRITING|VIẾT LẠI CÂU", "E2W.01", ["hsa_sentence_rewriting"]),
    (r"SENTENCE COMBINATION|KẾT HỢP CÂU", "E2W.02", ["hsa_sentence_combination"]),
    (r"LOGICAL|PROBLEM SOLVING|TOÁN|LẬP LUẬN", "E2M.01", ["hsa_logical_problem_solving"]),
    (r"ĐỀ TINH HOA|ĐỀ SỐ|DỰ ĐOÁN ĐẶC BIỆT", "E2M.03", ["thpt_reading_passage", "thpt_text_completion", "thpt_arrangement_text"]),
    (r"VIP90|TUẦN", "E2M.03", ["thpt_reading_passage", "thpt_text_completion"]),
]

SYSTEM_PROMPT = """Bạn là chuyên gia thiết kế taxonomy tiếng Anh cho học sinh Việt Nam học thi THPT tốt nghiệp, HSA và ĐGNL Sư phạm.
Nhiệm vụ: chọn knowledge_subtopic_code_v2 phù hợp nhất và các question_formats liên quan cho từng node học tập.
Nguyên tắc:
- THPT tốt nghiệp là lõi; HSA/SPT là bổ trợ.
- Không tạo kiến thức riêng cho từng kỳ thi nếu cùng một kiến thức.
- Bảng từ vựng/cấu trúc là learning support, không phải question format.
- Nếu mơ hồ thật sự, dùng E2M.99.
Trả JSON duy nhất dạng {"items":[{"node_code":"...","knowledge_subtopic_code_v2":"...","question_formats":["..."],"exam_profiles":["..."],"confidence":"high|medium|low","reason":"..."}]}.
"""

def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

def normalize_base_url(value: str) -> str:
    value = value.rstrip("/")
    return value[:-3] if value.endswith("/v1") else value

def extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return {}

def is_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ["429", "rate_limit", "timeout", "overloaded", "temporarily"])

def reset_after_seconds(exc: Exception) -> float | None:
    text = str(exc).lower()
    match = re.search(r"reset after\s+(?:(\d+)m\s*)?(\d+)?s?", text)
    if not match:
        return None
    total = int(match.group(1) or 0) * 60 + int(match.group(2) or 0)
    return float(total + 10) if total else None

def call_ai(client: anthropic.Anthropic, items: list[dict[str, Any]], taxonomy: dict[str, Any], model: str) -> list[dict[str, Any]]:
    catalog = [
        {"code": s["subtopic_code"], "title": s["subtopic_title"], "topic": s["topic_title"]}
        for s in taxonomy["knowledge_subtopics"]
    ]
    formats = [f["format_code"] for f in taxonomy["question_formats"]]
    payload = {
        "knowledge_subtopics": catalog,
        "question_formats": formats,
        "exam_profiles": [b["exam_profile"] for b in taxonomy["exam_blueprints"]],
        "items": items,
    }
    for attempt in range(1, RETRIES + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=4000,
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            data = extract_json(text)
            return data.get("items", []) if isinstance(data.get("items"), list) else []
        except Exception as exc:
            if attempt >= RETRIES or not is_retryable(exc):
                raise
            wait = reset_after_seconds(exc) or (8 * attempt)
            print(f"AI retry {attempt}/{RETRIES} after {wait:.0f}s: {exc}", flush=True)
            time.sleep(wait)
    return []

def infer_exam_profiles(text: str, formats: list[str], node: dict[str, Any]) -> list[str]:
    profiles = set()
    up = text.upper()
    if "VIP90" in up or "VIP90" in node.get("folder_path", "").upper():
        profiles.add("THPT_2025_CORE")
    if "ĐÁNH GIÁ NĂNG LỰC" in up or "DGNL" in up or "HSA" in up:
        profiles.add("HSA_ENGLISH")
    if "SƯ PHẠM" in up or "SPT" in up:
        profiles.add("SPT_ENGLISH")
    for fmt in formats:
        if fmt.startswith("thpt_"):
            profiles.add("THPT_2025_CORE")
        if fmt.startswith("hsa_"):
            profiles.add("HSA_ENGLISH")
        if fmt.startswith("spt_"):
            profiles.add("SPT_ENGLISH")
    return sorted(profiles) or ["THPT_2025_CORE"]

def rule_remap(node: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(str(node.get(k, "")) for k in ["node_title", "file_name", "folder_path", "lesson_summary", "concepts"]).upper()
    for pattern, code, formats in RULES:
        if re.search(pattern, text):
            return {
                "knowledge_subtopic_code_v2": code,
                "question_formats": formats,
                "exam_profiles": infer_exam_profiles(text, formats, node),
                "confidence": "high" if code != "E2M.99" else "low",
                "reason": f"Rule matched {pattern}.",
            }
    legacy = node.get("knowledge_subtopic_code")
    code = LEGACY_MAP.get(legacy, "E2M.99")
    return {
        "knowledge_subtopic_code_v2": code,
        "question_formats": [],
        "exam_profiles": infer_exam_profiles(text, [], node),
        "confidence": "medium" if code != "E2M.99" else "low",
        "reason": f"Mapped from legacy code {legacy}.",
    }

def slugify(text: str) -> str:
    text = (text or "").lower()
    replacements = {"à":"a","á":"a","ả":"a","ã":"a","ạ":"a","ă":"a","ắ":"a","ặ":"a","ằ":"a","ẳ":"a","ẵ":"a","â":"a","ấ":"a","ầ":"a","ẩ":"a","ẫ":"a","ậ":"a","đ":"d","è":"e","é":"e","ẻ":"e","ẽ":"e","ẹ":"e","ê":"e","ế":"e","ề":"e","ể":"e","ễ":"e","ệ":"e","ì":"i","í":"i","ỉ":"i","ĩ":"i","ị":"i","ò":"o","ó":"o","ỏ":"o","õ":"o","ọ":"o","ô":"o","ố":"o","ồ":"o","ổ":"o","ỗ":"o","ộ":"o","ơ":"o","ớ":"o","ờ":"o","ở":"o","ỡ":"o","ợ":"o","ù":"u","ú":"u","ủ":"u","ũ":"u","ụ":"u","ư":"u","ứ":"u","ừ":"u","ử":"u","ữ":"u","ự":"u","ỳ":"y","ý":"y","ỷ":"y","ỹ":"y","ỵ":"y"}
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text.strip())
    return re.sub(r"-+", "-", text)[:60].strip("-")

def apply_remap(nodes: list[dict[str, Any]], decisions: dict[str, dict[str, Any]], valid_codes: set[str]) -> None:
    seen: dict[str, int] = {}
    for node in nodes:
        legacy = node.get("knowledge_subtopic_code")
        node["legacy_subtopic_code"] = legacy
        node["taxonomy_version"] = "english_taxonomy_v2"
        decision = decisions.get(node.get("node_code")) or rule_remap(node)
        code = decision.get("knowledge_subtopic_code_v2")
        if code not in valid_codes:
            code = "E2M.99"
        node["knowledge_subtopic_code_v2"] = code
        node["question_formats"] = sorted(set(decision.get("question_formats") or []))
        node["exam_profiles"] = sorted(set(decision.get("exam_profiles") or infer_exam_profiles("", node["question_formats"], node)))
        node["taxonomy_v2_confidence"] = decision.get("confidence", "medium")
        node["taxonomy_v2_review_reason"] = decision.get("reason", "")
        base = f"{code}-{slugify(node.get('node_title') or node.get('file_name') or 'node')}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        node["node_code_v2"] = base if count == 1 else f"{base}-{count}"

def write_preview(nodes: list[dict[str, Any]], taxonomy: dict[str, Any], path: Path) -> None:
    subtopic_title = {s["subtopic_code"]: s["subtopic_title"] for s in taxonomy["knowledge_subtopics"]}
    rows = []
    for node in nodes:
        code = node.get("knowledge_subtopic_code_v2", "")
        rows.append(
            "<tr>"
            f"<td>{html.escape(code)}</td>"
            f"<td>{html.escape(subtopic_title.get(code, ''))}</td>"
            f"<td>{html.escape(node.get('legacy_subtopic_code') or '')}</td>"
            f"<td><b>{html.escape(node.get('node_title') or '')}</b></td>"
            f"<td>{html.escape(', '.join(node.get('question_formats') or []))}</td>"
            f"<td>{html.escape(', '.join(node.get('exam_profiles') or []))}</td>"
            f"<td>{html.escape(node.get('taxonomy_v2_confidence') or '')}</td>"
            f"<td>{html.escape((node.get('taxonomy_v2_review_reason') or '')[:140])}</td>"
            f"<td>{html.escape(node.get('file_name') or '')}</td>"
            "</tr>"
        )
    counts = Counter(node.get("knowledge_subtopic_code_v2") for node in nodes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Taxonomy V2 Remap Review</title>
<style>body{{font-family:sans-serif;font-size:12px;padding:20px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:5px 8px;vertical-align:top}}th{{background:#333;color:white;position:sticky;top:0}}.sum{{padding:12px;background:#f0f0f0;margin-bottom:16px}}</style></head>
<body><h2>Taxonomy V2 Remap Review</h2><div class="sum">Nodes: <b>{len(nodes)}</b> | V2 codes: <b>{len(counts)}</b> | Review/mixed: <b>{counts.get('E2M.99', 0)}</b></div>
<table><tr><th>V2 Code</th><th>V2 Subtopic</th><th>Legacy</th><th>Node</th><th>Question formats</th><th>Exam profiles</th><th>Confidence</th><th>Reason</th><th>File</th></tr>{''.join(rows)}</table></body></html>""",
        encoding="utf-8",
    )

def write_coverage(nodes: list[dict[str, Any]], taxonomy: dict[str, Any], path: Path) -> None:
    coverage = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_nodes": len(nodes),
        "by_v2_subtopic": Counter(node.get("knowledge_subtopic_code_v2") for node in nodes),
        "by_legacy_subtopic": Counter(node.get("legacy_subtopic_code") for node in nodes),
        "by_question_format": Counter(fmt for node in nodes for fmt in (node.get("question_formats") or [])),
        "by_exam_profile": Counter(profile for node in nodes for profile in (node.get("exam_profiles") or [])),
        "review_nodes": [
            {"node_code_v2": n.get("node_code_v2"), "title": n.get("node_title"), "reason": n.get("taxonomy_v2_review_reason")}
            for n in nodes if n.get("knowledge_subtopic_code_v2") == "E2M.99" or n.get("taxonomy_v2_confidence") == "low"
        ],
    }
    serializable = json.loads(json.dumps(coverage, ensure_ascii=False, default=dict))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")

def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Remap learning map nodes to English taxonomy v2.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--ai", action="store_true", help="Use 9Router AI to audit ambiguous/VIP90 nodes.")
    parser.add_argument("--ai-limit", type=int, default=40)
    args = parser.parse_args()

    root = args.root
    taxonomy_path = root / "output_json" / "english_taxonomy_v2.json"
    nodes_path = root / "output_json" / "learning_map_nodes.json"
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    payload = json.loads(nodes_path.read_text(encoding="utf-8"))
    nodes = payload["nodes"]
    valid_codes = {s["subtopic_code"] for s in taxonomy["knowledge_subtopics"]}

    decisions = {node.get("node_code"): rule_remap(node) for node in nodes}

    if args.ai:
        candidates = [
            node for node in nodes
            if node.get("rewrite_method") == "codex_vip90_bundle_scan"
            or decisions[node.get("node_code")]["knowledge_subtopic_code_v2"] in {"E2M.99", "E2M.03", "E2M.01"}
            or node.get("taxonomy_v2_confidence") == "low"
        ][: args.ai_limit]
        if candidates:
            client = anthropic.Anthropic(api_key=API_KEY, base_url=f"{normalize_base_url(BASE_URL)}/v1", timeout=TIMEOUT)
            items = [
                {
                    "node_code": node.get("node_code"),
                    "node_title": node.get("node_title"),
                    "legacy_subtopic_code": node.get("knowledge_subtopic_code"),
                    "folder_path": node.get("folder_path"),
                    "file_name": node.get("file_name"),
                    "concepts": node.get("concepts", [])[:6],
                    "lesson_summary": (node.get("lesson_summary") or "")[:500],
                    "theory_excerpt": (node.get("theory_content") or "")[:900],
                }
                for node in candidates
            ]
            try:
                ai_items = call_ai(client, items, taxonomy, MODEL)
            except Exception as exc:
                print(f"Primary AI model failed: {exc}", file=sys.stderr)
                try:
                    ai_items = call_ai(client, items, taxonomy, FALLBACK_MODEL)
                except Exception as fallback_exc:
                    print(f"Fallback AI model failed: {fallback_exc}", file=sys.stderr)
                    ai_items = []
            for item in ai_items:
                node_code = item.get("node_code")
                if not node_code:
                    continue
                decisions[node_code] = {
                    "knowledge_subtopic_code_v2": item.get("knowledge_subtopic_code_v2"),
                    "question_formats": item.get("question_formats") or [],
                    "exam_profiles": item.get("exam_profiles") or [],
                    "confidence": item.get("confidence", "medium"),
                    "reason": "9Router AI audit: " + str(item.get("reason", "")),
                }
            print(f"AI audited {len(ai_items)} / {len(candidates)} candidate nodes")

    apply_remap(nodes, decisions, valid_codes)
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    payload["taxonomy_version"] = "english_taxonomy_v2"
    nodes_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    write_preview(nodes, taxonomy, root / "previews" / "taxonomy_v2_remap_review.html")
    write_coverage(nodes, taxonomy, root / "output_json" / "taxonomy_v2_coverage.json")

    counts = Counter(node.get("knowledge_subtopic_code_v2") for node in nodes)
    dup_count = sum(1 for _, count in Counter(node.get("node_code_v2") for node in nodes).items() if count > 1)
    print(f"Nodes: {len(nodes)}")
    print(f"V2 subtopics used: {len(counts)}")
    print(f"Review/mixed nodes: {counts.get('E2M.99', 0)}")
    print(f"Duplicate node_code_v2: {dup_count}")
    print("Top V2 subtopics:")
    for code, count in counts.most_common(20):
        print(f"  {code}: {count}")

if __name__ == "__main__":
    main()
