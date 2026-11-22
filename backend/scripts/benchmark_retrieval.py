#!/usr/bin/env python3
"""
Offline retrieval benchmark for CFT Knowledge Assistant.
Runs inside the existing backend container; no Docker image rebuild required.
Compares BM25, dense E5 and Hybrid RRF on the same labeled query set.
"""
from __future__ import annotations
import argparse, json, os, statistics, time
from datetime import datetime, timezone
from pathlib import Path

from qdrant_client import QdrantClient, models
from app.config import settings
from app.retrieval.embeddings import dense_embed, sparse_query_embed

ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = ROOT / "benchmark_queries.json"
DEFAULT_OUT = ROOT / "benchmark_results.json"

def qdrant():
    return QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, timeout=30)

def search(client, query, mode, k):
    active = models.Filter(must=[
        models.FieldCondition(key="is_active", match=models.MatchValue(value=True)),
        models.FieldCondition(key="scope", match=models.MatchValue(value="cft")),
    ])
    if mode in ("e5", "hybrid"):
        dense = dense_embed([query], task="retrieval.query")[0]
    if mode in ("bm25", "hybrid"):
        sparse = sparse_query_embed(query)
    if mode == "e5":
        result = client.query_points(
            collection_name=settings.qdrant_collection, query=dense, using="dense",
            query_filter=active, limit=k, with_payload=True,
        )
    elif mode == "bm25":
        result = client.query_points(
            collection_name=settings.qdrant_collection,
            query=models.SparseVector(**sparse), using="bm25",
            query_filter=active, limit=k, with_payload=True,
        )
    else:
        result = client.query_points(
            collection_name=settings.qdrant_collection,
            prefetch=[
                models.Prefetch(query=dense, using="dense", limit=k * 3, filter=active),
                models.Prefetch(query=models.SparseVector(**sparse), using="bm25", limit=k * 3, filter=active),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=active, limit=k, with_payload=True,
        )
    rows=[]
    seen=set()
    for point in result.points:
        payload=point.payload or {}
        sid=payload.get("source_id")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        rows.append({"source_id":sid,"title":payload.get("title",""),"score":float(point.score or 0)})
    return rows

def dcg(rels):
    return sum(rel / __import__("math").log2(i + 2) for i, rel in enumerate(rels))

def metrics(ranked_ids, relevant_ids, k):
    relevant=set(relevant_ids)
    top=ranked_ids[:k]
    hits=[1 if x in relevant else 0 for x in top]
    precision=sum(hits)/k if k else 0.0
    recall=sum(hits)/len(relevant) if relevant else 0.0
    rr=next((1/(i+1) for i,x in enumerate(top) if x in relevant),0.0)
    ideal=[1]*min(len(relevant),k)
    ndcg=(dcg(hits)/dcg(ideal)) if ideal and dcg(ideal) else 0.0
    return {"precision_at_k":precision,"recall_at_k":recall,"mrr_at_k":rr,"ndcg_at_k":ndcg}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--dataset",default=str(DEFAULT_DATASET))
    parser.add_argument("--output",default=str(DEFAULT_OUT))
    parser.add_argument("--k",type=int,default=5)
    parser.add_argument("--repeats",type=int,default=3)
    args=parser.parse_args()
    dataset=json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    queries=dataset.get("queries",[])
    if not queries:
        raise SystemExit("Dataset has no queries.")
    client=qdrant()
    client.get_collection(settings.qdrant_collection)
    modes=("bm25","e5","hybrid")
    report={
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "collection":settings.qdrant_collection,
        "k":args.k,"repeats":args.repeats,
        "dataset_version":dataset.get("version","unversioned"),
        "warning":"Metrics are valid only for expert-reviewed relevance labels and this exact indexed snapshot.",
        "query_count":len(queries),"results":{}
    }
    for mode in modes:
        per_query=[]
        all_metrics=[]
        latencies=[]
        overlaps=[]
        # Warm up model/runtime and Qdrant once per mode; do not mix cold-start
        # download/model initialization time into steady-state retrieval latency.
        search(client, queries[0]["query"], mode, args.k)
        for item in queries:
            q=item["query"]
            relevant=item.get("relevant_source_ids",[])
            if not relevant:
                continue
            runs=[]
            for _ in range(max(1,args.repeats)):
                started=time.perf_counter()
                rows=search(client,q,mode,args.k)
                latencies.append((time.perf_counter()-started)*1000)
                runs.append(rows)
            ranked=[r["source_id"] for r in runs[0]]
            m=metrics(ranked,relevant,args.k)
            all_metrics.append(m)
            if len(runs)>1:
                sets=[set(r["source_id"] for r in run) for run in runs]
                denom=len(sets[0] | sets[1])
                overlaps.append(len(sets[0] & sets[1])/denom if denom else 1.0)
            per_query.append({
                "id":item.get("id"),"query":q,"expected_sources":len(relevant),
                "retrieved":[{"source_id":r["source_id"],"title":r["title"],"score":r["score"]} for r in runs[0]],
                **m
            })
        def avg(key):
            vals=[x[key] for x in all_metrics]
            return round(statistics.mean(vals),4) if vals else 0.0
        sorted_lat=sorted(latencies)
        def percentile(p):
            if not sorted_lat:return 0.0
            idx=min(len(sorted_lat)-1,max(0,int((len(sorted_lat)-1)*p)))
            return round(sorted_lat[idx],2)
        report["results"][mode]={
            "query_count":len(per_query),
            "mean_precision_at_k":avg("precision_at_k"),
            "mean_recall_at_k":avg("recall_at_k"),
            "mean_mrr_at_k":avg("mrr_at_k"),
            "mean_ndcg_at_k":avg("ndcg_at_k"),
            "latency_ms_mean":round(statistics.mean(latencies),2) if latencies else 0,
            "latency_ms_p50":percentile(.50),
            "latency_ms_p95":percentile(.95),
            "repeatability_jaccard_at_k":round(statistics.mean(overlaps),4) if overlaps else None,
            "per_query":per_query
        }
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("\\n=== CFT RETRIEVAL BENCHMARK ===")
    print(f"Collection: {report['collection']} | queries: {len(queries)} | k={args.k} | repeats={args.repeats}")
    print(f"{'MODE':<9} {'P@k':>8} {'R@k':>8} {'MRR@k':>8} {'NDCG@k':>8} {'mean ms':>10} {'p95 ms':>10} {'Jaccard':>10}")
    for mode in modes:
        r=report["results"][mode]
        print(f"{mode:<9} {r['mean_precision_at_k']:>8.3f} {r['mean_recall_at_k']:>8.3f} {r['mean_mrr_at_k']:>8.3f} {r['mean_ndcg_at_k']:>8.3f} {r['latency_ms_mean']:>10.1f} {r['latency_ms_p95']:>10.1f} {str(r['repeatability_jaccard_at_k']):>10}")
    print(f"\\nFull JSON report: {args.output}")
    print("NOTE: review benchmark_queries.json relevance labels with a domain expert before presenting scores.")

if __name__=="__main__":
    main()
