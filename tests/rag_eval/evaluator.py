import sys;sys.stdout.reconfigure(encoding="utf-8")
"""RAG检索质量评估器（v0.8.3 Phase E）

评估指标：Recall@K, MRR, NDCG
方法：对30条测试查询执行检索，对Top-5结果人工标注相关性，计算标准IR指标。
"""

import json
import math
import shutil
import sys
import time
import yaml
from pathlib import Path

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.rag.service import RAGService


def load_queries(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_relevance_labels(path):
    """加载人工标注的相关性标签
    
    格式: {"q01": {"doc_id_1": 2, "doc_id_2": 1, ...}}
    2=relevant, 1=partial, 0=irrelevant
    """
    if Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_relevance_labels(path, labels):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(labels, f, ensure_ascii=False, indent=2)


def dcg_at_k(scores, k):
    """DCG@K"""
    scores = scores[:k]
    if not scores:
        return 0.0
    return sum(s / math.log2(i + 2) for i, s in enumerate(scores))


def ndcg_at_k(scores, k):
    """NDCG@K"""
    ideal = sorted(scores, reverse=True)[:k]
    dcg_val = dcg_at_k(scores, k)
    idcg_val = dcg_at_k(ideal, k)
    return dcg_val / idcg_val if idcg_val > 0 else 0.0


def recall_at_k(scores, k):
    """Recall@K = 相关文档命中数 / 总相关文档数"""
    relevant_total = sum(1 for s in scores if s >= 1)
    if relevant_total == 0:
        return 0.0
    relevant_at_k = sum(1 for s in scores[:k] if s >= 1)
    return relevant_at_k / relevant_total


def mrr(scores):
    """Mean Reciprocal Rank: 第一个相关文档的排名倒数"""
    for i, s in enumerate(scores):
        if s >= 1:
            return 1.0 / (i + 1)
    return 0.0


def auto_label(rag_service, queries, top_k=5):
    "Auto-label using semantic cosine similarity (sentence-transformers)."
    labels = {}
    # Build query embeddings
    # P1-B修复(2026-09-05): 查询侧必须用 embed_query（带BGE检索前缀、与线上检索分布一致），
    # 原实现用 embed() 打标 = 用无前缀分布判定相关性 = 循环验证失真。
    # 文档侧保持 embed()（与建索引时一致，文档不加前缀）。
    query_texts = [q["query"] for q in queries]
    query_embeddings = [
        rag_service._embedder.embed_query(t) for t in query_texts
    ]
    
    for qi, q in enumerate(queries):
        result = rag_service.retrieve(q["query"], top_k=top_k)
        q_labels = {}
        doc_texts = []
        for doc in result.documents:
            doc_text = doc.content if hasattr(doc,"content") else ""
            doc_texts.append(doc_text[:500] if doc_text else "")
        
        # Build doc embeddings
        doc_embeddings = rag_service._embedder.embed(doc_texts)
        
        # Compute cosine similarity
        from numpy import dot
        from numpy.linalg import norm
        q_emb = query_embeddings[qi]
        for di, doc in enumerate(result.documents):
            if doc_texts[di]:
                sim = dot(q_emb, doc_embeddings[di]) / (norm(q_emb) * norm(doc_embeddings[di]) + 1e-8)
                if sim >= 0.72:
                    q_labels[doc.id] = 2
                elif sim >= 0.60:
                    q_labels[doc.id] = 1
                else:
                    q_labels[doc.id] = 0
            else:
                q_labels[doc.id] = 0
        labels[q["id"]] = q_labels
    return labels
def check_label_coverage(rag_service, labels):
    """检查标注中的 doc_id 与当前索引的命中率（P1-B守卫，2026-09-05）

    背景：索引重建/分块规则变更后 doc_id 体系会变（如 ch54_p5 -> rz54_p5），
    旧 labels 全部失配时评估指标全 0，质量保障形同虚设且无人察觉。

    Returns:
        (hit_ratio, index_ids): 命中率0-1, 当前索引doc_id集合
    """
    index_ids = set()
    store = getattr(rag_service, "_store", None)
    if store is not None:
        docs = getattr(store, "_docs", None) or getattr(store, "documents", None) or []
        index_ids = {d.id for d in docs}
    labeled_ids = set()
    for q_labels in labels.values():
        labeled_ids.update(q_labels.keys())
    if not labeled_ids:
        return 0.0, index_ids
    if not index_ids:
        return 0.0, index_ids
    hits = sum(1 for d in labeled_ids if d in index_ids)
    return hits / len(labeled_ids), index_ids


def evaluate(rag_service, queries, labels, top_k=5):
    """运行评估，计算所有指标"""
    results = []
    
    for q in queries:
        result = rag_service.retrieve(q["query"], top_k=top_k)
        q_labels = labels.get(q["id"], {})
        
        # 构建相关性分数列表（按检索排序）
        scores = []
        doc_ids = []
        for doc, score in zip(result.documents, result.scores):
            doc_ids.append(doc.id)
            scores.append(q_labels.get(doc.id, 0))
        
        r_k = recall_at_k(scores, top_k)
        m = mrr(scores)
        n = ndcg_at_k([s/2.0 for s in scores], top_k)  # normalize to 0-1
        
        results.append({
            "query_id": q["id"],
            "query": q["query"],
            "category": q["category"],
            "recall@5": round(r_k, 3),
            "mrr": round(m, 3),
            "ndcg@5": round(n, 3),
            "top_docs": doc_ids[:top_k],
            "top_scores": [round(s, 3) for s in result.scores[:top_k]],
            "num_relevant": sum(1 for s in scores if s >= 1),
        })
    
    return results


def series_breakdown(results, top_k=5):
    """C4：按系列（doc_id 前缀）统计 top-k 命中分布——「引用归属」的分组视图。

    检索结果若过度集中于单一系列，即使 Recall 高也说明知识覆盖面偏科。
    doc_id 前缀 = 系列隔离标识（rz/jrz/qp/ol/ot 等，v0.8.9.0 纪律）。

    Returns:
        {系列前缀: 出现次数}，按次数降序；无法解析前缀的归入 "other"。
    """
    from collections import Counter
    hits = Counter()
    for r in results:
        for doc_id in (r.get("top_docs") or [])[:top_k]:
            doc_id = str(doc_id)
            # 无下划线（不属于任何系列）的 doc_id 归入 other，不污染系列统计
            prefix = doc_id.split("_", 1)[0] if "_" in doc_id else "other"
            hits[prefix or "other"] += 1
    return dict(hits.most_common())


def print_report(results):
    """打印评估报告"""
    n = len(results)
    avg_recall = sum(r["recall@5"] for r in results) / n
    avg_mrr = sum(r["mrr"] for r in results) / n
    avg_ndcg = sum(r["ndcg@5"] for r in results) / n

    print("=" * 70)
    print("RAG 检索质量评估报告")
    print(f"生成时间: {__import__('datetime').datetime.now().isoformat(timespec='seconds')}"
          f"（索引变更后重跑本评估对比，C4）")
    print("=" * 70)
    print(f"测试查询数: {n}")
    print(f"Top-K: 5")
    print()
    print(f"{'指标':<15} {'值':>8} {'目标':>8} {'状态':<10}")
    print("-" * 45)
    
    targets = {"Recall@5": 0.7, "MRR": 0.5, "NDCG@5": 0.5}
    for metric, target in targets.items():
        if metric == "Recall@5":
            val = avg_recall
        elif metric == "MRR":
            val = avg_mrr
        else:
            val = avg_ndcg
        status = "PASS" if val >= target else "FAIL"
        print(f"{metric:<15} {val:>8.3f} {target:>8.2f} {status:<10}")
    
    print()
    print("-" * 70)
    print(f"{'查询ID':<6} {'类别':<8} {'Recall@5':>8} {'MRR':>8} {'NDCG@5':>8} {'相关文档':>8} {'Top-1分数':>8}")
    print("-" * 70)
    for r in results:
        top1_score = r["top_scores"][0] if r["top_scores"] else 0
        print(f"{r['query_id']:<6} {r['category']:<8} {r['recall@5']:>8.3f} {r['mrr']:>8.3f} {r['ndcg@5']:>8.3f} {r['num_relevant']:>8} {top1_score:>8.3f}")
    
    print()
    
    # 按类别汇总
    from collections import defaultdict
    cat_results = defaultdict(list)
    for r in results:
        cat_results[r["category"]].append(r)
    
    print("按类别汇总:")
    print(f"{'类别':<10} {'数量':>5} {'Recall@5':>8} {'MRR':>8} {'NDCG@5':>8}")
    print("-" * 45)
    for cat, items in sorted(cat_results.items()):
        avg_r = sum(x["recall@5"] for x in items) / len(items)
        avg_m = sum(x["mrr"] for x in items) / len(items)
        avg_n = sum(x["ndcg@5"] for x in items) / len(items)
        print(f"{cat:<10} {len(items):>5} {avg_r:>8.3f} {avg_m:>8.3f} {avg_n:>8.3f}")

    # C4：引用归属分组视图——top-k 命中按知识系列分布（过度集中=覆盖面偏科）
    breakdown = series_breakdown(results, top_k=5)
    total_hits = sum(breakdown.values()) or 1
    print()
    print("系列归属分布（top-k 命中按 doc_id 前缀分组）:")
    for prefix, cnt in breakdown.items():
        bar = "#" * max(1, round(cnt / total_hits * 30))
        print(f"  {prefix:<12} {cnt:>5}  {bar}")
    print("  （命中过度集中于单一系列时，即使 Recall 达标也应扩评估集或补知识库）")

    return {
        "avg_recall@5": round(avg_recall, 3),
        "avg_mrr": round(avg_mrr, 3),
        "avg_ndcg@5": round(avg_ndcg, 3),
        "num_queries": n,
        "targets": targets,
        "results": results,
        "series_breakdown": breakdown,
    }


def main():
    # 加载配置
    with open("configs/settings.yaml", "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    
    rag_config = settings.get("rag", {})
    
    print("初始化RAG服务...")
    rag = RAGService(rag_config)
    
    if not rag._initialized:
        print("RAG未初始化，尝试启动...")
        rag.initialize()
    
    if not rag._initialized:
        print("错误: RAG初始化失败")
        return
    
    print(f"RAG就绪: {rag._store.size if rag._store else 0} 文档已索引")
    
    # 加载查询
    queries = load_queries("tests/rag_eval/test_queries.json")
    print(f"加载 {len(queries)} 条测试查询")
    
    # 加载或生成标注
    labels_path = "tests/rag_eval/relevance_labels.json"
    labels = load_relevance_labels(labels_path)
    
    if not labels:
        print("生成自动标注（基于检索分数阈值）...")
        labels = auto_label(rag, queries, top_k=5)
        save_relevance_labels(labels_path, labels)
        print(f"标注已保存到 {labels_path}")
        print("提示: 使用语义相似度自动标注(cosine>=0.72 relevant, >=0.60 partial). 可人工审核调整")
    else:
        print(f"加载已有标注: {len(labels)} 条查询")
        # P1-B守卫: doc_id命中率<50%视为标注体系过期，自动备份并重标
        hit_ratio, _ = check_label_coverage(rag, labels)
        print(f"标注doc_id与当前索引命中率: {hit_ratio:.1%}")
        if hit_ratio < 0.5:
            backup_path = labels_path + "." + time.strftime("%Y%m%d_%H%M%S") + ".bak"
            shutil.copyfile(labels_path, backup_path)
            print(f"警告: 命中率不足50%，标注体系已过期（索引重建/分块变更），旧标注备份至 {backup_path}")
            print("重新自动标注中（查询侧已改用带BGE前缀的embed_query）...")
            labels = auto_label(rag, queries, top_k=5)
            save_relevance_labels(labels_path, labels)
            print(f"新标注已保存到 {labels_path}，建议人工抽查校准")
    
    # 运行评估
    print("\n运行评估...")
    results = evaluate(rag, queries, labels, top_k=5)
    
    # 输出报告
    report = print_report(results)
    
    # 保存报告
    report_path = "tests/rag_eval/eval_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n报告已保存到 {report_path}")


if __name__ == "__main__":
    main()