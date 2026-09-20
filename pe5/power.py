#!/usr/bin/env python3
"""本番の問題数と反復数を決めるための検出力の計算（標準ライブラリだけ）。

    pe5/power.py [--estimates pe5/estimates.json] [--budgets 200,400,800]

考え方
------
本番の L2 は、同じ問題を全ての条件で流す**対応のある**設計である。すると問題の
主効果 σ²_q は条件どうしの差で消え、差の平均のばらつきは

    Var(d̄) = (2/n)·(σ²_q×c + σ²_rep/r)

になる（n は問題数、r は 1 升目の反復数）。σ²_q がいくら大きくても検出力には
効かない代わりに、**反復 r は第 2 項しか減らさない**。σ²_q×c ≫ σ²_rep/r なら
r を増やしても Var(d̄) はほとんど下がらないので、同じ予算なら n を増やす方が
よい。この境目を数値で示すのがこの計算である。

費用の主要な仮説（H2b）は費用の比なので、log をとった差 δ = ln(比) を検出する。
正答（H2a）は ±5 ポイントの同等性（TOST）で、問題を単位にした対応のある差の
分散から検出力を求める（二項の反復はモンテカルロで確かめる）。
"""
import argparse
import json
import math
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 本番の条件（A0 は記憶の上限を測る別枠で、対比の族には入れない）。
N_CONDS = 7
# 主要な仮説の族（H1a・H1b・H2a・H2b）に Holm。最も厳しい段の α を使う。
FAMILY = 4
ALPHA = 0.05


# ---- 分布（標準ライブラリだけ） ------------------------------------------

def norm_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def norm_q(p):
    """逆正規（Acklam の近似、絶対誤差 ~1e-9）。"""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q, r = p - 0.5, (p - 0.5) ** 2
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def betacf(a, b, x):
    tiny, eps = 1e-30, 3e-16
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        de = d * c
        h *= de
        if abs(de - 1) < eps:
            break
    return h


def betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lb = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
          + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return math.exp(lb) * betacf(a, b, x) / a
    return 1 - math.exp(lb) * betacf(b, a, 1 - x) / b


def t_cdf(t, df):
    x = df / (df + t * t)
    p = 0.5 * betai(df / 2, 0.5, x)
    return 1 - p if t > 0 else p


def t_q(p, df):
    """t の分位点（二分法）。"""
    lo, hi = -50.0, 50.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---- 検出力 ----------------------------------------------------------------

def power_ratio(delta, n, r, var_qc, var_rep, alpha=ALPHA / FAMILY):
    """対応のある 2 条件の log の差 δ を両側で検出する力（t 検定、df = n−1）。"""
    se = math.sqrt(2 * (var_qc + var_rep / r) / n)
    if se == 0:
        return 1.0
    tc = t_q(1 - alpha / 2, n - 1)
    nc = delta / se
    return (1 - t_cdf(tc - nc, n - 1)) + t_cdf(-tc - nc, n - 1)


def mde_ratio(n, r, var_qc, var_rep, power=0.8, alpha=ALPHA / FAMILY):
    """検出力 power を与える最小の効果（費用の比）。"""
    lo, hi = 1e-4, 5.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if power_ratio(mid, n, r, var_qc, var_rep, alpha) < power:
            lo = mid
        else:
            hi = mid
    return math.exp((lo + hi) / 2)


def power_tost(n, r, p, tau, margin=0.05, alpha=ALPHA / FAMILY, iters=2000, seed=7):
    """正答の同等性（±margin）の検出力。問題ごとの難しさを logit 上の正規乱数
    （sd = tau）で表し、条件の間に真の差を置かずに、対応のある差の TOST が
    「同等」と言える割合を数える。"""
    rng = random.Random(seed)
    lo = math.log(p / (1 - p))
    hit = 0
    for _ in range(iters):
        ds = []
        for _ in range(n):
            b = rng.gauss(0, tau)
            pi = 1 / (1 + math.exp(-(lo + b)))
            a_ = sum(1 for _ in range(r) if rng.random() < pi) / r
            b_ = sum(1 for _ in range(r) if rng.random() < pi) / r
            ds.append(a_ - b_)
        m = sum(ds) / n
        s = math.sqrt(sum((d - m) ** 2 for d in ds) / (n - 1) / n) if n > 1 else 0
        if s == 0:
            hit += abs(m) < margin
            continue
        tc = t_q(1 - alpha, n - 1)
        if (m - margin) / s < -tc and (m + margin) / s > tc:
            hit += 1
    return hit / iters


# ---- 表 --------------------------------------------------------------------

def table(est, budgets, cost_per_session, var_qc, var_rep, acc_p, acc_tau, n_max):
    print("\n## 予算ごとの問題数 × 反復数（条件 7、1 問あたり "
          f"{cost_per_session:.3f} USD と仮定、問題は最大 {n_max}）")
    print("| 予算 USD | セッション数 | 反復 r | 問題数 n | 使うセッション | "
          "検出できる費用の比（検出力 80%） | 同 90% | 比 1.3 の検出力 | 正答の同等性 ±5pt の検出力 |")
    print("|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    rows = []
    for b in budgets:
        n_sessions = int(b / cost_per_session)
        for r in (1, 2, 3):
            n = min(n_max, n_sessions // (N_CONDS * r))
            if n < 4:
                continue
            m80 = mde_ratio(n, r, var_qc, var_rep, 0.8)
            m90 = mde_ratio(n, r, var_qc, var_rep, 0.9)
            p13 = power_ratio(math.log(1.3), n, r, var_qc, var_rep)
            pe = power_tost(n, r, acc_p, acc_tau)
            used = n * N_CONDS * r
            rows.append(dict(budget=b, sessions=n_sessions, r=r, n=n, used=used, mde80=m80,
                             mde90=m90, power13=p13, power_tost=pe))
            print(f"| {b} | {n_sessions} | {r} | {n} | {used}（{used * cost_per_session:.0f} USD） | "
                  f"{m80:.3f} | {m90:.3f} | {p13:.2f} | {pe:.2f} |")
    return rows


def fixed_n_table(var_qc, var_rep):
    print("\n## 同じセッション数を n と r にどう割るか（セッション数を固定）")
    print("反復 r は σ²_rep しか減らさない。σ²_q×c が残るので、同じセッション数なら "
          "n を増やす方が有利かどうかを数で見る。")
    print("\n| セッション数 | r=1 の n → 比の MDE | r=2 | r=3 | r=5 |")
    print("|--:|---|---|---|---|")
    for N in (700, 1400, 2800):
        cells = []
        for r in (1, 2, 3, 5):
            n = N // (N_CONDS * r)
            cells.append(f"n={n} → {mde_ratio(n, r, var_qc, var_rep):.3f}" if n >= 4 else "―")
        print(f"| {N} | " + " | ".join(cells) + " |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--estimates", default=os.path.join(ROOT, "pe5", "estimates.json"))
    ap.add_argument("--budgets", default="200,400,800")
    ap.add_argument("--metric", default="log(usd_nocache)")
    ap.add_argument("--cost", type=float, default=None, help="1 セッションの USD（既定は測定の中央値）")
    ap.add_argument("--max-n", type=int, default=230, help="本番で使える問題の上限（計画の約 230 問）")
    a = ap.parse_args()
    est = json.load(open(a.estimates))
    comp = est["components"][a.metric]
    var_qc, var_rep = comp["var_qc"], comp["var_rep"]
    acc = est["components"].get("correct(0/1)")
    rates = [v["rate"] for v in est["accuracy"].values() if v["rate"] is not None]
    p = min(0.95, max(0.55, sum(rates) / len(rates))) if rates else 0.9
    # 正答の問題ごとのばらつきを logit 上の sd に直す（δ 法の逆）。
    tau = math.sqrt(acc["var_q"]) / max(1e-6, p * (1 - p)) if acc else 1.0
    tau = min(tau, 4.0)
    cost = a.cost or median_cost(est)
    print(f"# 検出力（{a.metric}）")
    print(f"- σ²_rep = {var_rep:.4f}、σ²_q×c = {var_qc:.4f}、σ²_q = {comp['var_q']:.4f}"
          f"（問題 {comp['n_questions']}、条件 {comp['n_conds']}、反復 {comp['reps']}）")
    print(f"- 正答: p ≈ {p:.2f}、問題ごとの logit の sd ≈ {tau:.2f}")
    print(f"- α = 0.05/{FAMILY}（Holm の最も厳しい段）、両側。条件は {N_CONDS}")
    table(est, [float(x) for x in a.budgets.split(",")], cost, var_qc, var_rep, p, tau, a.max_n)
    fixed_n_table(var_qc, var_rep)


def median_cost(est):
    per = est.get("per_corpus", {})
    vals = [v["median_usd_nocache"] for v in per.values() if v.get("median_usd_nocache")]
    return sum(vals) / len(vals) if vals else 0.05


if __name__ == "__main__":
    main()
