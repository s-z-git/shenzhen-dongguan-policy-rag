"""全量精排候选 + doc cap 对比（K=20，规则版查询分析）。"""
import json, time
from app.config import DEFAULT
from app.pipeline import RAGPipeline
from app.eval.metrics import doc_equivalence, golden_ids
from app.generate import analyze_query, filters_from_analysis
from app.retrieve.rerank import rerank

K=20
gold=[json.loads(l) for l in open('data/eval_gold.reviewed.jsonl',encoding='utf-8') if l.strip()]
gold=[x for x in gold if x.get('golden_docs')]
p=RAGPipeline.load(DEFAULT); eq=doc_equivalence(p.chunks); by=p.retriever.by_id
p.retriever.cfg.vector_top_k=K; p.retriever.cfg.bm25_top_k=K
rows=[]; t0=time.perf_counter()
for i,item in enumerate(gold,1):
    q=item['query']; gids={eq.get(g,g) for g in golden_ids(item)}
    f=filters_from_analysis(analyze_query(q,DEFAULT,use_llm=False))
    r=p.retriever.retrieve(q,filters=f,use_rerank=False,top_k=5,keep_stages=True)
    filt=r['stages']['filtered']
    cands=[(cid,by[cid]['text']) for cid in filt]
    ranked=rerank(q,cands,DEFAULT.rerank_model,DEFAULT.embed_device)
    rows.append({'query':q,'type':item.get('type','fact'),'golden':sorted(gids),
                 'filtered':[eq.get(by[c]['doc_id'],by[c]['doc_id']) for c in filt],
                 'rerank':[[cid,round(float(s),6)] for cid,s in ranked],
                 'rerank_docs':[eq.get(by[cid]['doc_id'],by[cid]['doc_id']) for cid,_ in ranked]})
    if i%10==0: print('%d/%d %.0fs'%(i,len(gold),time.perf_counter()-t0),flush=True)
json.dump(rows,open('data/rank_probe_rerank_full.json','w',encoding='utf-8'),ensure_ascii=False,indent=2)

def pick(r,cap):
    out=[]; cnt={}
    for cid,d in zip([x[0] for x in r['rerank']],r['rerank_docs']):
        if cap and cnt.get(d,0)>=cap: continue
        cnt[d]=cnt.get(d,0)+1; out.append((cid,d))
        if len(out)>=5: break
    return out

def ev(cap):
    hit=0; rr=0.0; full=0; uniq=0
    for r in rows:
        ids=[d for _,d in pick(r,cap)]; g=set(r['golden'])
        ranks=[i+1 for i,d in enumerate(ids) if d in g]
        hit+=bool(ranks); rr+=1/ranks[0] if ranks else 0
        full+=sum(1 for x in g if x in ids)==len(g); uniq+=len(set(ids))
    n=len(rows); return {'recall':round(hit/n,4),'mrr':round(rr/n,4),'full':round(full/n,4),'mean_unique_docs':round(uniq/n,2)}
for cap in (0,1,2,3):
    print('cap',cap,ev(cap),flush=True)
print('total %.1fs'%(time.perf_counter()-t0))
