import json, re
from dataclasses import replace
from app.config import DEFAULT
from app.pipeline import RAGPipeline
from app.generate import answer
from app.eval.answer_points import judge_records, aggregate_answer_points
Q=["东莞职业介绍补贴标准是多少？员工制家政企业吸纳就业补贴怎么算？","深圳新引进博士人才入户补贴多少？深圳最低工资标准是多少？","深圳职业技能培训补贴包括哪些项目？自主创业场租补贴第二年每月多少？","深圳自主创业的场租补贴每月多少？就业困难人员灵活就业社保补贴按什么标准？","深圳青年人才安居补贴每月多少？就业困难人员灵活就业补贴最长能领多久？"]
gold={x['query']:x for x in (json.loads(l) for l in open('data/eval_gold.reviewed.jsonl',encoding='utf-8') if l.strip())}
p=RAGPipeline.load(DEFAULT); rows=[]
for q in Q:
    subs=[x.strip() for x in re.split(r'[？?]',q) if x.strip()]
    parts=[]
    for s in subs:
        r=p.retriever.retrieve(s,filters={'city_in':[('东莞' if '东莞' in q else '深圳')]},use_rerank=True,top_k=5)
        out=answer(s,r['final'],DEFAULT)
        parts.append(out['answer'])
    combined='\n\n'.join('%d. %s'%(i+1,a) for i,a in enumerate(parts))
    rows.append({'query':q,'type':'multi','answer_points':gold[q].get('answer_points') or [],'answer':combined})
    print('generated',q[:40],'parts',len(parts),flush=True)
recs,stats=judge_records(rows,model='glm-4.5-air',workers=2,cache_path='data/l1_cache.jsonl',fallback_model='')
old={r['query']:r for r in json.load(open('data/l1_report_v2_prompt2_air.json',encoding='utf-8'))['records'] if r.get('l1')}
print('\nRESULT')
for r in rows: print('%.2f -> %.2f | %s'%(old.get(r['query'],{}).get('l1',{}).get('coverage',-1),r['l1']['coverage'],r['query'][:45]))
print(json.dumps(aggregate_answer_points(rows),ensure_ascii=False))
json.dump({'summary':aggregate_answer_points(rows),'records':rows},open('data/reports/multi_subanswer_smoke.json','w',encoding='utf-8'),ensure_ascii=False,indent=2)
