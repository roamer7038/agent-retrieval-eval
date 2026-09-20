#!/usr/bin/env python3
"""PE5 の較正の集計: 費用の定義の比べ方、分散の成分、道具の使用率、失敗の率。

    pe5/analyze.py [--scores results/pe5-l2.jsonl] [--manifest results/pe5-manifest.jsonl]
                   [--json pe5/estimates.json]

標準ライブラリだけで動く（この機械に numpy・R が無いため）。設計は升目ごとに
同じ回数を流す釣り合った形なので、分散の成分は分散分析の積率推定で足りる。

費用の 3 つの定義
  usd        実際に請求される額（`grade/grade.py` の `usd`。キャッシュの読みは
             入力の 0.1 倍、書きは 1.25〜2 倍）
  usd_nocache キャッシュが無かったとしたらいくらか（読みも書きも入力の単価で
             数え直す）。同じトークン数なら順番に依らない
  usd_warm   温めた腕（arm=warm）の usd
"""
import argparse
import collections
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES = json.load(open(os.path.join(ROOT, "grade", "prices.json")))


def load(scores, manifest):
    rows = [json.loads(l) for l in open(scores) if l.strip()]
    # セッションの名前は問題・条件・モデル・回だけで決まるので、ブロックをまたぐと
    # ぶつかる（var と cacheB は同じ升目を含む）。**ラベル込みで**突き合わせる。
    man = {(j.get("label"), j["session"]): j for j in map(json.loads, open(manifest)) if l_ok(j)}
    out = []
    for r in rows:
        m = man.get((r.get("label"), r["session"]))
        if m is None:
            continue
        r = dict(r)
        r.update({k: m[k] for k in ("block", "arm", "role", "round", "order")})
        tok = next(iter(r["tokens"].values()), {}) if r.get("tokens") else {}
        model = next(iter(r["tokens"]), None) if r.get("tokens") else None
        p = PRICES.get(model or "", {})
        if p:
            r["usd_nocache"] = ((tok.get("inputTokens", 0) + tok.get("cacheReadInputTokens", 0)
                                 + tok.get("cacheCreationInputTokens", 0)) * p["input"]
                                + tok.get("outputTokens", 0) * p["output"]) / 1e6
        rd, wr = tok.get("cacheReadInputTokens", 0), tok.get("cacheCreationInputTokens", 0)
        r["cache_read_frac"] = rd / (rd + wr) if rd + wr else 0.0
        r["cache_write_tokens"] = wr
        r["wall_s"] = r.get("wall_ms", 0) / 1000
        out.append(r)
    return out


def l_ok(j):
    return "session" in j


# ---- 記述統計 --------------------------------------------------------------

def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def var(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def sd(xs):
    return math.sqrt(var(xs))


def median(xs):
    s = sorted(xs)
    n = len(s)
    return float("nan") if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


# ---- 分散の成分 ------------------------------------------------------------

def components(rows, value, by_q="question", by_c="cond"):
    """問題 × 条件の釣り合った交差計画から、分散の成分を積率で推定する。

    y_qcr = μ + α_c + b_q + (bα)_qc + ε_r
      σ²_rep = MS_e、σ²_qc = (MS_qc − MS_e)/r、σ²_q = (MS_q − MS_qc)/(r·C)
    負になったものは 0 に丸める（積率推定の常）。"""
    cells = collections.defaultdict(list)
    for r in rows:
        v = value(r)
        if v is not None:
            cells[(r[by_q], r[by_c])].append(v)
    all_qs = sorted({q for q, _ in cells})
    cs = sorted({c for _, c in cells})
    if not all_qs or not cs:
        return None
    # 釣り合った部分設計を選ぶ。反復 r を大きい方から試し、全ての条件で r 回
    # 以上ある問題だけを残す。残差の自由度 Q·C·(r−1) が最大になる r を採る。
    best = None
    for rr in range(max(len(v) for v in cells.values()), 0, -1):
        qs = [q for q in all_qs if all(len(cells.get((q, c), [])) >= rr for c in cs)]
        if len(qs) < 2:
            continue
        df = len(qs) * len(cs) * (rr - 1)
        if best is None or (df, len(qs)) > (best[0], len(best[1])):
            best = (df, qs, rr)
    if best is None:
        return None
    _, qs, rr = best
    y = {(q, c): cells[(q, c)][:rr] for q in qs for c in cs}
    full = [(q, c) for q in qs for c in cs]
    Q, C = len(qs), len(cs)
    allv = [v for q, c in full for v in y[(q, c)]]
    gm = mean(allv)
    cell_m = {k: mean(y[k]) for k in full}
    qm = {q: mean([cell_m[(q, c)] for c in cs]) for q in qs}
    cm = {c: mean([cell_m[(q, c)] for q in qs]) for c in cs}
    ss_e = sum((v - cell_m[k]) ** 2 for k in full for v in y[k])
    ss_qc = rr * sum((cell_m[(q, c)] - qm[q] - cm[c] + gm) ** 2 for q in qs for c in cs)
    ss_q = rr * C * sum((qm[q] - gm) ** 2 for q in qs)
    ss_c = rr * Q * sum((cm[c] - gm) ** 2 for c in cs)
    df_e, df_qc, df_q, df_c = Q * C * (rr - 1), (Q - 1) * (C - 1), Q - 1, C - 1
    ms_e = ss_e / df_e if df_e else 0.0
    ms_qc = ss_qc / df_qc if df_qc else 0.0
    ms_q = ss_q / df_q if df_q else 0.0
    ms_c = ss_c / df_c if df_c else 0.0
    return {
        "n_questions": Q, "n_conds": C, "reps": rr, "grand_mean": gm,
        "var_rep": ms_e,
        "var_qc": max(0.0, (ms_qc - ms_e) / rr),
        "var_q": max(0.0, (ms_q - ms_qc) / (rr * C)),
        "ms": {"e": ms_e, "qc": ms_qc, "q": ms_q, "c": ms_c},
        "F_cond": (ms_c / ms_qc) if ms_qc else float("inf"),
        "df_cond": [df_c, df_qc],
        "cond_means": cm,
    }


def scenario_interaction(rows, value):
    """条件 × シナリオの交互作用。(題材, シナリオ) の升目に問題が 2 問以上ある
    ものだけを使い、σ²_c×s（シナリオの水準の交互作用）と σ²_c×q（同じシナリオの
    中の問題ごとの交互作用）を分ける。2 問未満しかなければ分けられない。"""
    by_cell = collections.defaultdict(set)
    for r in rows:
        by_cell[(r["corpus"], r["scenario"])].add(r["question"])
    usable = {k for k, v in by_cell.items() if len(v) >= 2}
    sub = [r for r in rows if (r["corpus"], r["scenario"]) in usable]
    if not sub:
        return {"estimable": False, "reason": "(題材, シナリオ) あたり 2 問以上の升目が無い"}
    comp = components(sub, value, by_q="question")
    # シナリオを単位にした交差計画（問題の平均をとる）
    agg = collections.defaultdict(list)
    for r in sub:
        v = value(r)
        if v is not None:
            agg[((r["corpus"], r["scenario"]), r["cond"], r["question"])].append(v)
    qmeans = [{"question": q, "cond": c, "scen": f"{s[0]}-{s[1]}", "v": mean(vs)}
              for (s, c, q), vs in agg.items()]
    scomp = components(qmeans, lambda r: r["v"], by_q="scen", by_c="cond")
    return {"estimable": True, "cells": sorted("-".join(k) for k in usable),
            "within": comp, "scenario_level": scomp}


# ---- 報告 ------------------------------------------------------------------

def fmt(x, n=3):
    return "―" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{n}f}"


def report(rows, out_json):
    est = {}
    meas = [r for r in rows if r["role"] == "measure"]
    print(f"# セッション {len(rows)}（測定 {len(meas)}、捨て {len(rows) - len(meas)}）"
          f" 合計 {sum(r.get('usd') or 0 for r in rows):.3f} USD")

    # 1. 費用の定義とキャッシュの腕
    print("\n## 1. 費用の定義とキャッシュの扱い")
    print("| 腕 | 定義 | n | 中央 USD | 平均 USD | sd(log) 升目内 | 升目間/升目内 | "
          "条件の F | cache 読みの割合 中央 |")
    print("|---|---|--:|--:|--:|--:|--:|--:|--:|")
    cache_rows = [r for r in meas if r["block"] in ("cacheA", "cacheB")]
    cache_tbl = {}
    for arm, label in (("warm", "cacheA（温める）"), ("rand", "cacheB（無作為）")):
        sub = [r for r in cache_rows if r["arm"] == arm]
        if not sub:
            continue
        for key, name in (("usd", "usd（請求どおり）"), ("usd_nocache", "usd_nocache")):
            comp = components(sub, lambda r, k=key: math.log(r[k]) if r.get(k) else None)
            vals = [r[key] for r in sub if r.get(key)]
            ratio = (comp["var_q"] + comp["var_qc"]) / comp["var_rep"] if comp and comp["var_rep"] else float("nan")
            print(f"| {label} | {name} | {len(vals)} | {fmt(median(vals), 4)} | {fmt(mean(vals), 4)} | "
                  f"{fmt(math.sqrt(comp['var_rep']) if comp else float('nan'))} | {fmt(ratio, 2)} | "
                  f"{fmt(comp['F_cond'] if comp else float('nan'), 1)} | "
                  f"{fmt(median([r['cache_read_frac'] for r in sub]), 2)} |")
            cache_tbl[(arm, key)] = {"comp": comp, "median": median(vals), "mean": mean(vals),
                                     "n": len(vals)}
    est["cache"] = {f"{a}/{k}": {"median_usd": v["median"], "mean_usd": v["mean"], "n": v["n"],
                                 "var_rep": v["comp"] and v["comp"]["var_rep"],
                                 "var_qc": v["comp"] and v["comp"]["var_qc"],
                                 "var_q": v["comp"] and v["comp"]["var_q"],
                                 "F_cond": v["comp"] and v["comp"]["F_cond"]}
                    for (a, k), v in cache_tbl.items()}
    warm_cost = sum(r.get("usd") or 0 for r in rows if r["role"] == "warmup")
    est["warmup_overhead_usd"] = warm_cost
    print(f"\n捨てセッションの費用: {warm_cost:.3f} USD（{sum(1 for r in rows if r['role'] == 'warmup')} 本）")

    # 2. 分散の成分
    print("\n## 2. 分散の成分（測定のブロック全部）")
    print("| 指標 | 問題 | 条件 | 反復 | σ²_rep | σ²_q×c | σ²_q | σ_rep | σ_q | σ²_q/σ²_rep |")
    print("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    var_rows = [r for r in meas if r["block"] == "var"]
    metrics = {
        "log(usd)": lambda r: math.log(r["usd"]) if r.get("usd") else None,
        "log(usd_nocache)": lambda r: math.log(r["usd_nocache"]) if r.get("usd_nocache") else None,
        "log(wall_s)": lambda r: math.log(r["wall_s"]) if r.get("wall_s") else None,
        "correct(0/1)": lambda r: 1.0 if r.get("correct") else 0.0,
    }
    est["components"] = {}
    for name, f in metrics.items():
        comp = components(var_rows, f)
        if not comp:
            continue
        est["components"][name] = comp
        print(f"| {name} | {comp['n_questions']} | {comp['n_conds']} | {comp['reps']} | "
              f"{fmt(comp['var_rep'], 4)} | {fmt(comp['var_qc'], 4)} | {fmt(comp['var_q'], 4)} | "
              f"{fmt(math.sqrt(comp['var_rep']))} | {fmt(math.sqrt(comp['var_q']))} | "
              f"{fmt(comp['var_q'] / comp['var_rep'] if comp['var_rep'] else float('inf'), 1)} |")

    # 3. 条件 × シナリオ
    print("\n## 3. 条件 × シナリオの交互作用")
    for name in ("log(usd_nocache)", "log(wall_s)"):
        si = scenario_interaction(var_rows, metrics[name])
        est.setdefault("scenario_interaction", {})[name] = si
        if not si.get("estimable"):
            print(f"- {name}: 分けられない（{si['reason']}）")
        else:
            w, s = si["within"], si["scenario_level"]
            print(f"- {name}: 使えた升目 {', '.join(si['cells'])}。"
                  f"σ²_c×q（同じシナリオの中）= {fmt(w['var_qc'], 4) if w else '―'}、"
                  f"σ²_c×s（シナリオの間）= {fmt(s['var_qc'], 4) if s else '―'}")

    # 4. 道具の使用率
    print("\n## 4. 道具の使用率（条件ごと、Wilson の 95% 区間）")
    print("| 条件 | n | 使った | 率 | 95% 区間 | 呼び出しの中央 | 返ったバイトの中央 |")
    print("|---|--:|--:|--:|---|--:|--:|")
    est["tool_use"] = {}
    for cond in sorted({r["cond"] for r in meas}):
        sub = [r for r in meas if r["cond"] == cond and r["added_tools"]]
        if not sub:
            continue
        k = sum(1 for r in sub if r.get("added_tool_used"))
        lo, hi = wilson(k, len(sub))
        used = [r for r in sub if r.get("added_tool_used")]
        print(f"| {cond} | {len(sub)} | {k} | {k / len(sub):.2f} | [{lo:.2f}, {hi:.2f}] | "
              f"{fmt(median([r['added_tool_calls'] for r in used]), 1)} | "
              f"{fmt(median([r['added_tool_bytes'] for r in used]), 0)} |")
        est["tool_use"][cond] = {"n": len(sub), "used": k, "rate": k / len(sub), "ci": [lo, hi]}

    # 5. 失敗・停止
    print("\n## 5. 失敗・停止・無効な回答")
    print("| 条件 | n | 時間切れ | exit≠0 | answer.json が無効 | result≠success | MCP が未接続 |")
    print("|---|--:|--:|--:|--:|--:|--:|")
    est["failures"] = {}
    for cond in sorted({r["cond"] for r in rows}):
        sub = [r for r in rows if r["cond"] == cond]
        f = {"timed_out": sum(1 for r in sub if r.get("timed_out")),
             "exit": sum(1 for r in sub if r.get("exit")),
             "invalid": sum(1 for r in sub if not r.get("answer_valid")),
             "not_success": sum(1 for r in sub if r.get("result_subtype") != "success"),
             "mcp_bad": sum(1 for r in sub if any(v != "connected" for v in (r.get("mcp_servers") or {}).values()))}
        est["failures"][cond] = {"n": len(sub), **f}
        print(f"| {cond} | {len(sub)} | {f['timed_out']} | {f['exit']} | {f['invalid']} | "
              f"{f['not_success']} | {f['mcp_bad']} |")

    # 6. 正答率（同等性の検定の下ごしらえ）
    print("\n## 6. 正答率と問題の中の相関")
    print("| 条件 | n | 正答 | 率 | 95% 区間 |")
    print("|---|--:|--:|--:|---|")
    est["accuracy"] = {}
    for cond in sorted({r["cond"] for r in meas}):
        sub = [r for r in meas if r["cond"] == cond]
        k = sum(1 for r in sub if r.get("correct"))
        lo, hi = wilson(k, len(sub))
        est["accuracy"][cond] = {"n": len(sub), "correct": k, "rate": k / len(sub) if sub else None,
                                 "ci": [lo, hi]}
        print(f"| {cond} | {len(sub)} | {k} | {k / len(sub):.2f} | [{lo:.2f}, {hi:.2f}] |")

    # 7. 題材・シナリオごとの費用と時間（本番の見積もりのため）
    print("\n## 7. 題材ごとの 1 セッション")
    print("| 題材 | n | 中央 USD | 中央 usd_nocache | 中央 実時間 s |")
    print("|---|--:|--:|--:|--:|")
    est["per_corpus"] = {}
    for c in sorted({r["corpus"] for r in meas}):
        sub = [r for r in meas if r["corpus"] == c]
        e = {"n": len(sub), "median_usd": median([r["usd"] for r in sub if r.get("usd")]),
             "median_usd_nocache": median([r["usd_nocache"] for r in sub if r.get("usd_nocache")]),
             "median_wall_s": median([r["wall_s"] for r in sub])}
        est["per_corpus"][c] = e
        print(f"| {c} | {e['n']} | {fmt(e['median_usd'], 4)} | {fmt(e['median_usd_nocache'], 4)} | "
              f"{fmt(e['median_wall_s'], 1)} |")

    if out_json:
        with open(out_json, "w") as f:
            json.dump(est, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n（推定値を {os.path.relpath(out_json, ROOT)} に書いた）", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", default=os.path.join(ROOT, "results", "pe5-l2.jsonl"))
    ap.add_argument("--manifest", default=os.path.join(ROOT, "results", "pe5-manifest.jsonl"))
    ap.add_argument("--json", default=os.path.join(ROOT, "pe5", "estimates.json"))
    a = ap.parse_args()
    report(load(a.scores, a.manifest), a.json)


if __name__ == "__main__":
    main()
