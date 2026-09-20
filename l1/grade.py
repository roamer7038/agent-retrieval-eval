#!/usr/bin/env python3
"""L1: the metrics of results/pe3-l1.jsonl.

    l1/grade.py [--jsonl FILE] [--out DIR] [table|cells|json]

Metrics (plan v3.1)
-------------------
  nDCG@10   main. Binary gains: every relevant file has grade 1, so
            DCG = sum(1/log2(i+1)) over the relevant files in the top 10 and
            IDCG = sum(1/log2(i+1)) over i = 1..min(10, |R|).
  recall@10 |top10 & R| / |R|. For S7, whose R can hold hundreds of files,
            it is capped by 10/|R|; that is a property of the task, reported
            as such.
  MRR@10    1/(rank of the first relevant file), 0 when there is none.
  F1@10     S7 only: precision@10 = hits/10, recall@10 as above.

Secondary count for the corpora with several languages (the decision of
2026-09-20): for c4 a retrieved page content/<lang>/<rest> counts as "the same
page in another language" when content/<other>/<rest> is relevant. It is not
relevant (the gold is the page in the language the question was about); it is
counted apart, and nDCG is also given with those pages counted as relevant
(ndcg10_xlang) so that the cost of the rule can be seen.
"""
import argparse
import collections
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks as T  # noqa: E402

K = 10


def dcg(flags):
    return sum(f / math.log2(i + 2) for i, f in enumerate(flags))


def idcg(n):
    return sum(1 / math.log2(i + 2) for i in range(n))


def xlang(path, rels):
    """The same page of c4 in another language."""
    p = path.split("/")
    if len(p) < 4 or p[0] != "website" or p[1] != "content":
        return False
    rest = "/".join(p[3:])
    for r in rels:
        q = r.split("/")
        if len(q) >= 4 and q[0] == "website" and q[1] == "content" and q[2] != p[2] \
                and "/".join(q[3:]) == rest:
            return True
    return False


def metrics(paths, rels, corpus):
    rels = set(rels)
    top = [p for p, _ in paths[:K]]
    flags = [1 if p in rels else 0 for p in top]
    hits = sum(flags)
    ideal = idcg(min(K, len(rels)))
    mrr = next((1 / (i + 1) for i, f in enumerate(flags) if f), 0.0)
    prec = hits / K
    rec = hits / len(rels) if rels else 0.0
    m = {"ndcg10": dcg(flags) / ideal if ideal else 0.0,
         "recall10": rec, "mrr10": mrr, "precision10": prec,
         "f1_10": (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0,
         "hits10": hits, "n_ret": len(paths), "n_rel": len(rels)}
    if corpus == "c4":
        xf = [1 if (f or xlang(p, rels)) else 0 for p, f in zip(top, flags)]
        m["ndcg10_xlang"] = dcg(xf) / idcg(min(K, len(rels))) if ideal else 0.0
        m["xlang_only"] = sum(1 for p, f in zip(top, flags) if not f and xlang(p, rels))
    return m


def primary_lang(rec):
    """The condition the main table uses: ja for text tools, id for the rest."""
    return rec["query_lang"] in ("id", "ja")


def load(path):
    out = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            out.append(r)
    return out


def grade(recs):
    tasks = {t["id"]: t for t in T.load()}
    for r in recs:
        t = tasks.get(r["id"])
        if not t:
            continue
        r["m"] = metrics(r["paths"], t["qrels"], r["corpus"])
    return recs


def agg(recs, keys, metric="ndcg10"):
    g = collections.defaultdict(list)
    for r in recs:
        if "m" not in r:
            continue
        g[tuple(r[k] for k in keys)].append(r["m"][metric])
    return {k: (statistics.fmean(v), len(v)) for k, v in sorted(g.items())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", nargs="?", default="table",
                    choices=["table", "cells", "json", "time", "spread", "xlang", "empty",
                             "phrasing", "querylang"])
    ap.add_argument("--jsonl", default=os.path.join(T.ROOT, "results", "pe3-l1.jsonl"))
    ap.add_argument("--lang", default="primary", help="primary | ja | en | id | all")
    ap.add_argument("--metric", default="ndcg10")
    ap.add_argument("--phrasing", choices=["identifier", "paraphrase"])
    ap.add_argument("--corpus")
    a = ap.parse_args()
    recs = grade(load(a.jsonl))
    if a.lang == "primary":
        recs = [r for r in recs if primary_lang(r)]
    elif a.lang != "all":
        recs = [r for r in recs if r["query_lang"] == a.lang]
    if a.phrasing:
        recs = [r for r in recs if r["phrasing"] == a.phrasing]
    if a.corpus:
        recs = [r for r in recs if r["corpus"] == a.corpus]

    if a.what == "json":
        json.dump({"n": len(recs),
                   "by_tool": {"/".join(k): v for k, v in agg(recs, ["tool"], a.metric).items()},
                   "by_tool_scenario": {"/".join(k): v for k, v in
                                        agg(recs, ["tool", "scenario"], a.metric).items()}},
                  sys.stdout, ensure_ascii=False, indent=1)
        return
    if a.what == "time":
        # a question that gives the tool no query at all is not a search
        g = collections.defaultdict(list)
        for r in recs:
            if r["err"] == "empty_query":
                continue
            g[(r["tool"], r["corpus"])].append(r["t_s"] or 0)
        print("| 道具 | 題材 | 検索 n | 検索 s 中央 | 検索 s 95% | 立ち上げ s |")
        print("|---|---|--:|--:|--:|--:|")
        starts = {}
        for r in recs:
            starts[(r["tool"], r["corpus"])] = (r["startup"] or {}).get("container_s", 0) + \
                ((r["startup"] or {}).get("tool_s") or 0)
        for k, v in sorted(g.items()):
            v = sorted(v)
            p95 = v[min(len(v) - 1, int(0.95 * len(v)))]
            print(f"| {k[0]} | {k[1]} | {len(v)} | {statistics.median(v):.2f} | {p95:.2f} "
                  f"| {starts.get(k, 0):.1f} |")
        return

    scen = T.L1_SCENARIOS
    tools = sorted({r["tool"] for r in recs})
    if a.what == "spread":
        # PE3's condition to go on: the tools differ within each scenario.
        print("| シナリオ | 道具 n | 問 n | 平均 | 最小 | 最大 | 幅 | 標準偏差 | 床（全道具 0） | 天井（全道具 ≥0.9） |")
        print("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
        for sname in scen:
            sub = [r for r in recs if r["scenario"] == sname and "m" in r]
            if not sub:
                continue
            per = agg(sub, ["tool"], a.metric)
            vals = [v for v, _ in per.values()]
            byq = collections.defaultdict(list)
            for r in sub:
                byq[r["id"]].append(r["m"][a.metric])
            floor = sum(1 for v in byq.values() if max(v) == 0)
            ceil = sum(1 for v in byq.values() if min(v) >= 0.9)
            sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
            print(f"| {sname} | {len(vals)} | {len(byq)} | {statistics.fmean(vals):.3f} | {min(vals):.3f} "
                  f"| {max(vals):.3f} | {max(vals)-min(vals):.3f} | {sd:.3f} | "
                  f"{floor}/{len(byq)} | {ceil}/{len(byq)} |")
        return
    if a.what == "xlang":
        print("| 道具 | 別言語のページが上位 10 に入った問 | nDCG@10 | 別言語も正解とした nDCG@10 |")
        print("|---|--:|--:|--:|")
        sub = [r for r in recs if r["corpus"] == "c4" and "m" in r]
        g = collections.defaultdict(list)
        for r in sub:
            g[r["tool"]].append(r["m"])
        for t, ms in sorted(g.items()):
            n = sum(1 for m in ms if m.get("xlang_only", 0))
            print(f"| {t} | {n}/{len(ms)} | {statistics.fmean(m['ndcg10'] for m in ms):.3f} | "
                  f"{statistics.fmean(m.get('ndcg10_xlang', m['ndcg10']) for m in ms):.3f} |")
        return
    if a.what == "phrasing":
        print("| 系統 | 道具 | 識別子あり | 言い換え | 差 |")
        print("|---|---|--:|--:|--:|")
        fam = {r["tool"]: r["family"] for r in recs}
        by = agg(recs, ["tool", "phrasing"], a.metric)
        for t in tools:
            i = by.get((t, "identifier"))
            p_ = by.get((t, "paraphrase"))
            d = f"{i[0]-p_[0]:+.3f}" if i and p_ else "―"
            print(f"| {fam[t]} | {t} | {i[0]:.3f} | {p_[0]:.3f} | {d} |" if i and p_ else
                  f"| {fam[t]} | {t} | ― | ― | ― |")
        return
    if a.what == "querylang":
        allr = grade(load(a.jsonl))
        by = agg([r for r in allr if r["query_lang"] in ("ja", "en")], ["tool", "corpus", "query_lang"], a.metric)
        print("| 道具 | 題材 | 日本語の質問 | 英語の対訳 | 差 |")
        print("|---|---|--:|--:|--:|")
        cells = sorted({(k[0], k[1]) for k in by})
        for t, c in cells:
            ja, en = by.get((t, c, "ja")), by.get((t, c, "en"))
            if not (ja and en):
                continue
            print(f"| {t} | {c} | {ja[0]:.3f} | {en[0]:.3f} | {en[0]-ja[0]:+.3f} |")
        return
    if a.what == "empty":
        g = collections.defaultdict(lambda: [0, 0, 0])
        for r in recs:
            v = g[(r["tool"], r["corpus"])]
            v[0] += 1
            v[1] += 1 if r["err"] == "empty_query" else 0
            v[2] += 1 if (r["err"] and r["err"] != "empty_query") else 0
        print("| 道具 | 題材 | 実行 | 質問が空（識別子なし） | 失敗 |")
        print("|---|---|--:|--:|--:|")
        for k, v in sorted(g.items()):
            print(f"| {k[0]} | {k[1]} | {v[0]} | {v[1]} | {v[2]} |")
        return
    if a.what == "cells":
        print("| 道具 | 題材 | n | " + a.metric + " |")
        print("|---|---|--:|--:|")
        for k, (v, n) in agg(recs, ["tool", "corpus"], a.metric).items():
            print(f"| {k[0]} | {k[1]} | {n} | {v:.3f} |")
        return
    print("| 系統 | 道具 | " + " | ".join(scen) + " | 全体 |")
    print("|---|---|" + "--:|" * (len(scen) + 1))
    fam = {r["tool"]: r["family"] for r in recs}
    by = agg(recs, ["tool", "scenario"], a.metric)
    tot = agg(recs, ["tool"], a.metric)
    for t in tools:
        row = []
        for s in scen:
            v = by.get((t, s))
            row.append(f"{v[0]:.3f}" if v else "―")
        row.append(f"**{tot[(t,)][0]:.3f}**")
        print(f"| {fam[t]} | {t} | " + " | ".join(row) + " |")


if __name__ == "__main__":
    main()
