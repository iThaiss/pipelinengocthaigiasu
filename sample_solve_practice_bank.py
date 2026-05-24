from __future__ import annotations

import argparse, json, os, random, re, sys, time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

ROOT = Path('local_curriculum_english')
BASE_URL = os.getenv('CLAUDE_BASE_URL', 'http://localhost:20128').rstrip('/')
API_KEY = os.getenv('CLAUDE_API_KEY') or os.getenv('ANTHROPIC_API_KEY') or os.getenv('NINEROUTER_API_KEY') or 'local-9router'
MODEL = os.getenv('PRACTICE_SAMPLE_SOLVE_MODEL', os.getenv('PRACTICE_AI_MODEL', 'gz-prod/claude-sonnet-4-6'))
TIMEOUT = float(os.getenv('PRACTICE_SAMPLE_SOLVE_TIMEOUT_SECONDS', '180'))

SYSTEM = '''You are a careful Vietnamese English exam teacher auditing a question bank.
Solve the question from the provided stem, options, and passage/context. Do not rewrite the question.
Return strict JSON only.'''

PROMPT = '''Solve this English exam question and audit whether it is usable.
Return JSON exactly:
{"answer":"A|B|C|D|NONE","explanation":"short Vietnamese explanation","confidence":"high|medium|low","usable":true,"issues":["..."]}

Rules:
- If options are missing, duplicated in a way that affects correctness, or context is insufficient, set usable=false and confidence low/medium.
- If no option is correct, answer NONE and explain.
- For writing prompts or non-MCQ tasks, answer NONE, explain expected response/rubric briefly, and set usable=true if prompt is valid.
- Do not invent missing source text.
'''

def norm_base(url: str) -> str:
    return url if url.endswith('/v1') else url + '/v1'

def jload(p: Path) -> Any:
    return json.loads(p.read_text(encoding='utf-8'))

def jdump(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def text_of(resp: Any) -> str:
    return ''.join(b.text for b in resp.content if getattr(b,'type',None)=='text').strip()

def extract_json(s: str) -> dict[str, Any]:
    s=s.strip()
    s=re.sub(r'^```(?:json)?\s*','',s)
    s=re.sub(r'\s*```$','',s)
    try: return json.loads(s)
    except Exception:
        start=s.find('{'); end=s.rfind('}')
        return json.loads(s[start:end+1])

def question_payload(q: dict[str, Any]) -> str:
    opts='\n'.join(f'{k}. {v}' for k,v in (q.get('options') or {}).items())
    parts=[
        f"ID: {q.get('question_id')}",
        f"Subtopic: {q.get('knowledge_subtopic_code_v2')}",
        f"Format: {q.get('question_format')}",
        f"Question: {q.get('question_text') or ''}",
    ]
    if q.get('passage_text'):
        parts.append('Passage/context:\n' + str(q.get('passage_text'))[:5000])
    if opts:
        parts.append('Options:\n' + opts)
    if q.get('correct_answer'):
        parts.append(f"Existing answer to verify: {q.get('correct_answer')} ({q.get('answer_source')})")
    return '\n\n'.join(parts)

def choose_sample(questions: list[dict[str,Any]], per_subtopic: int, limit_subtopics: int|None, seed: int) -> list[dict[str,Any]]:
    rng=random.Random(seed)
    by=defaultdict(list)
    for q in questions:
        by[q.get('knowledge_subtopic_code_v2') or 'UNKNOWN'].append(q)
    subtopics=sorted(by, key=lambda k: (-len(by[k]), k))
    if limit_subtopics:
        subtopics=subtopics[:limit_subtopics]
    sample=[]
    for st in subtopics:
        pool=by[st]
        priority=[q for q in pool if q.get('answer_source')=='missing'] or pool
        rng.shuffle(priority)
        sample.extend(priority[:per_subtopic])
    return sample

def solve_one(client: anthropic.Anthropic, q: dict[str,Any]) -> dict[str,Any]:
    resp=client.messages.create(
        model=MODEL, max_tokens=1600, temperature=0, system=SYSTEM,
        messages=[{'role':'user','content':PROMPT+'\n\n'+question_payload(q)}],
        extra_headers={'User-Agent':'curl/8.7.1'},
    )
    data=extract_json(text_of(resp))
    return data

def render_html(out: Path, payload: dict[str,Any]) -> None:
    import html
    rows=[]
    for item in payload['results']:
        q=item['question']; r=item.get('result') or {}; err=item.get('error')
        opts=' | '.join(f"{k}. {v}" for k,v in (q.get('options') or {}).items())
        passage=q.get('passage_text') or ''
        cls='bad' if err or not r.get('usable', True) or r.get('confidence')=='low' else 'ok'
        rows.append(f"""
<section class='{cls}'>
<div class='meta'>{html.escape(str(q.get('knowledge_subtopic_code_v2')))} / {html.escape(str(q.get('question_format')))} / {html.escape(str(q.get('relative_path')))}</div>
<div><b>Q{html.escape(str(q.get('question_number')))}</b> {html.escape(q.get('question_text') or '')}</div>
{f"<details><summary>Passage ({len(passage)} chars)</summary><pre>{html.escape(passage[:2500])}</pre></details>" if passage else ''}
<div class='opts'>{html.escape(opts)}</div>
<div class='ans'>Solved: <b>{html.escape(str(r.get('answer') if r else 'ERROR'))}</b> | confidence={html.escape(str(r.get('confidence')))} | usable={html.escape(str(r.get('usable')))} | existing={html.escape(str(q.get('correct_answer')))} ({html.escape(str(q.get('answer_source')))})</div>
<div>{html.escape(str(r.get('explanation') if r else err))}</div>
<div class='issues'>{html.escape(', '.join(r.get('issues') or []))}</div>
</section>""")
    summary=payload['summary']
    body=''.join(rows)
    out.write_text(f"""<!doctype html><html><head><meta charset='utf-8'><title>Practice Sample Solve QA</title><style>
body{{font-family:Arial,sans-serif;padding:20px;line-height:1.45}}section{{border:1px solid #ddd;margin:12px 0;padding:10px}}.bad{{background:#fff5f0;border-color:#d98262}}.ok{{background:#f8fff7}}.meta{{font-size:12px;color:#666;word-break:break-all}}.opts{{margin:8px 0}}.ans{{background:#eee;padding:6px;margin:8px 0}}pre{{white-space:pre-wrap;max-height:260px;overflow:auto;background:#f7f7f7;padding:8px}}.issues{{color:#9a4b00}}
</style></head><body><h1>Practice Sample Solve QA</h1><pre>{html.escape(json.dumps(summary,ensure_ascii=False,indent=2))}</pre>{body}</body></html>""",encoding='utf-8')

def main() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s,'reconfigure'): s.reconfigure(encoding='utf-8')
    ap=argparse.ArgumentParser()
    ap.add_argument('--root',type=Path,default=ROOT)
    ap.add_argument('--per-subtopic',type=int,default=1)
    ap.add_argument('--limit-subtopics',type=int,default=20)
    ap.add_argument('--seed',type=int,default=42)
    ap.add_argument('--force',action='store_true')
    args=ap.parse_args()
    data=jload(args.root/'output_json/practice_questions.json')
    sample=choose_sample(data['questions'], args.per_subtopic, args.limit_subtopics, args.seed)
    cache_dir=args.root/'cache/sample_solve_practice_bank'
    client=anthropic.Anthropic(api_key=API_KEY, base_url=norm_base(BASE_URL), timeout=TIMEOUT)
    results=[]
    print(f'Sample questions: {len(sample)} | model={MODEL} | url={norm_base(BASE_URL)}', flush=True)
    for i,q in enumerate(sample,1):
        qid=q.get('question_id') or f'{i}'
        cache=cache_dir/f'{qid}.json'
        if cache.exists() and not args.force:
            item=jload(cache); item['from_cache']=True
        else:
            try:
                res=solve_one(client,q)
                item={'question':q,'result':res,'error':None,'solved_at':datetime.now().isoformat(timespec='seconds'),'from_cache':False}
            except Exception as e:
                item={'question':q,'result':None,'error':str(e),'solved_at':datetime.now().isoformat(timespec='seconds'),'from_cache':False}
            jdump(cache,item)
        results.append(item)
        r=item.get('result') or {}
        print(f"[{i}/{len(sample)}] {q.get('knowledge_subtopic_code_v2')} ans={r.get('answer')} conf={r.get('confidence')} usable={r.get('usable')} err={bool(item.get('error'))}", flush=True)
        time.sleep(0.5)
    summary={
        'generated_at':datetime.now().isoformat(timespec='seconds'),
        'model':MODEL,
        'sample_size':len(results),
        'subtopics_sampled':len(set((x['question'].get('knowledge_subtopic_code_v2') for x in results))),
        'confidence':Counter((x.get('result') or {}).get('confidence') for x in results),
        'usable':Counter(str((x.get('result') or {}).get('usable')) for x in results),
        'errors':sum(1 for x in results if x.get('error')),
        'answer':Counter((x.get('result') or {}).get('answer') for x in results),
    }
    # Counters are not JSON serializable as-is in nested output in some contexts
    summary={k:(dict(v) if isinstance(v,Counter) else v) for k,v in summary.items()}
    payload={'summary':summary,'results':results}
    out_json=args.root/'output_json/practice_sample_solve_qa.json'
    out_html=args.root/'previews/practice_sample_solve_qa.html'
    jdump(out_json,payload)
    render_html(out_html,payload)
    print(f'Saved: {out_json}')
    print(f'Saved: {out_html}')

if __name__=='__main__':
    main()
