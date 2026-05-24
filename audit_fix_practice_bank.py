from __future__ import annotations

import argparse, html, json, re, sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import scan_practice_meta as sp

ROOT = Path('local_curriculum_english')
PASSAGE_FORMATS = set(sp.PASSAGE_FORMATS)
NO_OPTION_FORMATS = set(sp.NO_OPTION_FORMATS)
NO_OPTION_ITEM_TYPES = set(sp.NO_OPTION_ITEM_TYPES)

CLOZE_PREFIX_RE = re.compile(r'^\s*Read\s+the\s+following\s+passage.*?(?:from\s+\d+\s+to\s+\d+|from\s+\d+\s*-\s*\d+|numbered\s+blanks?\s+from\s+\d+\s+to\s+\d+)\s*\.\s*', re.I | re.S)
BLANK_RE = re.compile(r'\((\d+)\)\s*_+')
CONTEXT_VOCAB_RE = re.compile(r'\bin the (?:text|passage|paragraph)\b|closest in meaning|opposite in meaning|refers to', re.I)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def dump_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def split_bits(value: Any) -> list[str]:
    return [x for x in str(value or '').split('; ') if x]


def add_reason(q: dict[str, Any], reason: str) -> None:
    bits = split_bits(q.get('review_reason'))
    bits.append(reason)
    q['review_reason'] = '; '.join(sorted(set(bits)))


def option_values(q: dict[str, Any]) -> list[str]:
    opts = q.get('options') if isinstance(q.get('options'), dict) else {}
    return [str(v).strip() for v in opts.values() if str(v).strip()]


def infer_blank_number(q: dict[str, Any], text: str) -> str | None:
    nums = BLANK_RE.findall(text)
    qnum = str(q.get('question_number') or '')
    if qnum and qnum in nums:
        return qnum
    # For extracted cloze questions, question_number is usually the blank number.
    if qnum and re.fullmatch(r'\d+', qnum):
        return qnum
    if len(nums) == 1:
        return nums[0]
    return None


def fix_cloze(q: dict[str, Any], issues: list[str]) -> bool:
    if q.get('question_format') != 'hsa_cloze_text':
        return False
    text = str(q.get('question_text') or '').strip()
    if not text:
        return False
    has_passage_prefix = bool(re.search(r'^\s*Read\s+the\s+following\s+passage', text, re.I))
    many_blanks = len(BLANK_RE.findall(text)) >= 2
    blank = infer_blank_number(q, text)
    changed = False
    if has_passage_prefix or many_blanks:
        if not q.get('passage_text'):
            q['passage_text'] = CLOZE_PREFIX_RE.sub('', text).strip()
        if blank:
            q['question_text'] = f'Choose the option that best fits blank ({blank}) in the passage.'
            add_reason(q, 'cloze_passage_moved_to_context')
            changed = True
        else:
            issues.append('ambiguous_blank_number')
    elif not re.search(r'blank\s*\(?\d+\)?|\(\d+\)', text, re.I):
        if blank:
            q['question_text'] = f'Choose the option that best fits blank ({blank}) in the passage.'
            add_reason(q, 'cloze_blank_number_normalized')
            changed = True
        else:
            issues.append('ambiguous_blank_number')
    return changed


def audit_question(q: dict[str, Any], apply: bool) -> tuple[list[str], bool]:
    issues: list[str] = []
    changed = False
    fmt = q.get('question_format') or ''
    text = str(q.get('question_text') or '').strip()
    opts = q.get('options') if isinstance(q.get('options'), dict) else {}
    vals = option_values(q)
    item_type = q.get('practice_item_type') or (q.get('raw_extract') or {}).get('practice_item_type')

    if apply and fix_cloze(q, issues):
        changed = True
        text = str(q.get('question_text') or '').strip()

    if len(text) < 20:
        issues.append('short_question_text')
    if fmt in PASSAGE_FORMATS and q.get('knowledge_subtopic_code_v2') not in sp.OPTIONAL_CONTEXT_SUBTOPICS and not q.get('passage_text'):
        issues.append('missing_context')
    if fmt not in NO_OPTION_FORMATS and item_type not in NO_OPTION_ITEM_TYPES and len(opts) < 2:
        issues.append('missing_options')
    if vals and len(vals) != len(set(vals)):
        issues.append('duplicate_options')
    if fmt == 'hsa_sentence_completion' and len(BLANK_RE.findall(text)) >= 2:
        issues.append('ambiguous_blank_number')
    if fmt == 'hsa_sentence_completion' and item_type in {'open_response', 'error_correction'} and len(opts) < 2:
        issues.append('missing_options')
    if vals and any(re.search(r'\b(?:within its|following its|like|as|of|to|for|with|from|that|which|when|because|although)$', v, re.I) for v in vals):
        issues.append('truncated_options')
    if fmt in {'hsa_synonym', 'hsa_antonym'} and CONTEXT_VOCAB_RE.search(text) and not q.get('passage_text'):
        issues.append('context_vocab_missing_context')
    if fmt == 'hsa_cloze_text' and re.search(r'^\s*Read\s+the\s+following\s+passage', text, re.I):
        issues.append('cloze_passage_in_question_text')
    if fmt == 'hsa_cloze_text' and q.get('passage_text'):
        m = re.search(r'blank\s*\((\d+)\)|\((\d+)\)', text, re.I)
        blank_no = (m.group(1) or m.group(2)) if m else None
        if blank_no and f'({blank_no})' not in str(q.get('passage_text') or ''):
            issues.append('cloze_blank_not_in_passage')
    if fmt == 'thpt_reading_passage' and item_type == 'true_false' and len(opts) < 2:
        issues.append('reading_true_false_not_structured')
    if fmt == 'thpt_reading_passage' and q.get('passage_text'):
        passage = str(q.get('passage_text') or '').lower()
        words = [w.lower() for w in re.findall(r'[A-Za-z]{5,}', text) if w.lower() not in {'which','following','paragraph','according','passage','question','mostly','meaning','underlined','sentence'}]
        hits = sum(1 for w in words[:10] if w in passage)
        if words and hits == 0:
            # Strong mismatch signal: specific content words in the question are absent from the attached passage.
            issues.append('possible_wrong_passage_pairing')
    if not q.get('knowledge_subtopic_code_v2'):
        issues.append('missing_subtopic')
    if not fmt:
        issues.append('missing_format')

    ready = not any(i in issues for i in {
        'short_question_text','missing_context','missing_options','ambiguous_blank_number',
        'context_vocab_missing_context','cloze_passage_in_question_text','possible_wrong_passage_pairing',
        'cloze_blank_not_in_passage','reading_true_false_not_structured',
        'truncated_options'
    })
    if 'duplicate_options' in issues:
        ready = False
    if apply:
        old_ready = q.get('ready_for_ai_solve')
        old_needs = q.get('needs_review')
        q['ready_for_ai_solve'] = ready
        if issues:
            q['needs_review'] = True
            for issue in issues:
                add_reason(q, issue)
        elif q.get('answer_source') != 'missing':
            # keep existing review flags unless already solved cleanly
            q['needs_review'] = bool(q.get('needs_review'))
        changed = changed or old_ready != q.get('ready_for_ai_solve') or old_needs != q.get('needs_review')
        if issues:
            q['quality_issues'] = sorted(set(issues))
        else:
            q.pop('quality_issues', None)
    return sorted(set(issues)), changed


def render_report(path: Path, audit: dict[str, Any], samples: dict[str, list[dict[str, Any]]]) -> None:
    sections=[]
    for issue, rows in sorted(samples.items(), key=lambda kv: (-audit['issue_counts'].get(kv[0],0), kv[0])):
        cards=[]
        for q in rows[:8]:
            opts=' | '.join(f'{k}. {v}' for k,v in (q.get('options') or {}).items())
            passage=q.get('passage_text') or ''
            cards.append(f"""
<div class='card'>
<div class='meta'>{html.escape(str(q.get('relative_path')))}<br>{html.escape(str(q.get('knowledge_subtopic_code_v2')))} / {html.escape(str(q.get('question_format')))} / ready={html.escape(str(q.get('ready_for_ai_solve')))}</div>
<div><b>Q{html.escape(str(q.get('question_number')))}</b> {html.escape((q.get('question_text') or '')[:700])}</div>
<div class='opts'>{html.escape(opts[:900])}</div>
{f"<details><summary>Passage ({len(passage)} chars)</summary><pre>{html.escape(passage[:1800])}</pre></details>" if passage else ''}
<div class='reason'>{html.escape(str(q.get('review_reason') or ''))}</div>
</div>""")
        sections.append(f"<section><h2>{html.escape(issue)} ({audit['issue_counts'].get(issue,0)})</h2>{''.join(cards)}</section>")
    path.write_text(f"""<!doctype html><html><head><meta charset='utf-8'><title>Practice Bank Quality Audit</title><style>
body{{font-family:Arial,sans-serif;padding:20px;line-height:1.45}}.summary{{background:#eee;padding:10px;border:1px solid #ccc}}section{{border-top:3px solid #222;margin-top:24px}}.card{{border:1px solid #ddd;padding:10px;margin:10px 0}}.meta{{font-size:12px;color:#666;word-break:break-all}}.opts{{margin:8px 0}}.reason{{font-size:12px;color:#9a4b00}}pre{{white-space:pre-wrap;max-height:260px;overflow:auto;background:#f7f7f7;padding:8px}}
</style></head><body><h1>Practice Bank Quality Audit</h1><div class='summary'><pre>{html.escape(json.dumps(audit,ensure_ascii=False,indent=2))}</pre></div>{''.join(sections)}</body></html>""", encoding='utf-8')


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, default=ROOT)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    path = args.root/'output_json/practice_questions.json'
    data = load_json(path)
    questions = data.get('questions', [])
    issue_counts=Counter()
    by_format=Counter()
    by_subtopic=Counter()
    samples=defaultdict(list)
    changed=0
    for q in questions:
        issues, did_change = audit_question(q, args.apply)
        if did_change:
            changed += 1
        for issue in issues:
            issue_counts[issue]+=1
            by_format[(issue, q.get('question_format'))]+=1
            by_subtopic[(issue, q.get('knowledge_subtopic_code_v2'))]+=1
            if len(samples[issue]) < 12:
                samples[issue].append(q)
    ready_count=sum(bool(q.get('ready_for_ai_solve')) for q in questions)
    audit={
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'applied': args.apply,
        'total_questions': len(questions),
        'ready_for_ai_solve': ready_count,
        'not_ready_for_ai_solve': len(questions)-ready_count,
        'changed_questions': changed,
        'issue_counts': dict(issue_counts.most_common()),
        'issue_by_format_top': {f'{i}|{fmt}': c for (i,fmt),c in by_format.most_common(80)},
        'issue_by_subtopic_top': {f'{i}|{st}': c for (i,st),c in by_subtopic.most_common(80)},
    }
    data['quality_audit'] = audit
    if args.apply:
        data['generated_at'] = datetime.now().isoformat(timespec='seconds')
        data['ready_for_ai_solve_count'] = ready_count
        data['needs_review_count'] = sum(bool(q.get('needs_review')) for q in questions)
        data['passages'] = sp.build_passages([{'questions': questions}])
        data['total_passages'] = len(data['passages'])
        dump_json(path, data)
    out_json=args.root/'output_json/practice_bank_quality_audit.json'
    out_html=args.root/'previews/practice_bank_quality_audit.html'
    dump_json(out_json, {'audit': audit, 'samples': samples})
    render_report(out_html, audit, samples)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    print(f'Saved: {out_json}')
    print(f'Saved: {out_html}')

if __name__ == '__main__':
    main()
