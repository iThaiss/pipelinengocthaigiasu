"""Build QA HTML reports for English practice extraction outputs."""
from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path("local_curriculum_english")

FORMAT_LABELS = {
    "hsa_sentence_completion": "Câu lẻ điền/chọn đáp án (grammar/vocab)",
    "hsa_cloze_text": "Đọc điền khuyết đoạn văn",
    "spt_word_formation": "Từ loại / cấu tạo từ",
    "thpt_reading_passage": "Đọc hiểu có passage",
    "hsa_synonym": "Đồng nghĩa",
    "hsa_antonym": "Trái nghĩa",
    "hsa_dialogue_arrangement": "Sắp xếp hội thoại",
    "thpt_arrangement_text": "Sắp xếp đoạn/thư/email",
    "hsa_sentence_rewriting": "Viết lại câu",
    "spt_paragraph_writing": "Viết đoạn văn",
    "hsa_dialogue_completion": "Hoàn thành hội thoại",
}

FORMAT_EXPECTATIONS = {
    "hsa_sentence_completion": "Mỗi câu phải có đề/stem và options. Không cần passage.",
    "hsa_cloze_text": "Phải có passage/context chứa các blank và các câu hỏi bên dưới.",
    "spt_word_formation": "Mỗi câu cần stem rõ; options có thể là từ/cụm từ hoặc dạng biến đổi từ.",
    "thpt_reading_passage": "Phải có passage và nhiều câu hỏi dùng chung passage_id.",
    "hsa_synonym": "Câu hỏi phải có từ/cụm từ cần tìm nghĩa, kèm options.",
    "hsa_antonym": "Câu hỏi phải có từ/cụm từ cần tìm trái nghĩa, kèm options.",
    "hsa_dialogue_arrangement": "Question text phải chứa các phát ngôn a/b/c..., options là thứ tự sắp xếp.",
    "thpt_arrangement_text": "Question text phải chứa các câu/đoạn cần sắp xếp, options là thứ tự.",
    "hsa_sentence_rewriting": "Phải có câu gốc/yêu cầu viết lại. Có thể không có passage.",
    "spt_paragraph_writing": "Phải có prompt viết đoạn. Thường không có options.",
    "hsa_dialogue_completion": "Phải có hội thoại/context và options để hoàn thành.",
}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def folder_topic(path: str) -> str:
    parts = path.split("/")
    if len(parts) >= 3:
        return "/".join(parts[:3])
    return "/".join(parts[:-1]) or "unknown"


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def render_question(q: dict[str, Any], show_file: bool = True) -> str:
    opts = " | ".join(f"{k}. {v}" for k, v in (q.get("options") or {}).items())
    passage = q.get("passage_text") or ""
    passage_html = ""
    if passage:
        passage_html = f"<details><summary>Passage ({len(passage)} chars)</summary><div class='passage'>{esc(passage[:1400])}{'...' if len(passage) > 1400 else ''}</div></details>"
    file_html = f"<div class='file'>{esc(q.get('relative_path'))}</div>" if show_file else ""
    rejected = q.get("rejected_reason") or ""
    rejected_html = f"<div class='bad'>Lý do bị loại: {esc(rejected)}</div>" if rejected else ""
    return f"""
    <div class="qcard">
      {file_html}
      <div><b>Câu {esc(q.get('question_number'))}</b> <span class="muted">format={esc(q.get('question_format'))} | subtopic={esc(q.get('knowledge_subtopic_code_v2'))} | đủ để AI giải={esc(q.get('ready_for_ai_solve'))}</span></div>
      {passage_html}
      <div class="qtext">{esc((q.get('question_text') or '')[:700])}</div>
      <div class="opts">{esc(opts[:900])}</div>
      <div class="muted">đáp án={esc(q.get('correct_answer'))} | nguồn đáp án={esc(q.get('answer_source'))} | ghi chú={esc(q.get('review_reason'))}</div>
      {rejected_html}
    </div>
    """


def render_format_section(fmt: str, accepted: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> str:
    files = Counter(q.get("relative_path") for q in accepted)
    subtopics = Counter(q.get("knowledge_subtopic_code_v2") for q in accepted)
    reject_reasons = Counter(q.get("rejected_reason") for q in rejected)
    passage_count = sum(1 for q in accepted if q.get("passage_id"))
    accepted_samples = "".join(render_question(q) for q in accepted[:6]) or "<div class='empty'>No accepted samples</div>"
    rejected_samples = "".join(render_question(q) for q in rejected[:6]) or "<div class='empty'>No rejected samples</div>"
    label = FORMAT_LABELS.get(fmt, fmt)
    expectation = FORMAT_EXPECTATIONS.get(fmt, "Kiểm tra câu hỏi có đủ đề, options/context và gắn đúng taxonomy hay không.")
    quality = "OK để solve" if accepted and len(rejected) <= len(accepted) else "Cần xem kỹ"
    return f"""
    <section>
      <h2>{esc(label)}</h2>
      <div class="code">format_code: {esc(fmt)}</div>
      <div class="explain"><b>Format này nên trông như thế nào:</b> {esc(expectation)}</div>
      <div class="stats">
        <b>Kết luận nhanh</b>: {esc(quality)}<br>
        <b>Câu sạch giữ lại</b>: {len(accepted)} &nbsp; <b>Câu bị loại</b>: {len(rejected)} &nbsp; <b>Câu có passage</b>: {passage_count}<br>
        <b>Subtopic được gắn nhiều nhất</b>: {esc(', '.join(f'{k}:{v}' for k, v in subtopics.most_common(8)) or 'none')}<br>
        <b>Lý do loại nhiều nhất</b>: {esc(', '.join(f'{k}:{v}' for k, v in reject_reasons.most_common(6)) or 'none')}<br>
        <b>File đóng góp nhiều nhất</b>: {esc(', '.join(f'{Path(k or '').name}:{v}' for k, v in files.most_common(5)) or 'none')}
      </div>
      <h3>Câu sạch mẫu (đây là câu sẽ vào database chính)</h3>
      <div class="grid">{accepted_samples}</div>
      <h3>Câu bị loại mẫu (không đưa vào database chính)</h3>
      <div class="grid rejected">{rejected_samples}</div>
    </section>
    """


def render_folder_section(topic: str, accepted: list[dict[str, Any]], rejected: list[dict[str, Any]], file_status: dict[str, str]) -> str:
    formats = Counter(q.get("question_format") for q in accepted)
    subtopics = Counter(q.get("knowledge_subtopic_code_v2") for q in accepted)
    reject_reasons = Counter(q.get("rejected_reason") for q in rejected)
    files = sorted({q.get("relative_path") for q in accepted + rejected if q.get("relative_path")})
    failed = [f for f in files if file_status.get(f) == "failed"]
    samples = "".join(render_question(q, show_file=False) for q in accepted[:4]) or "<div class='empty'>No accepted samples</div>"
    rejected_samples = "".join(render_question(q, show_file=False) for q in rejected[:4]) or "<div class='empty'>No rejected samples</div>"
    return f"""
    <section>
      <h2>{esc(topic)}</h2>
      <div class="stats">
        <b>files</b>: {len(files)} &nbsp; <b>failed files</b>: {len(failed)} &nbsp; <b>accepted</b>: {len(accepted)} &nbsp; <b>rejected</b>: {len(rejected)}<br>
        <b>formats</b>: {esc(', '.join(f'{k}:{v}' for k, v in formats.most_common(8)))}<br>
        <b>subtopics</b>: {esc(', '.join(f'{k}:{v}' for k, v in subtopics.most_common(8)))}<br>
        <b>reject reasons</b>: {esc(', '.join(f'{k}:{v}' for k, v in reject_reasons.most_common(6)))}
      </div>
      <h3>Accepted Samples</h3><div class="grid">{samples}</div>
      <h3>Rejected Samples</h3><div class="grid rejected">{rejected_samples}</div>
    </section>
    """


def page(title: str, summary: str, body: str) -> str:
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>
body{{font-family:Arial,sans-serif;font-size:13px;line-height:1.45;padding:20px;color:#222}}
h1{{margin-top:0}} h2{{border-top:3px solid #222;padding-top:14px;margin-top:28px}} h3{{margin-bottom:8px}}
.summary,.stats,.explain{{background:#f2f2f2;border:1px solid #ddd;padding:10px;margin:10px 0}} .explain{{background:#eef6ff}} .code{{font-family:monospace;color:#555;margin-top:-8px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:10px}}
.qcard{{border:1px solid #ccc;padding:10px;background:#fff}}
.rejected .qcard{{background:#fff9f1;border-color:#e0b36a}}
.file{{font-size:11px;color:#666;margin-bottom:4px;word-break:break-all}}
.qtext{{margin:8px 0;font-weight:500}} .opts{{color:#333;margin:6px 0}} .muted{{font-size:11px;color:#666}} .bad{{color:#9a4b00;font-size:12px;margin-top:6px}}
.passage{{background:#f8f8f8;border-left:3px solid #999;padding:8px;margin:6px 0}}
.empty{{color:#777;font-style:italic}}
</style></head><body><h1>{esc(title)}</h1><div class="summary">{summary}</div>{body}</body></html>"""

def render_document_segment(seg: dict[str, Any]) -> str:
    context = seg.get("context_text") or ""
    context_html = f"<div class='doc-context'>{esc(context[:2500])}{'...' if len(context) > 2500 else ''}</div>" if context else ""
    questions = []
    for block in seg.get("question_blocks", [])[:12]:
        questions.append(f"<pre class='doc-question'>{esc(block[:1800])}{'...' if len(block) > 1800 else ''}</pre>")
    if seg.get("question_count", 0) > 12:
        questions.append(f"<div class='muted'>... còn {seg.get('question_count', 0) - 12} câu trong segment này</div>")
    qnums = ", ".join(str(n) for n in seg.get("question_numbers", []))
    return f"""
    <section class="doc-segment">
      <div class="doc-meta">{esc(seg.get('segment_type'))} | câu: {esc(qnums or seg.get('question_count'))} | trang {esc(seg.get('page_start'))}-{esc(seg.get('page_end'))} | {esc(seg.get('confidence'))}</div>
      {context_html}
      {''.join(questions)}
    </section>
    """

def write_topic_document_preview(root: Path) -> None:
    segments_path = root / "output_json" / "practice_segments.json"
    questions_path = root / "output_json" / "practice_questions.json"
    if not segments_path.exists():
        return
    segments_data = json.loads(segments_path.read_text(encoding="utf-8"))
    questions_data = json.loads(questions_path.read_text(encoding="utf-8")) if questions_path.exists() else {}
    files = segments_data.get("files", [])
    by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in files:
        rel = item.get("file", {}).get("relative_path") or ""
        by_topic[folder_topic(rel)].append(item)

    topic_sections = []
    for topic in sorted(by_topic):
        file_sections = []
        for item in by_topic[topic]:
            file_info = item.get("file", {})
            rel = file_info.get("relative_path") or ""
            segs = item.get("segments", [])
            qblocks = sum(seg.get("question_count", 0) for seg in segs)
            segment_html = "".join(render_document_segment(seg) for seg in segs[:8])
            if len(segs) > 8:
                segment_html += f"<div class='muted'>... còn {len(segs) - 8} segment trong file này</div>"
            file_sections.append(
                f"<details><summary><b>{esc(file_info.get('file_name') or Path(rel).name)}</b> "
                f"<span class='muted'>segments={len(segs)} | question_blocks={qblocks} | status={esc(item.get('status'))}</span></summary>"
                f"<div class='file-path'>{esc(rel)}</div>{segment_html}</details>"
            )
        topic_sections.append(
            f"<section><h2>{esc(topic)}</h2><div class='stats'>files={len(by_topic[topic])}</div>{''.join(file_sections)}</section>"
        )

    summary = (
        "Preview này nhóm theo topic/folder rồi theo file, dùng segment raw để nhìn gần với tài liệu gốc hơn. "
        "Mục tiêu là kiểm context bị cắt sai, câu bị dính sang passage khác, hoặc file nào không có question blocks.<br>"
        f"Files: <b>{len(files)}</b> | Segments: <b>{segments_data.get('total_segments')}</b> | "
        f"Question blocks: <b>{segments_data.get('total_question_blocks')}</b> | "
        f"Accepted questions hiện tại: <b>{questions_data.get('total_questions', 'n/a')}</b> | "
        f"Rejected: <b>{questions_data.get('total_rejected_questions', 'n/a')}</b>"
    )
    content = page("Practice Topic Document Preview", summary, "".join(topic_sections))
    content = content.replace(
        "</style>",
        """
.doc-segment{border:1px solid #ddd;background:#fff;margin:10px 0;padding:10px}
.doc-meta{font-size:11px;color:#555;margin-bottom:8px;font-family:monospace}
.doc-context{white-space:pre-wrap;background:#f8f8f8;border-left:4px solid #555;padding:10px;margin:8px 0}
.doc-question{white-space:pre-wrap;font-family:Arial,sans-serif;background:#fff;border-top:1px solid #eee;margin:0;padding:10px 0;font-size:13px;line-height:1.45}
.file-path{font-size:11px;color:#666;margin:8px 0;word-break:break-all}
</style>""",
    )
    out_path = root / "previews" / "practice_topic_document_preview.html"
    out_path.write_text(content, encoding="utf-8")
    print(f"Saved: {out_path}")


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    root = args.root
    data = json.loads((root / "output_json" / "practice_questions.json").read_text(encoding="utf-8"))
    accepted = data.get("questions", [])
    rejected = data.get("rejected_questions", [])
    files = data.get("files", [])
    file_status = {f.get("file", {}).get("relative_path"): f.get("status") for f in files}

    by_format_a: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_format_r: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for q in accepted:
        by_format_a[q.get("question_format") or "unknown"].append(q)
    for q in rejected:
        by_format_r[q.get("question_format") or "unknown"].append(q)
    formats = sorted(set(by_format_a) | set(by_format_r))

    fmt_body = "".join(render_format_section(fmt, by_format_a.get(fmt, []), by_format_r.get(fmt, [])) for fmt in formats)
    summary = (
        "Cách đọc: mỗi mục bên dưới là một dạng câu hỏi. "
        "<b>Câu sạch giữ lại</b> là câu đủ đề/context/options để đưa vào database và AI solve. "
        "<b>Câu bị loại</b> là câu scan ra nhưng thiếu dữ liệu, không đưa vào database chính.<br>"
        f"Câu sạch: <b>{len(accepted)}</b> | Câu bị loại: <b>{len(rejected)}</b> | "
        f"Số dạng câu hỏi: <b>{len(formats)}</b> | Passage groups: <b>{len(data.get('passages', []))}</b>"
    )
    fmt_path = root / "previews" / "practice_format_qa.html"
    fmt_path.write_text(page("Practice Format QA", summary, fmt_body), encoding="utf-8")

    by_folder_a: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_folder_r: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for q in accepted:
        by_folder_a[folder_topic(q.get("relative_path") or "")].append(q)
    for q in rejected:
        by_folder_r[folder_topic(q.get("relative_path") or "")].append(q)
    folders = sorted(set(by_folder_a) | set(by_folder_r))
    folder_body = "".join(render_folder_section(folder, by_folder_a.get(folder, []), by_folder_r.get(folder, []), file_status) for folder in folders)
    folder_path = root / "previews" / "practice_folder_qa.html"
    folder_path.write_text(page("Practice Folder QA", summary, folder_body), encoding="utf-8")
    write_topic_document_preview(root)

    print(f"Saved: {fmt_path}")
    print(f"Saved: {folder_path}")


if __name__ == "__main__":
    main()
