from __future__ import annotations

import argparse, base64, json, os, re, sys, time
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
import fitz
import scan_practice_meta as sp

ROOT = Path('local_curriculum_english')
BASE_URL = os.getenv('CLAUDE_BASE_URL', 'http://localhost:20128').rstrip('/')
API_KEY = os.getenv('CLAUDE_API_KEY') or os.getenv('ANTHROPIC_API_KEY') or 'local-9router'
MODEL = os.getenv('OCR_VISION_MODEL') or os.getenv('PRACTICE_AI_MODEL', 'gz-prod/claude-sonnet-4-6')
TIMEOUT = float(os.getenv('OCR_VISION_TIMEOUT_SECONDS', '240'))

SYSTEM = '''You recover structured English exam questions from scanned PDF page images.
Extract only visible content. Do not invent options, answers, or answer keys.
Ignore headers, footers, watermark/fanpage/course/copyright/anti-sharing text.
Return ONLY newline-delimited JSON objects. No markdown. No JSON array.'''

PROMPT = '''Return JSON Lines, exactly one JSON object per line.
The first line must be: {"record_type":"file_summary","confidence":"high|medium|low","review_reason":"..."}
Every later line must be one question object with record_type "question".

Question object fields:
{"record_type":"question","question_number":1,"page_start":1,"page_end":1,"question_text":"...","options":{"A":"...","B":"...","C":"...","D":"..."},"correct_answer":null,"answer_source":"missing","explanation":"","passage_id":"p1","passage_text":"...","question_format":"thpt_reading_passage","knowledge_subtopic_code_v2":"E2R.05","exam_profiles":["THPT_2025_CORE"],"difficulty":"basic","confidence":"high|medium|low","needs_review":false,"review_reason":""}

Rules:
- Extract all visible questions in the file.
- Preserve option labels A/B/C/D separately.
- Attach relevant passage_text to reading questions. If passage spans pages, include the full visible passage text you can recover.
- If answer key is visible, set correct_answer and answer_source "pdf_key"; otherwise correct_answer null and answer_source "missing".
- Do not solve missing answers.
- Mark needs_review true if text/options are uncertain or incomplete.
- Use hints for likely subtopic/format only when unsure.'''

def norm_base(url: str) -> str:
    return url if url.endswith('/v1') else url + '/v1'

def jload(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))

def jdump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def enc(pdf: Path, page_index: int, zoom: float) -> str:
    with fitz.open(pdf) as doc:
        pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return base64.b64encode(pix.tobytes('png')).decode('ascii')

def content_for(pdf: Path, hints: dict[str, Any], zoom: float) -> list[dict[str, Any]]:
    with fitz.open(pdf) as doc:
        n = len(doc)
    c: list[dict[str, Any]] = [{'type':'text','text': PROMPT + '\nFile: ' + pdf.name + '\nHints: ' + json.dumps(hints, ensure_ascii=False)}]
    for i in range(n):
        c.append({'type':'text','text': f'Page {i+1}'})
        c.append({'type':'image','source': {'type':'base64','media_type':'image/png','data': enc(pdf, i, zoom)}})
    return c

def resp_text(resp: Any) -> str:
    return ''.join(b.text for b in resp.content if getattr(b, 'type', None) == 'text')

def parse_jsonl(text: str) -> dict[str, Any]:
    text = re.sub(r'^```(?:jsonl|json)?\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text)
    summary = {'confidence': 'medium', 'review_reason': 'jsonl_vision'}
    questions = []
    bad = 0
    buf = ''
    depth = 0
    in_str = False
    esc = False
    objects: list[str] = []
    for ch in text:
        if not buf and ch.isspace():
            continue
        if not buf and ch != '{':
            continue
        buf += ch
        if esc:
            esc = False
            continue
        if ch == '\\':
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                objects.append(buf.strip().rstrip(','))
                buf = ''
    if buf.strip():
        bad += 1
    for line in objects:
        try:
            obj = json.loads(line)
        except Exception:
            bad += 1
            continue
        rt = obj.pop('record_type', '')
        if rt == 'file_summary':
            summary = obj
        elif rt == 'question':
            questions.append(obj)
    return {'file_summary': summary, 'questions': questions, '_jsonl_bad_lines': bad, '_raw_response': text}

def fill_passage_text(raw: dict[str, Any]) -> dict[str, Any]:
    passages: dict[str, str] = {}
    for q in raw.get('questions') or []:
        pid = q.get('passage_id')
        text = q.get('passage_text')
        if pid and text and len(str(text).strip()) > len(passages.get(pid, '')):
            passages[str(pid)] = str(text).strip()
    last_text = ''
    for q in raw.get('questions') or []:
        pid = q.get('passage_id')
        if q.get('passage_text'):
            last_text = str(q.get('passage_text') or '').strip()
        elif pid and str(pid) in passages:
            q['passage_text'] = passages[str(pid)]
        elif last_text and str(q.get('question_format') or '').startswith('thpt_reading'):
            q['passage_text'] = last_text
    return raw

def normalize_raw_questions(raw: dict[str, Any]) -> dict[str, Any]:
    for q in raw.get('questions') or []:
        text = str(q.get('question_text') or '')
        opts = q.get('options') if isinstance(q.get('options'), dict) else {}
        if not opts and re.search(r'correct form|word formation|capitals|\b\(\d+\)\s*_+', text, re.I):
            q['question_format'] = 'spt_word_formation'
            q['knowledge_subtopic_code_v2'] = q.get('knowledge_subtopic_code_v2') or 'E2X.01'
        if not opts and q.get('question_format') == 'hsa_sentence_completion' and re.search(r'\b\(\d+\)\s*_+', text):
            q['question_format'] = 'spt_word_formation'
        if q.get('question_format') == 'thpt_reading_passage' and not q.get('knowledge_subtopic_code_v2'):
            q['knowledge_subtopic_code_v2'] = 'E2R.05'
    return raw

def failed_files(root: Path, limit: int | None) -> list[dict[str, Any]]:
    source = jload(root/'output_json/practice_questions_no_vip90_source.json')
    rows = [r for r in source.get('files', []) if r.get('status') != 'ok' and r.get('error') == 'ocr_required_or_image_only_pdf']
    return rows[:limit] if limit else rows

def qkey(q: dict[str, Any]) -> tuple[Any, ...]:
    opts = q.get('options') or {}
    opt_sig = '|'.join(f'{k}:{" ".join(str(opts[k]).split())}' for k in sorted(opts))[:600]
    text_sig = ' '.join((q.get('question_text') or '').split())[:240]
    return (q.get('file_sha1'), q.get('question_number'), opt_sig or text_sig)

def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, default=ROOT)
    ap.add_argument('--limit', type=int)
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--zoom', type=float, default=2.2)
    ap.add_argument('--only-name')
    args = ap.parse_args()
    root = args.root
    taxonomy = jload(root/'output_json/english_taxonomy_v2.json')
    node_path = root/'output_json/learning_map_nodes.json'
    node_index = sp.build_node_index(jload(node_path).get('nodes', [])) if node_path.exists() else {}
    rows = failed_files(root, args.limit)
    if args.only_name:
        rows = [r for r in rows if args.only_name in (r.get('file', {}).get('relative_path') or '')]
    cache_dir = root/'cache/ocr_vision_rescan_jsonl'
    client = anthropic.Anthropic(api_key=API_KEY, base_url=norm_base(BASE_URL), timeout=TIMEOUT)
    results = []
    print(f'Files: {len(rows)} | model={MODEL} | url={norm_base(BASE_URL)}', flush=True)
    for n, old in enumerate(rows, 1):
        info = old.get('file', {})
        rel = info.get('relative_path') or ''
        pdf = root/'input_sources'/rel
        sha1 = sp.file_sha1(pdf) if pdf.exists() else ''
        cache = cache_dir/f'{sha1 or n}.json'
        if cache.exists() and not args.force:
            raw = jload(cache); from_cache = True
        elif not pdf.exists():
            raw = {'file_summary': {'confidence':'low','review_reason': f'missing_pdf: {pdf}'}, 'questions': []}; from_cache = False
        else:
            hints = dict(old.get('hints') or {})
            content = content_for(pdf, hints, args.zoom)
            try:
                resp = client.messages.create(model=MODEL, max_tokens=16000, temperature=0, system=SYSTEM, messages=[{'role':'user','content':content}], extra_headers={'User-Agent':'curl/8.7.1'})
                raw = parse_jsonl(resp_text(resp)); raw['_model_used'] = MODEL
            except Exception as exc:
                raw = {'file_summary': {'confidence':'low','review_reason': f'vision_failed: {exc}'}, 'questions': []}
            jdump(cache, raw); from_cache = False
        hints = old.get('hints') or {}
        raw = normalize_raw_questions(fill_passage_text(raw))
        qs, summary = sp.validate_and_normalize(raw, info, sha1, hints, taxonomy, node_index, raw.get('_model_used') or MODEL)
        for q in qs:
            q['source_type'] = 'ocr_vision_rescan'
            q['needs_review'] = bool(q.get('needs_review')) or q.get('confidence') != 'high' or q.get('answer_source') == 'missing'
            bits = [x for x in str(q.get('review_reason') or '').split('; ') if x]
            bits.append('vision_rescan')
            q['review_reason'] = '; '.join(sorted(set(bits)))
        result = {'file': info, 'file_sha1': sha1, 'scanned_at': datetime.now().isoformat(timespec='seconds'), 'status': 'ok' if qs else 'failed', 'error': '' if qs else 'vision_returned_zero_questions', 'hints': hints, 'file_summary': summary or raw.get('file_summary') or {}, 'questions': qs, 'raw_question_count': len(raw.get('questions') or []), 'bad_jsonl_lines': raw.get('_jsonl_bad_lines', 0), 'from_cache': from_cache, 'vision_cache': str(cache)}
        results.append(result)
        print(f'[{n}/{len(rows)}] raw={result["raw_question_count"]} normalized={len(qs)} bad={result["bad_jsonl_lines"]} status={result["status"]} cache={from_cache} {Path(rel).name[:75]}', flush=True)
    accepted, rejected = sp.split_questions(results)
    payload = {'generated_at': datetime.now().isoformat(timespec='seconds'), 'ocr_engine':'claude_vision_rescan_jsonl', 'model': MODEL, 'total_files': len(results), 'total_questions': len(accepted), 'total_rejected_questions': len(rejected), 'files': results, 'questions': accepted, 'rejected_questions': rejected}
    out = root/'output_json/practice_questions_ocr_vision_rescan.json'
    jdump(out, payload)
    source_path = root/'output_json/practice_questions_no_vip90_source_ocr.json'
    source = jload(source_path) if source_path.exists() else jload(root/'output_json/practice_questions_no_vip90_source.json')
    paths = {r.get('file', {}).get('relative_path') for r in results}
    base = [q for q in source.get('questions', []) if q.get('relative_path') not in paths]
    merged = {qkey(q): q for q in base}
    for q in accepted:
        merged[qkey(q)] = q
    by_path = {r.get('file', {}).get('relative_path'): r for r in results}
    files = [by_path.get(f.get('file', {}).get('relative_path'), f) for f in source.get('files', [])]
    improved = dict(source)
    improved.update({'generated_at': datetime.now().isoformat(timespec='seconds'), 'ocr_merge_source':'claude_vision_rescan_merge', 'ocr_vision_questions_added': len(accepted), 'questions': list(merged.values()), 'total_questions': len(merged), 'files': files})
    improved['rejected_questions'] = [q for q in source.get('rejected_questions', []) if q.get('relative_path') not in paths] + rejected
    improved['total_rejected_questions'] = len(improved['rejected_questions'])
    improved['passages'] = sp.build_passages([{'questions': improved['questions']}])
    improved['total_passages'] = len(improved['passages'])
    merged_out = root/'output_json/practice_questions_no_vip90_source_ocr_vision.json'
    jdump(merged_out, improved)
    print(f'Saved: {out}')
    print(f'Saved: {merged_out}')

if __name__ == '__main__':
    main()
