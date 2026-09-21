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

Contrasts left out of the comparisons
-------------------------------------
A tool whose index holds no file of the kind a question's gold is made of
cannot answer that question at all; its 0.000 says what the index covers, not
how good the tool is. Those contrasts (l1/coverage.py) are measured and
reported, and left out of every table unless --include-na is given. PE3b left
out whole cells (c4, whose gold is Markdown throughout); PE3c applies the same
rule question by question, so the Markdown questions of c1 and c2 go with them
(pe3b-l1.md 第 11 節 #4).

Comparisons within a family
---------------------------
"Leave the contrast out" makes the tools of a family average over different
sets of questions, which favours the tool that indexes less (pe3b-l1.md 第 11
節 #2). Inside a family the tools are therefore compared over the questions
they both keep (`rep`, the column 共通の題材のみ).

Several records files
---------------------
--jsonl may be given more than once. A later file replaces the records of an
earlier one for the same (tool, corpus, query form, question), so PE3c's
re-measurement of S3 and S7 can be read together with PE3b's other scenarios.

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
import coverage as COV  # noqa: E402
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


def load(paths):
    """1 つ以上の記録。後の file が同じ検索の記録を置き換える。"""
    if isinstance(paths, str):
        paths = [paths]
    out = {}
    for path in paths:
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                out[(r["tool"], r["corpus"], r["query_lang"], r["id"])] = r
    return list(out.values())


def grade(recs):
    tasks = {t["id"]: t for t in T.load()}
    for r in recs:
        t = tasks.get(r["id"])
        if not t:
            continue
        r["m"] = metrics(r["paths"], t["qrels"], r["corpus"])
        r["na"] = COV.not_comparable(r["tool"], r["corpus"], t["qrels"])
    return recs


def comparable(recs):
    return [r for r in recs if not r.get("na")]


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
    ts = T.load()
    print("| 道具 | 形 | c1 | c2 | c3 | c4 | 外した理由 |")
    print("|---|---|:-:|:-:|:-:|:-:|---|")
    for tool, spec in R.TOOLS.items():
        marks, why = [], []
        for c in CORPORA:
            if c not in spec["corpora"]:
                marks.append("―")
                why.append(f"{c}: {R.NOT_APPLICABLE.get((tool, c), '?')}")
                continue
            qs = [t for t in ts if t["corpus"] == c]
            out = [t for t in qs if COV.not_comparable(tool, c, t["qrels"])]
            if not out:
                marks.append("○")
            elif len(out) == len(qs):
                marks.append("△")
                why.append(f"{c}: 全問 {COV.reason(tool, c)}")
            else:
                marks.append(f"◐{len(out)}")
                why.append(f"{c}: 索引に正解の種類が無い {len(out)}/{len(qs)} 問を外す"
                           f"（{COV.reason(tool, c)}）")
        print(f"| {tool} | {spec['form']} | " + " | ".join(marks) + " | " + "；".join(why) + " |")
    print()
    print("○ 全問を対比に使う／◐n そのうち n 問を外す／△ 測るが全問を対比から外す／― 組まない")
    print()
    print("| 道具 | 題材 | 検索 | 問い合わせが空 | 返したパスのうち .md |")
    print("|---|---|--:|--:|--:|")
    for k, v in sorted(seen.items()):
        print(f"| {k[0]} | {k[1]} | {v[0]} | {v[1]} | {v[2]} |")
    print()
    print("外した対比（索引に正解の種類が入らない道具 × 題材 × シナリオ。"
          "主の条件の問の数で数える）:")
    print()
    out = collections.Counter()
    for r in recs:
        if r.get("na") and primary_lang(r):
            out[(r["tool"], r["corpus"], r["scenario"])] += 1
    print("| 道具 | 題材 | シナリオ | 外した問 | 理由 |")
    print("|---|---|---|--:|---|")
    for (tool, c, s), n in sorted(out.items()):
        print(f"| {tool} | {c} | {s} | {n} | {COV.reason(tool, c)} |")
    print(f"\n合計 {sum(out.values())} 問（主の条件）")


def docblind(recs):
    """規則の根拠: 索引に Markdown が入らない道具は、その題材の文書の問に届かない。

    外す・外さないの判断は索引の中身（l1/coverage.py）で決めてあり、この表は
    その判断が測定と合うかを見るためのもの。外した問の平均が 0.000 でなければ
    規則が広すぎる。
    """
    print("| 道具 | 題材 | 外した問（索引に正解の種類が無い） | その平均 | 残した問 | その平均 |")
    print("|---|---|--:|--:|--:|--:|")
    recs = [r for r in recs if primary_lang(r) and "m" in r]
    for tool, c in sorted(set(COV.NO_MARKDOWN) | set(COV.ONLY_MARKDOWN)):
        sub = [r for r in recs if r["tool"] == tool and r["corpus"] == c]
        out = [r["m"]["ndcg10"] for r in sub if r.get("na")]
        kept = [r["m"]["ndcg10"] for r in sub if not r.get("na")]
        f = lambda v: f"{statistics.fmean(v):.3f}" if v else "―"  # noqa: E731
        print(f"| {tool} | {c} | {len(out)} | {f(out)} | {len(kept)} | {f(kept)} |")


def common_mean(recs, a, b):
    """道具 a と b を、両方が対比に使う問だけで比べた、題材で等しく重みづけた平均。

    「当てはまらない対比を外す」規則は道具ごとに問の集合を変えるので、
    そのままの平均は索引しない道具を有利にする（pe3b-l1.md 第 11 節 #2）。
    """
    keep = collections.defaultdict(dict)
    for r in recs:
        if r["tool"] in (a, b):
            keep[(r["corpus"], r["id"])][r["tool"]] = r["m"]["ndcg10"]
    per = collections.defaultdict(lambda: ([], []))
    for (c, _), v in keep.items():
        if a in v and b in v:
            per[c][0].append(v[a])
            per[c][1].append(v[b])
    if not per:
        return None
    n = sum(len(v[0]) for v in per.values())
    return (statistics.fmean([statistics.fmean(v[0]) for v in per.values()]),
            statistics.fmean([statistics.fmean(v[1]) for v in per.values()]),
            sorted(per), n)


def rep(recs, drop_scenarios=()):
    """L2 の代表の道具を選ぶ規則（PE3 第 13 節）を当てはめる。

    候補は L0 で索引が作れた題材が 2 つ以上ある道具。主の順序は主の条件の
    nDCG@10 を題材で等しく重みづけた平均（当てはまらない対比は平均に入れない）。
    同点（0.02 以内）は 1 回の検索の時間の中央値、次に L0 の索引の CPU 時間。
    系統の中の比べ方は「両方が対比に使う問だけ」（PE3c、pe3b-l1.md 第 11 節 #2）。
    """
    recs = [r for r in comparable(recs) if primary_lang(r) and "m" in r
            and r["scenario"] not in drop_scenarios]
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
    order = sorted(rows, key=lambda x: (x[0], -x[2]))
    for fam, tool, m, n, detail, ts in order:
        print(f"| {fam} | {tool} | {m:.3f} | {n} | {detail} | {ts:.2f} | "
              f"{'○' if n >= 2 else '×（題材が 1 つ）'} |")
    print()
    print("系統の中を、両方が対比に使う問だけで比べる:")
    print()
    print("| 系統 | 比べた 2 つ | 共通の問 | 題材 | 前者 | 後者 | 勝ち |")
    print("|---|---|--:|---|--:|--:|---|")
    fams = collections.defaultdict(list)
    for fam, tool, m, n, _d, _t in order:
        if n >= 2:
            fams[fam].append((tool, m))
    for fam, cand in fams.items():
        if len(cand) < 2:
            continue
        head = cand[0][0]
        for other, _m in cand[1:]:
            r = common_mean(recs, head, other)
            if not r:
                print(f"| {fam} | {head} 対 {other} | 0 | ― | ― | ― | "
                      f"**比べられない**（索引が持つ種類が重ならない） |")
                continue
            ma, mb, corp, n = r
            win = head if ma - mb > 0.02 else (other if mb - ma > 0.02 else "同点（0.02 以内）")
            print(f"| {fam} | {head} 対 {other} | {n} | {'・'.join(corp)} | "
                  f"{ma:.3f} | {mb:.3f} | {win} |")


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
    kept = comparable(recs)
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


def verify(a):
    """記録の問い合わせが、いまの規則で作り直したものと一致するかを確かめる。

    PE3c は S3・S7 だけを測り直し、他のシナリオは PE3b の記録をそのまま使う。
    それが許されるのは、定型を外す規則で問い合わせが変わるのが S3・S7 の 35 問
    だけだからで、ここはその前提そのものを記録に当てて確かめる。
    """
    tasks = {t["id"]: t for t in T.load()}
    same = collections.Counter()
    diff = collections.Counter()
    for r in load(a.jsonl):
        t = tasks.get(r["id"])
        if not t:
            continue
        want = R.query_for(t, r["form"], r["query_lang"])
        (same if want == r["query"] else diff)[(r["scenario"], r["form"])] += 1
    print("| シナリオ | 形 | いまの規則と同じ | 違う |")
    print("|---|---|--:|--:|")
    for k in sorted(set(same) | set(diff)):
        print(f"| {k[0]} | {k[1]} | {same[k]} | {diff[k]} |")
    print(f"\n一致 {sum(same.values())} 件、不一致 {sum(diff.values())} 件")
    files = collections.Counter()
    for r in load(a.jsonl):
        v = r.get("tasks")
        files[(v or {}).get("sha256", "（記録に無い）")] += 1
    print("\n| 問題ファイルの sha256 | 記録 |")
    print("|---|--:|")
    for k, v in files.most_common():
        print(f"| {k} | {v} |")


def compare3b(a):
    """PE3b（定型を外す前）と PE3c（外した後）の、同じ記録どうしの比べ。

    問題ファイルは同じ（`tasks` の sha256 が一致する）ので、こちらは報告の値
    ではなく記録そのものを採点し直して比べられる。PE3b でできなかったことで、
    記録に問題ファイルの版を持たせた理由でもある。
    """
    old = [r for r in grade(load(a.baseline_jsonl)) if primary_lang(r)]
    new = [r for r in grade(load(a.jsonl)) if primary_lang(r)]
    keys = {(r["tool"], r["corpus"], r["query_lang"], r["id"]) for r in new} & \
           {(r["tool"], r["corpus"], r["query_lang"], r["id"]) for r in old}
    o = comparable([r for r in old if (r["tool"], r["corpus"], r["query_lang"], r["id"]) in keys])
    n = comparable([r for r in new if (r["tool"], r["corpus"], r["query_lang"], r["id"]) in keys])
    print(f"同じ (道具, 題材, 形, 問) の記録 {len(keys)} 件、"
          f"うち対比に使うもの {len(n)} 件\n")
    for scen in ("S3", "S7"):
        print(f"### {scen}\n")
        print("| 道具 | 形 | PE3b | PE3c | 差 |")
        print("|---|---|--:|--:|--:|")
        bo = agg([r for r in o if r["scenario"] == scen], ["tool"])
        bn = agg([r for r in n if r["scenario"] == scen], ["tool"])
        form = {r["tool"]: r["form"] for r in n}
        for t in sorted(bn, key=lambda k: -bn[k][0]):
            if t not in bo:
                continue
            print(f"| {t[0]} | {form[t[0]]} | {bo[t][0]:.3f} | {bn[t][0]:.3f} | "
                  f"{bn[t][0]-bo[t][0]:+.3f} |")
        vo = [v for v, _ in bo.values()]
        vn = [v for v, _ in bn.values()]
        fo = floor_ceiling([r for r in o if r["scenario"] == scen])
        fn = floor_ceiling([r for r in n if r["scenario"] == scen])
        print(f"| **平均** | | **{statistics.fmean(vo):.3f}** | **{statistics.fmean(vn):.3f}** | "
              f"**{statistics.fmean(vn)-statistics.fmean(vo):+.3f}** |")
        print(f"| **幅** | | {max(vo)-min(vo):.3f} | {max(vn)-min(vn):.3f} | "
              f"{(max(vn)-min(vn))-(max(vo)-min(vo)):+.3f} |")
        print(f"| **床（全道具 0）** | | {fo} | {fn} | |")
        print()


def floor_ceiling(recs):
    byq = collections.defaultdict(list)
    for r in recs:
        if "m" in r:
            byq[r["id"]].append(r["m"]["ndcg10"])
    return f"{sum(1 for v in byq.values() if max(v) == 0)}/{len(byq)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", nargs="?", default="table",
                    choices=["table", "cells", "json", "time", "spread", "xlang", "empty",
                             "phrasing", "querylang", "applicability", "compare", "compare3b",
                             "docblind", "rep", "verify"])
    ap.add_argument("--jsonl", action="append",
                    help="記録。複数回渡すと、後のものが同じ検索の記録を置き換える"
                         "（既定: PE3b に PE3c の S3・S7 を重ねる）")
    ap.add_argument("--baseline", default=os.path.join(T.ROOT, "results", "pe3-l1.md"),
                    help="compare: 前回の報告（results/pe3-l1.md）")
    ap.add_argument("--lang", default="primary", help="primary | ja | en | id | all")
    ap.add_argument("--metric", default="ndcg10")
    ap.add_argument("--phrasing", choices=["identifier", "paraphrase"])
    ap.add_argument("--corpus")
    ap.add_argument("--scenario")
    ap.add_argument("--include-na", action="store_true",
                    help="索引に正解の種類が無い対比も表に入れる")
    ap.add_argument("--baseline-jsonl", default=os.path.join(T.ROOT, "results", "pe3b-l1.jsonl"),
                    help="compare3b: 定型を外す前の記録")
    ap.add_argument("--drop-scenarios", default="",
                    help="rep: 平均から外すシナリオ（S3,S7 のように）")
    a = ap.parse_args()
    if not a.jsonl:
        a.jsonl = [os.path.join(T.ROOT, "results", "pe3b-l1.jsonl"),
                   os.path.join(T.ROOT, "results", "pe3c-l1.jsonl")]
        a.jsonl = [p for p in a.jsonl if os.path.exists(p)]
    if a.what == "applicability":
        applicability(grade(load(a.jsonl)))
        return
    if a.what == "compare":
        compare(a)
        return
    if a.what == "compare3b":
        compare3b(a)
        return
    if a.what == "verify":
        verify(a)
        return
    if a.what == "docblind":
        docblind(grade(load(a.jsonl)))
        return
    if a.what == "rep":
        rep(grade(load(a.jsonl)), tuple(s for s in a.drop_scenarios.split(",") if s))
        return
    recs = grade(load(a.jsonl))
    if not a.include_na:
        recs = comparable(recs)
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
            allr = comparable(allr)
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
