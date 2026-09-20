#!/usr/bin/env python3
"""PE5 (較正): 問題の層化抽出と、無作為の順で流す測定の駆動。

    pe5/run.py select                      層化抽出した問題を pe5/questions.json に書く
    pe5/run.py block --block cacheB --rounds 3 --model anthropic:sonnet
    pe5/run.py spend                       これまでに使った USD

一つのブロックは (問題 × 条件) の升目を「回」ごとに無作為の順で流す。順番は
種から決まるので再現できる（`--seed`）。ブロックごとに次の 2 点が違う。

  arm=warm  その回の各条件の最初に、捨てる 1 セッション（同じ題材の別の問題）
            を流してからプロンプトキャッシュの温まった状態で測る
  arm=rand  温めない。条件の順番が無作為なので、キャッシュの状態は
            共変量（cacheReadInputTokens の割合）として記録される

どちらの腕でも `grade/grade.py` が 1 セッションずつ採点され、結果は
results/pe5-l2.jsonl に、順番と腕は results/pe5-manifest.jsonl に追記される。
**費用の上限**（既定 20 USD、`ARE_PE5_BUDGET_USD`）を超えるとブロックは止まる。
"""
import argparse
import json
import os
import random
import subprocess
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "harness"))
import run as R  # noqa: E402

RESULTS = os.path.join(ROOT, "results")
SCORES = os.environ.get("ARE_PE5_SCORES", os.path.join(RESULTS, "pe5-l2.jsonl"))
MANIFEST = os.environ.get("ARE_PE5_MANIFEST", os.path.join(RESULTS, "pe5-manifest.jsonl"))
QUESTIONS = os.path.join(ROOT, "pe5", "questions.json")
BUDGET = float(os.environ.get("ARE_PE5_BUDGET_USD", "20"))
SEED = 20260921

# L2 で自動採点できるシナリオ（S4・S5 は rubric で LLM 判定が要る）。
AUTO = ["S1", "S2", "S3", "S6", "S7", "S8", "S9"]
ALL_CONDS = ["A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7"]
# 全ての L2 の題材（c1・c2・c4）に当てはまる条件。分散の推定に使う。
CORE_CONDS = ["A1", "A3", "A4", "A5"]


def load():
    qs = R.load_questions()
    return {k: v for k, v in qs.items() if v.get("split") == "dev" and v["corpus"] in R.L2_CORPORA
            and v["scenario"] in AUTO}


def select():
    """題材 × シナリオごとに 1 問。表し方は識別子と言い換えを交互にして散らす。

    抽出は種から決まる。捨てセッション用の問題（温める腕で使う）は、測る問題と
    重ならないように題材ごとに 1 問だけ別に選ぶ。"""
    qs = load()
    rng = random.Random(SEED)
    cells, warm = [], {}
    for corpus in R.L2_CORPORA:
        scen = sorted({q["scenario"] for q in qs.values() if q["corpus"] == corpus})
        for i, s in enumerate(scen):
            want = "identifier" if i % 2 == 0 else "paraphrase"
            pool = sorted(q["id"] for q in qs.values()
                          if q["corpus"] == corpus and q["scenario"] == s and q["phrasing"] == want)
            pool = pool or sorted(q["id"] for q in qs.values()
                                  if q["corpus"] == corpus and q["scenario"] == s)
            cells.append(rng.choice(pool))
        rest = sorted(q["id"] for q in qs.values() if q["corpus"] == corpus and q["id"] not in cells
                      and q["scenario"] == "S1") or \
               sorted(q["id"] for q in qs.values() if q["corpus"] == corpus and q["id"] not in cells)
        warm[corpus] = rng.choice(rest)
    # 条件 × シナリオの交互作用を、条件 × 問題の交互作用と分けて推定するには、
    # 一つの (題材, シナリオ) の升目に 2 問以上が要る。費用の都合で 4 つの升目に
    # 限り、表し方が主の問題と逆になる 2 問目を足す。
    second, pairs = [], [("c1", "S1"), ("c1", "S3"), ("c2", "S1"), ("c4", "S7")]
    for corpus, s_ in pairs:
        first = next(q for q in cells if qs[q]["corpus"] == corpus and qs[q]["scenario"] == s_)
        want = "paraphrase" if qs[first]["phrasing"] == "identifier" else "identifier"
        pool = sorted(q["id"] for q in qs.values() if q["corpus"] == corpus and q["scenario"] == s_
                      and q["phrasing"] == want and q["id"] not in cells
                      and q["base_id"] != qs[first]["base_id"])
        if pool:
            second.append(rng.choice(pool))
    cells_all = cells + second
    picked = {q: qs[q] for q in cells_all}
    out = {
        "seed": SEED,
        "questions": {q: {k: picked[q][k] for k in ("corpus", "scenario", "phrasing")} for q in cells_all},
        "primary": cells, "second": second,
        "warmup": warm,
        # ブロックの定義。予備の 4 セッションで 1 問が 0.06〜0.36 USD と分かったので、
        # 上限 22 USD に収まるように問題を絞ってある（pe5.md 第 1 節）。
        #   cache*  キャッシュの扱いを決めるため、c1 の**安い** 2 問 × 全 8 条件
        #   var     分散の成分のため、題材とシナリオを散らした 12 問 × 3 条件。
        #           うち 4 つの (題材, シナリオ) の升目は 2 問ずつで、条件 × シナリオと
        #           条件 × 問題の交互作用を分けられる
        #   tools   残りの条件（A2・A4・A6・A7）の使用率を c2・c4 でも見るため
        "blocks": {
            "cacheA": {"arm": "warm", "questions": pick(picked, [("c1", "S1"), ("c1", "S8")]),
                       "conds": ALL_CONDS},
            "cacheB": {"arm": "rand", "questions": pick(picked, [("c1", "S1"), ("c1", "S8")]),
                       "conds": ALL_CONDS},
            "var": {"arm": "rand", "conds": ["A1", "A3", "A5"],
                    "questions": (pick(picked, [("c1", "S1"), ("c1", "S3"), ("c2", "S1"), ("c4", "S7")])
                                  + second
                                  + pick(picked, [("c1", "S8"), ("c2", "S9"), ("c2", "S6"), ("c4", "S6")]))},
            "tools": {"arm": "rand", "conds": ["A2", "A4", "A6", "A7"],
                      "questions": pick(picked, [("c1", "S8"), ("c2", "S1"), ("c2", "S9"), ("c4", "S6")])},
        },
    }
    os.makedirs(os.path.dirname(QUESTIONS), exist_ok=True)
    with open(QUESTIONS, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def pick(picked, keys):
    """(題材, シナリオ) の並びから、層化で選んだ主の問題を取り出す。"""
    out = []
    for corpus, scen in keys:
        out += [q for q in picked
                if picked[q]["corpus"] == corpus and picked[q]["scenario"] == scen][:1]
    return out


def spent():
    if not os.path.exists(SCORES):
        return 0.0, 0
    rows = [json.loads(l) for l in open(SCORES) if l.strip()]
    return sum(r.get("usd") or 0.0 for r in rows), len(rows)


def done_sessions():
    if not os.path.exists(SCORES):
        return set()
    return {json.loads(l)["session"] for l in open(SCORES) if l.strip()}


def grade_one(d):
    out = subprocess.run([sys.executable, os.path.join(ROOT, "grade", "grade.py"), d],
                         capture_output=True, text=True)
    if out.returncode:
        print(out.stderr[-2000:], file=sys.stderr)
        return None
    return json.loads(out.stdout.strip().splitlines()[-1])


def run_one(qid, cond, model, rep, label, note):
    d, meta = R.run_session(qid, cond, model, rep, label)
    row = grade_one(d)
    if row is not None:
        with open(SCORES, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(MANIFEST, "a") as f:
        f.write(json.dumps({**note, "session": meta["session"], "label": label,
                            "wall_ms": meta["wall_ms"], "exit": meta["exit"],
                            "timed_out": meta["timed_out"], "at": meta["started_at"]},
                           ensure_ascii=False) + "\n")
    return row


def block(name, rounds, model, seed, limit, dry=False, cap=None):
    spec = json.load(open(QUESTIONS))
    b = spec["blocks"][name]
    qs = R.load_questions()
    cells = [(q, c) for q in b["questions"] for c in b["conds"]
             if not R.not_applicable(c, qs[q]["corpus"])]
    rng = random.Random(seed ^ zlib.crc32(name.encode()))
    done, ran = done_sessions(), 0
    for rnd in range(1, rounds + 1):
        order = cells[:]
        rng.shuffle(order)
        warmed = set()
        for i, (qid, cond) in enumerate(order):
            if cap is not None and ran >= cap:
                print(f"STOP cap {cap}", flush=True)
                return
            usd, _ = spent()
            if usd >= limit:
                print(f"STOP budget {usd:.3f} >= {limit}", flush=True)
                return
            corpus = qs[qid]["corpus"]
            if dry:
                print(f"{name} r{rnd} {i:3d} {qid} {cond} {corpus}")
                continue
            if b["arm"] == "warm" and (cond, corpus) not in warmed:
                wq = spec["warmup"][corpus]
                ws = f"{wq}-{cond}-{R.slug(model)}-r{rnd}"
                if ws not in done and not R.not_applicable(cond, corpus):
                    run_one(wq, cond, model, rnd, f"pe5-{name}-warmup",
                            {"block": name, "arm": "warm", "role": "warmup", "round": rnd,
                             "order": i, "question": wq, "cond": cond, "rep": rnd})
                warmed.add((cond, corpus))
            session = f"{qid}-{cond}-{R.slug(model)}-r{rnd}"
            if session in done:
                print(f"skip {session}", flush=True)
                continue
            row = run_one(qid, cond, model, rnd, f"pe5-{name}",
                          {"block": name, "arm": b["arm"], "role": "measure", "round": rnd,
                           "order": i, "question": qid, "cond": cond, "rep": rnd})
            ran += 1
            usd, n = spent()
            print(f"done {name} r{rnd} {i + 1}/{len(order)} {qid} {cond} "
                  f"usd={row and row.get('usd')} correct={row and row.get('correct')} "
                  f"total={usd:.3f} n={n}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("select")
    sub.add_parser("spend")
    b = sub.add_parser("block")
    b.add_argument("--block", required=True)
    b.add_argument("--rounds", type=int, default=1)
    b.add_argument("--model", default="anthropic:sonnet")
    b.add_argument("--seed", type=int, default=SEED)
    b.add_argument("--limit", type=float, default=BUDGET)
    b.add_argument("--dry", action="store_true")
    b.add_argument("--max", type=int, default=None, help="このコマンドで流すセッションの上限")
    a = ap.parse_args()
    if a.cmd == "select":
        select()
    elif a.cmd == "spend":
        usd, n = spent()
        print(json.dumps({"usd": round(usd, 4), "sessions": n, "budget": BUDGET}))
    else:
        block(a.block, a.rounds, a.model, a.seed, a.limit, a.dry, a.max)


if __name__ == "__main__":
    sys.exit(main())
