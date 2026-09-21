#!/usr/bin/env python3
"""L1: the metrics of results/pe3b-l1.jsonl.

    l1/grade.py [--jsonl FILE] [--out DIR] [table|cells|json]

Metrics (plan v3.1, with the decision of 2026-09-20)
---------------------------------------------------
  nDCG@10   main, for every scenario S7 included. Binary gains: every relevant
            file has grade 1, so DCG = sum(1/log2(i+1)) over the relevant
            files in the top 10 and IDCG = sum(1/log2(i+1)) over
            i = 1..min(10, |R|).
  recall@10 |top10 & R| / |R|. For S7, whose R can hold hundreds of files,
            it is capped by 10/|R|; that is a property of the task, reported
            as such.
  MRR@10    1/(rank of the first relevant file), 0 when there is none.
  F1@10     kept as a secondary number only. The plan asked for F1 on S7, but
            |R| runs from 1 to 489 and F1@10 then has a ceiling of 0.04..0.77
            set by |R| rather than by the tool, so F1 is used at L2 (where the
            answer is a set of names) and not as L1's metric
            (`--metric f1_10` still prints it).

Cells left out of the comparisons
---------------------------------
A tool whose index holds no file of the kind a corpus's gold is made of
cannot answer that corpus at all; its 0.000 says what the index covers, not
how good the tool is. Those cells (l1/run.py: NOT_COMPARABLE) are measured
and reported, and left out of every table unless --include-na is given.

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
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks as T  # noqa: E402
import run as R  # noqa: E402

K = 10
CORPORA = ("c1", "c2", "c3", "c4")


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


def applicability(recs):
    """道具 × 題材の当てはまり: 組んだ・測った・対比に使うかを 1 枚にする。"""
    seen = collections.defaultdict(lambda: [0, 0, 0])  # 検索・空の問い合わせ・文書を返した数
    for r in recs:
        v = seen[(r["tool"], r["corpus"])]
        v[0] += 1
        v[1] += 1 if r["err"] == "empty_query" else 0
        v[2] += sum(1 for p, _ in r["paths"] if p.endswith(".md"))
    print("| 道具 | 形 | c1 | c2 | c3 | c4 | 外した理由 |")
    print("|---|---|:-:|:-:|:-:|:-:|---|")
    for tool, spec in R.TOOLS.items():
        marks, why = [], []
        for c in CORPORA:
            if (tool, c) in R.NOT_COMPARABLE:
                marks.append("△")
                why.append(f"{c}: {R.NOT_COMPARABLE[(tool, c)]}")
            elif c in spec["corpora"]:
                marks.append("○")
            else:
                marks.append("―")
                why.append(f"{c}: {R.NOT_APPLICABLE.get((tool, c), '?')}")
        print(f"| {tool} | {spec['form']} | " + " | ".join(marks) + " | " + "；".join(why) + " |")
    print()
    print("○ 測って対比に使う／△ 測るが対比から外す（索引に題材の答えの種類が無い）／― 組まない")
    print()
    print("| 道具 | 題材 | 検索 | 問い合わせが空 | 返したパスのうち .md |")
    print("|---|---|--:|--:|--:|")
    for k, v in sorted(seen.items()):
        print(f"| {k[0]} | {k[1]} | {v[0]} | {v[1]} | {v[2]} |")


def docblind(recs):
    """索引に文書が入らない道具は c4 だけでなく c1 の S4・S5 にも届かない。"""
    print("| 道具 | c1 の S4・S5 | c1 の S1・S2・S3・S7・S9 | c1 全体 | 対比から外した c4 |")
    print("|---|--:|--:|--:|--:|")
    recs = [r for r in recs if primary_lang(r) and "m" in r]
    for tool in R.DOC_BLIND:
        sub = [r for r in recs if r["tool"] == tool and r["corpus"] == "c1"]
        doc = [r["m"]["ndcg10"] for r in sub if r["scenario"] in ("S4", "S5")]
        code = [r["m"]["ndcg10"] for r in sub if r["scenario"] not in ("S4", "S5")]
        c4 = [r["m"]["ndcg10"] for r in recs if r["tool"] == tool and r["corpus"] == "c4"]
        f = lambda v: f"{statistics.fmean(v):.3f}" if v else "―"  # noqa: E731
        print(f"| {tool} | {f(doc)} | {f(code)} | {f(doc + code)} | {f(c4)} |")


def rep(recs):
    """L2 の代表の道具を選ぶ規則（PE3 第 13 節）を当てはめる。

    候補は L0 で索引が作れた題材が 2 つ以上ある道具。主の順序は主の条件の
    nDCG@10 を題材で等しく重みづけた平均（当てはまらない組は平均に入れない）。
    同点（0.02 以内）は 1 回の検索の時間の中央値、次に L0 の索引の CPU 時間。
    """
    recs = [r for r in recs if primary_lang(r) and "m" in r
            and (r["tool"], r["corpus"]) not in R.NOT_COMPARABLE]
    by = agg(recs, ["tool", "corpus"])
    t_s = collections.defaultdict(list)
    for r in recs:
        if r["err"] != "empty_query":
            t_s[r["tool"]].append(r["t_s"] or 0)
    rows = []
    for tool in R.TOOLS:
        per = {k[1]: v[0] for k, v in by.items() if k[0] == tool}
        if not per:
            continue
        rows.append((R.TOOLS[tool]["family"], tool, statistics.fmean(per.values()), len(per),
                     "、".join(f"{c} {per[c]:.3f}" for c in CORPORA if c in per),
                     statistics.median(t_s[tool]) if t_s[tool] else float("nan")))
    print("| 系統 | 道具 | 題材を等しく重みづけた nDCG@10 | 題材数 | 内訳 | 検索 s 中央 | 候補 |")
    print("|---|---|--:|--:|---|--:|:-:|")
    for fam, tool, m, n, detail, ts in sorted(rows, key=lambda x: (x[0], -x[2])):
        print(f"| {fam} | {tool} | {m:.3f} | {n} | {detail} | {ts:.2f} | "
              f"{'○' if n >= 2 else '×（題材が 1 つ）'} |")


def read_report_table(path):
    """前回の報告（results/pe3-l1.md）の「道具 × シナリオ」の表を読む。

    PE3 が測った 238 問の問題は PE2b〜PE2d の作り直しで id ごと入れ替わって
    おり、当時の tasks/dev/pe2.jsonl は残っていない。前回の記録を採点し直す
    ことはできないので、比較は**前回の報告に載せた値**と今回の値の間で行う。
    """
    out, hit = {}, False
    with open(path) as f:
        for line in f:
            if line.startswith("## 4."):
                hit = True
                continue
            if hit and line.startswith("## "):
                break
            if not (hit and line.startswith("|")):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != len(T.L1_SCENARIOS) + 3 or cells[0] in ("系統", "---"):
                continue
            tool = re.sub(r"（.*", "", cells[1]).strip().lower()
            vals = {}
            for s, c in zip(T.L1_SCENARIOS, cells[2:-1]):
                c = c.strip("*")
                if c not in ("―", ""):
                    vals[s] = float(c)
            vals["全体"] = float(cells[-1].strip("*"))
            out[tool] = vals
    return out


def compare(a):
    """前回の報告（288 問）と今回の測定（354 問）の道具ごとの順位の差。"""
    old = read_report_table(a.baseline)
    recs = [r for r in grade(load(a.jsonl)) if primary_lang(r)]
    kept = [r for r in recs if (r["tool"], r["corpus"]) not in R.NOT_COMPARABLE]
    new_all = agg(recs, ["tool"], a.metric)           # 前回と同じ数え方（c4 の 0 も入れる）
    new_kept = agg(kept, ["tool"], a.metric)          # 今回の決定（当てはまらない組を外す）
    new_s = agg(kept, ["tool", "scenario"], a.metric)
    rank = lambda d: {t: i + 1 for i, t in  # noqa: E731
                      enumerate(sorted(d, key=lambda t: -d[t]))}
    ro = rank({t: v["全体"] for t, v in old.items()})
    rn = rank({k[0]: v[0] for k, v in new_all.items()})
    print("| 道具 | 前回（288 問） | 今回（354 問、同じ数え方） | 差 | 前回の順位 | 今回の順位 | 順位の差 "
          "| 今回（当てはまらない組を外す） |")
    print("|---|--:|--:|--:|--:|--:|--:|--:|")
    for t in sorted(set(ro) | set(rn), key=lambda t: rn.get(t, 99)):
        o = old.get(t, {}).get("全体")
        n = new_all.get((t,))
        k = new_kept.get((t,))
        if o is None or n is None:
            print(f"| {t} | {o if o is not None else '―'} | "
                  f"{f'{n[0]:.3f}' if n else '―'} | ― | ― | ― | ― | ― |")
            continue
        kept_s = f"{k[0]:.3f}" if k and abs(k[0] - n[0]) > 5e-4 else "同じ"
        print(f"| {t} | {o:.3f} | {n[0]:.3f} | {n[0]-o:+.3f} | {ro[t]} | {rn[t]} | "
              f"{ro[t]-rn[t]:+d} | {kept_s} |")
    print()
    print("| シナリオ | 前回の上位 3 | 今回の上位 3 | 前回の平均 | 今回の平均 |")
    print("|---|---|---|--:|--:|")
    for s in T.L1_SCENARIOS:
        o = {t: v[s] for t, v in old.items() if s in v}
        n = {k[0]: v[0] for k, v in new_s.items() if k[1] == s}
        top = lambda d: "、".join(f"{t} {d[t]:.3f}" for t in  # noqa: E731
                                 sorted(d, key=lambda t: -d[t])[:3]) or "―"
        print(f"| {s} | {top(o)} | {top(n)} | "
              f"{statistics.fmean(o.values()):.3f} | {statistics.fmean(n.values()):.3f} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", nargs="?", default="table",
                    choices=["table", "cells", "json", "time", "spread", "xlang", "empty",
                             "phrasing", "querylang", "applicability", "compare", "docblind", "rep"])
    ap.add_argument("--jsonl", default=os.path.join(T.ROOT, "results", "pe3b-l1.jsonl"))
    ap.add_argument("--baseline", default=os.path.join(T.ROOT, "results", "pe3-l1.md"),
                    help="compare: 前回の報告（results/pe3-l1.md）")
    ap.add_argument("--lang", default="primary", help="primary | ja | en | id | all")
    ap.add_argument("--metric", default="ndcg10")
    ap.add_argument("--phrasing", choices=["identifier", "paraphrase"])
    ap.add_argument("--corpus")
    ap.add_argument("--scenario")
    ap.add_argument("--include-na", action="store_true",
                    help="索引に題材の答えの種類が無い組み合わせも表に入れる")
    a = ap.parse_args()
    if a.what == "applicability":
        applicability(grade(load(a.jsonl)))
        return
    if a.what == "compare":
        compare(a)
        return
    if a.what == "docblind":
        docblind(grade(load(a.jsonl)))
        return
    if a.what == "rep":
        rep(grade(load(a.jsonl)))
        return
    recs = grade(load(a.jsonl))
    if not a.include_na:
        recs = [r for r in recs if (r["tool"], r["corpus"]) not in R.NOT_COMPARABLE]
    if a.scenario:
        recs = [r for r in recs if r["scenario"] == a.scenario]
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
        if not a.include_na:
            allr = [r for r in allr if (r["tool"], r["corpus"]) not in R.NOT_COMPARABLE]
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
