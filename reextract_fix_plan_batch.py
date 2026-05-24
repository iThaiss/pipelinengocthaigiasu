from __future__ import annotations

import argparse, json, os, sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

import audit_fix_practice_bank as audit
import scan_practice_meta as sp

ROOT = Path('local_curriculum_english')
BASE_URL = os.getenv('CLAUDE_BASE_URL', 'http://localhost:20128').rstrip('/')
API_KEY = os.getenv('NINEROUTER_API_KEY') or os.getenv('CLAUDE_API_KEY') or 'local-9router'


def jload(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def jdump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def normalize_base(url: str) -> str:
    return url if url.endswith('/v1') else url + '/v1'


def qkey(q: dict[str, Any]) -> tuple[Any, ...]:
    opts=q.get('options') or {}
    opt_sig='|'.join(f'{k}:{" ".join(str(opts[k]).split())}' for k in sorted(opts))[:600]
    return (q.get('relative_path'), q.get('question_number'), opt_sig or ' '.join((q.get('question_text') or '').split())[:240])


def audit_result_questions(questions: list[dict[str, Any]]) -> tuple[int, Counter]:
    issue_counts=Counter()
    ready=0
    for q in questions:
        issues,_=audit.audit_question(q, True)
        if q.get('ready_for_ai_solve'):
            ready+=1
        for issue in issues:
            issue_counts[issue]+=1
    return ready, issue_counts


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream,'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    ap=argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, default=ROOT)
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--actions', default='re_extract_from_source_pdf,re_extract_options_or_mark_manual_review')
    ap.add_argument('--apply', action='store_true')
    args=ap.parse_args()
    root=args.root
    plan=jload(root/'output_json/practice_bank_fix_plan.json')
    actions=set(a.strip() for a in args.actions.split(',') if a.strip())
    targets=[r for r in plan['files'] if r.get('recommended_action') in actions and not str(r.get('relative_path','')).startswith('generated_backfill/')]
    targets=targets[:args.limit]
    manifest=jload(root/'output_json/file_manifest.json')
    by_rel={f.get('relative_path'):f for f in manifest.get('files',[]) if f.get('file_type')=='practice'}
    taxonomy=jload(root/'output_json/english_taxonomy_v2.json')
    node_path=root/'output_json/learning_map_nodes.json'
    node_index=sp.build_node_index(jload(node_path).get('nodes',[])) if node_path.exists() else {}
    client=anthropic.Anthropic(api_key=API_KEY, base_url=normalize_base(BASE_URL), timeout=sp.AI_TIMEOUT_SECONDS)
    cache_dir=root/'cache/reextract_fix_plan_batch'
    results=[]
    print(f'Target files: {len(targets)} apply={args.apply}', flush=True)
    for i,t in enumerate(targets,1):
        rel=t['relative_path']
        file_info=by_rel.get(rel)
        if not file_info:
            print(f'[{i}/{len(targets)}] missing manifest {rel}', flush=True)
            results.append({'relative_path':rel,'status':'missing_manifest','old_blocked':t['blocked_questions']})
            continue
        old_blocked=t['blocked_questions']
        result=sp.process_file(file_info, root, client, taxonomy, node_index, cache_dir, force=True, allow_ai_solve=False, regex_only=False)
        qs=result.get('questions',[])
        ready,issues=audit_result_questions(qs)
        result['fix_plan_reextract_audit']={'ready':ready,'total':len(qs),'issue_counts':dict(issues)}
        results.append(result)
        print(f'[{i}/{len(targets)}] old_blocked={old_blocked} new_q={len(qs)} ready={ready} issues={dict(issues)} {rel[:100]}', flush=True)
    out=root/'output_json/practice_reextract_fix_plan_batch.json'
    payload={'generated_at':datetime.now().isoformat(timespec='seconds'),'apply':args.apply,'targets':targets,'results':results}
    jdump(out,payload)
    if args.apply:
        main_path=root/'output_json/practice_questions.json'
        data=jload(main_path)
        target_paths=set()
        old_by_path={t['relative_path']: t for t in targets}
        for r in results:
            rel=r.get('file',{}).get('relative_path')
            audit_info=r.get('fix_plan_reextract_audit') or {}
            old_blocked=(old_by_path.get(rel) or {}).get('blocked_questions', 0)
            # Apply only if re-extraction produces usable questions and substantially reduces blocked count.
            if r.get('status')=='ok' and r.get('questions') and audit_info.get('ready',0)>0 and sum((audit_info.get('issue_counts') or {}).values()) < old_blocked:
                target_paths.add(rel)
        kept=[q for q in data['questions'] if q.get('relative_path') not in target_paths]
        new=[]
        seen=set()
        for r in results:
            if r.get('status')!='ok':
                continue
            for q in r.get('questions',[]):
                k=qkey(q)
                if k in seen:
                    continue
                seen.add(k)
                q['source_type']='reextract_fix_plan'
                new.append(q)
        data['questions']=kept+new
        data['total_questions']=len(data['questions'])
        data['generated_at']=datetime.now().isoformat(timespec='seconds')
        data['fix_plan_reextract_last_run']=str(out.relative_to(root))
        data['fix_plan_reextract_files_applied']=len(target_paths)
        data['passages']=sp.build_passages([{'questions':data['questions']}])
        data['total_passages']=len(data['passages'])
        jdump(main_path,data)
        # Re-run audit after merge.
        import subprocess
        subprocess.run([sys.executable,'audit_fix_practice_bank.py','--apply'], check=True)
    print(f'Saved: {out}')

if __name__=='__main__':
    main()
