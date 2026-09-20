#!/usr/bin/env python3
"""Candidates whose gold comes from git: S2 (implementation of a behaviour,
from patches merged after the training cutoff) and S6 (history).

    gen/cand_git.py [--split dev] [--scenario S2,S6]

S2: a patch merged after CUTOFF that touches 1-4 non-test source files, whose
added lines mostly survive at the pinned commit. The gold names the main file
alone (see main_file); every other file the patch touched is optional, so
naming it is neither required nor wrong. A person confirms that the question
(written later from the commit message) points at the main file. S6: the
release (c1), pull request (c2) or date (wiki, c4) where a function or page
first appeared, from git log.
Output: gold-work/cand/<split>/S2.jsonl, S6.jsonl
"""
import argparse
import collections
import os
import re
import subprocess

from common import (CAND, CUTOFF, OUT, dropped_base_ids, git, is_test_path, read_jsonl, repo_dir, rng, split_of,
                    write_jsonl)

CODE_EXT = {"c1": (".go",), "c2": (".go", ".ts", ".tsx"), "c3": (".c", ".h")}
REPO = {"c1": "wikictl", "c2": "grafana", "c3": "linux"}
N2 = {"c1": 6, "c2": 8, "c3": 8}
N6 = {("c1", "wikictl"): 5, ("c1", "wiki"): 3, ("c2", "grafana"): 6, ("c4", "website"): 6}


def is_test(path):
    return is_test_path(path)


def commits(corpus):
    repo = REPO[corpus]
    if corpus == "c2":
        fmt = git(corpus, repo, "log", "--first-parent", f"--since={CUTOFF}", "--format=%H%x09%ad%x09%s",
                  "--date=short")
    else:
        fmt = git(corpus, repo, "log", "--no-merges", f"--since={CUTOFF}", "--format=%H%x09%ad%x09%s", "--date=short")
    for line in fmt.splitlines():
        h, d, s = line.split("\t", 2)
        yield h, d, s


def subject_ok(corpus, s):
    if corpus == "c1":
        return bool(re.match(r"^(fix|feat)(\(.+\))?!?: ", s))
    if corpus == "c2":
        return bool(re.search(r"\(#\d+\)$", s)) and not re.match(
            r"(?i)^(chore|docs?|deps|update|bump|revert|i18n|build|ci|e2e|test|release|changelog|codeowners|"
            r"betterer|cue|go:|npm|yarn)", s) and not re.search(r"(?i)\b(migrat\w*|refactor\w*|rename\w*)\b", s)
    if corpus == "c3":
        return bool(re.search(r"(?i)\b(fix|add|support|handle|avoid|prevent|use|allow|reject|return)\b", s)) \
            and not re.match(r"(?i)^(revert|merge|selftests?|docs?|documentation|tools|kunit|dt-bindings)", s)
    return False


def numstat(corpus, h):
    out = git(corpus, REPO[corpus], "show", "--numstat", "--format=", "--no-renames", h)
    rows = []
    for line in out.splitlines():
        a, d, p = line.split("\t", 2)
        if a == "-":
            continue
        rows.append((int(a), int(d), p))
    return rows


def added_lines(corpus, h, path):
    """The lines the patch adds to one file, the functions whose body they
    fall in (from the hunk headers), and how many of the added lines are
    code (see code_lines)."""
    out = git(corpus, REPO[corpus], "show", "-U0", "--format=", h, "--", path)
    lines, funcs = [], set()
    for line in out.splitlines():
        if line.startswith("@@"):
            m = re.search(r"@@[^@]*@@\s*(.*)$", line)
            f = func_name(m.group(1)) if m and m.group(1) else None
            if f:
                funcs.add(f)
        elif line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:].strip())
    return lines, funcs, code_lines(lines)


PUNCT = re.compile(r"[{};=]")


def code_lines(lines):
    """How many of the added lines are code rather than text. A line that is
    blank, a comment, or a sentence (five words or more and none of { } ; = )
    is text: the help text of a command, a line of a table, a paragraph of a
    comment block. Which file took the most of these decides the main file,
    so a change of the help text does not outweigh the implementation."""
    n = 0
    for line in lines:
        t = line.strip()
        if not t or t.startswith(("//", "/*", "*/", "*", "#", "<!--", "|", "-")):
            continue
        if not PUNCT.search(t) and len(t.split()) >= 5:
            continue
        n += 1
    return n


def main_file(stats):
    """The main file of a patch, by a rule that can be written down: the file
    that took the most added lines of code, then the most added lines, then
    the first path. stats is a list of (path, added, code)."""
    return sorted(stats, key=lambda s: (-s[2], -s[1], s[0]))[0][0]


def func_name(ctx):
    m = re.match(r"func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*[\[(]", ctx)
    if m:
        return m.group(1)
    m = re.match(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)", ctx)
    if m:
        return m.group(1)
    m = re.match(r"(?:export\s+)?const\s+([A-Za-z_]\w*)\s*=", ctx)
    if m:
        return m.group(1)
    m = re.match(r"^[A-Za-z_][\w\s\*]*?\b([A-Za-z_]\w*)\s*\(", ctx)
    if m and m.group(1) not in ("if", "for", "while", "switch", "return", "sizeof"):
        return m.group(1)
    return None


def survives(corpus, path, lines):
    p = os.path.join(repo_dir(corpus, REPO[corpus]), path)
    if not os.path.exists(p):
        return 0.0
    body = {l.strip() for l in open(p, errors="replace")}
    sig = [l for l in lines if len(l) > 10]
    if not sig:
        return 0.0
    return sum(1 for l in sig if l in body) / len(sig)


def s2(corpus, split, n):
    r = rng(split, "S2", corpus)
    # the author date too: a patch written before the cutoff may have been
    # public (a mailing list, a fork) before it was merged
    pool = [c for c in commits(corpus) if c[1] >= CUTOFF and subject_ok(corpus, c[2])
            and split_of(f"{corpus}:S2:{c[0]}") == split]
    r.shuffle(pool)
    out, dropped = [], dropped_base_ids()
    for h, date, subj in pool:
        if len(out) == n:
            break
        if f"{split}-{corpus}-s2-{h[:10]}" in dropped:
            continue  # audited as a wrong gold or an invalid question: draw another
        rows = numstat(corpus, h)
        code = [(a, d, p) for a, d, p in rows if p.endswith(CODE_EXT[corpus]) and not is_test(p)
                and not re.search(r"(\.gen\.|_gen\.go|generated|/locales/|\.pb\.go|zz_)", p)]
        if not 1 <= len(code) <= 4:
            continue
        added = sum(a for a, _, _ in code)
        if not 5 <= added <= 200:
            continue
        files, funcs, surv, stats = [], set(), [], []
        for a, d, p in code:
            lines, fs, ncode = added_lines(corpus, h, p)
            s = survives(corpus, p, lines)
            if s >= 0.6:
                files.append(p)
                funcs |= fs
                stats.append((p, a, ncode))
            surv.append((p, round(s, 2), ncode))
        if not files:
            continue
        main = main_file(stats)
        body = git(corpus, REPO[corpus], "show", "-s", "--format=%b", h).strip()
        body = re.sub(r"\n(Signed-off-by|Reviewed-by|Acked-by|Tested-by|Co-authored-by|Cc|Link|Reported-by|"
                      r"Suggested-by|Fixes|Closes|Message-ID|Co-developed-by):.*", "", body, flags=re.I)
        other = sorted({p for _, _, p in rows} - {main})
        repo = REPO[corpus]
        out.append({
            "base_id": f"{split}-{corpus}-s2-{h[:10]}",
            "corpus": corpus, "scenario": "S2", "split": split,
            "commit": {"hash": h, "date": date, "subject": subj, "body": body[:1500]},
            "identifiers": sorted(funcs)[:5],
            "answer_format": "ファイルのパスの一覧（リポジトリの根から）。関係の強い順に。"
                             "振る舞いを実装しているファイルを必ず挙げ、それに付随して変わるだけのファイルは挙げても挙げなくてもよい",
            # the main file is required, the other files of the patch are
            # optional: naming one is neither required nor wrong
            "gold": {"type": "files", "repo": repo, "value": [main], "optional": other},
            "evidence_accept": sorted({f"{repo}/{p}" for p in [main] + files}),
            "review": {"required": True,
                       "check": "質問が指す振る舞いを主に実装しているのが正解のファイル（gold.value）か。"
                                "付随の変更（ヘルプ文・呼び出し側・テスト）は optional に入っているか",
                       "machine_flags": {"main": main, "survival": surv, "other_files_in_patch": other[:10]}},
            "source": {"method": "git show --numstat of a patch merged after the cutoff",
                       "main_file_rule": "追加行のうちコードの行（空行・コメント・5 語以上で {};= を含まない文を除いたもの）が最多のファイル。同数なら追加行、次にパスの順"},
        })
    return out


# ---------------------------------------------------------------- S6

def semver_key(t):
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)$", t)
    return tuple(int(x) for x in m.groups()) if m else None


def first_tag(corpus, repo, h):
    tags = git(corpus, repo, "tag", "--contains", h).split()
    rel = sorted((semver_key(t), t) for t in tags if semver_key(t))
    return rel[0][1] if rel else None


def intro_commit(corpus, repo, regex, paths=None):
    args = ["log", "--reverse", "--format=%H%x09%ad%x09%s", "--date=short", "-G", regex]
    if corpus == "c2":
        args += ["--since=2024-01-01"]
    args += ["--"] + (paths or ["."])
    out = git(corpus, repo, *args).splitlines()
    return out[0].split("\t", 2) if out else None


def defined_at(corpus, repo, rev, regex):
    r = subprocess.run(["git", "-C", repo_dir(corpus, repo), "grep", "-qE", regex, rev, "--", "*.go"])
    return r.returncode == 0


def s6_c1_code(split, n):
    defs = [d for d in read_jsonl(os.path.join(OUT, "c1-go.jsonl")) if d["rec"] == "def"]
    seen, pool = set(), []
    for d in defs:
        if d["test"] or d["kind"] not in ("func", "method") or (d["file"], d["id"]) in seen:
            continue
        seen.add((d["file"], d["id"]))
        if split_of(f"c1:S6:{d['file']}:{d['id']}") == split:
            pool.append(d)
    r = rng(split, "S6", "c1", "code")
    r.shuffle(pool)
    out, dropped = [], dropped_base_ids()
    for d in pool:
        if len(out) == n:
            break
        if f"{split}-c1-s6-code-{d['id'].replace('.', '-')}" in dropped:
            continue
        if d["recv"]:  # the receiver's type too: a name like Error is shared by many types
            regex = rf"^func \(\w* ?\*?{d['recv']}(\[[^]]*\])?\) {d['name']}[\[(]"
        else:
            regex = rf"^func {d['name']}[\[(]"
        c = intro_commit("c1", "wikictl", regex, ["*.go"])
        if not c or defined_at("c1", "wikictl", c[0] + "^", regex):
            continue
        tag = first_tag("c1", "wikictl", c[0])
        if not tag:
            continue
        what = f"型 `{d['recv']}` のメソッド `{d['name']}`" if d["recv"] else f"関数 `{d['name']}`"
        out.append(s6_item("c1", split, f"code-{d['id'].replace('.', '-')}",
                           f"wikictl のソースコードに{what}が初めて含まれたリリースの版はどれか。",
                           sorted({d["id"], d["name"]}), {"label": d["id"], "kind": d["kind"], "doc": d["doc"], "area": os.path.dirname(d["file"])},
                           "次の関数が初めて含まれたリリースの版を尋ねる質問", "版（例: `v0.3.0`）",
                           {"type": "version", "value": tag},
                           ["wikictl/" + d["file"], "wiki/projects/wikictl/releases/"],
                           {"commit": c[0], "date": c[1], "subject": c[2], "regex": regex}))
    return out


def s6_pages(corpus, repo, split, n, root, lang_note):
    base = repo_dir(corpus, repo)
    files = []
    for dp, dn, fn in os.walk(os.path.join(base, root)):
        dn[:] = [x for x in dn if x != ".git"]
        for f in fn:
            if f.endswith(".md") and f not in ("_index.md", "index.md", "README.md"):
                files.append(os.path.relpath(os.path.join(dp, f), base))
    files = [f for f in sorted(files) if split_of(f"{corpus}:S6:{f}") == split]
    r = rng(split, "S6", corpus, repo)
    r.shuffle(files)
    out, dropped = [], dropped_base_ids()
    for f in files:
        if len(out) == n:
            break
        if f"{split}-{corpus}-s6-page-" + re.sub(r"[^A-Za-z0-9]+", "-", f)[-60:] in dropped:
            continue
        log = git(corpus, repo, "log", "--follow", "--diff-filter=A", "--format=%H%x09%ad", "--date=short", "--", f)
        rows = [l.split("\t") for l in log.splitlines()]
        if not rows:
            continue
        # --follow also walks through a copy (a Japanese page starts as a copy
        # of the English one) and a rename, where "when was the page added"
        # has two answers; keep only pages whose path itself was added there
        plain = git(corpus, repo, "log", "--diff-filter=A", "--format=%H", "--", f).split()
        if not plain or plain[-1] != rows[-1][0]:
            continue
        h, date = rows[-1]
        title = page_title(os.path.join(base, f))
        if not title:
            continue
        if corpus == "c4":
            q = f"Kubernetes の文書のページ `{f}` が、このリポジトリに最初に追加されたのは何年何月か。"
            gold = {"type": "date", "precision": "month", "value": date[:7]}
            fmt = "年月（例: `2021-03`）"
        else:
            q = f"wiki のページ `{f}` が作られたのはいつか。"
            gold = {"type": "date", "precision": "day", "value": date}
            fmt = "日付（例: `2026-09-01`）"
        out.append(s6_item(corpus, split, "page-" + re.sub(r"[^A-Za-z0-9]+", "-", f)[-60:], q, [f, os.path.basename(f)],
                           {"label": f, "kind": "page", "doc": title},
                           f"{lang_note}次のページが最初に追加された時期を尋ねる質問", fmt, gold,
                           [f"{repo}/{f}"], {"commit": h, "date": date, "renames_followed": len(rows) > 1}))
    return out


def page_title(path):
    try:
        txt = open(path, errors="replace").read(4000)
    except OSError:
        return None
    m = re.search(r"^title:\s*\"?(.+?)\"?\s*$", txt, re.M)
    if m:
        return m.group(1)
    m = re.search(r"^#\s+(.+)$", txt, re.M)
    s = re.search(r"^summary:\s*(.+)$", txt, re.M)
    if m:
        return m.group(1) + (f"（{s.group(1)}）" if s else "")
    return None


def s6_c2(split, n):
    defs = [d for d in read_jsonl(os.path.join(OUT, "c2-go.jsonl")) if d["rec"] == "def"]
    universe = collections.Counter()
    for p in read_jsonl(os.path.join(OUT, "c2-go-parse.jsonl")):
        universe[p.get("name")] += 1
    pool, seen = [], set()
    for d in defs:
        if d["test"] or d["kind"] != "func" or not d["exported"] or universe[d["name"]] != 1 or len(d["doc"]) < 40:
            continue
        if (d["file"], d["id"]) in seen or not d["file"].startswith("pkg/"):
            continue
        seen.add((d["file"], d["id"]))
        if split_of(f"c2:S6:{d['file']}:{d['id']}") == split:
            pool.append(d)
    r = rng(split, "S6", "c2")
    r.shuffle(pool)
    out, dropped = [], dropped_base_ids()
    for d in pool:
        if len(out) == n:
            break
        if f"{split}-c2-s6-code-{d['id']}" in dropped:
            continue
        regex = rf"^func {d['name']}[\[(]"
        c = intro_commit("c2", "grafana", regex, ["pkg/"])
        if not c:
            continue  # added before 2024: the pull request is hard to pin down
        if defined_at("c2", "grafana", c[0] + "^", regex):
            continue  # the first match in the window changed an older definition
        m = re.search(r"\(#(\d+)\)$", c[2])
        if not m:
            continue
        out.append(s6_item("c2", split, f"code-{d['id']}",
                           f"grafana のソースコードに関数 `{d['name']}` を追加したプルリクエストの番号はどれか。",
                           [d["name"]], {"label": d["name"], "kind": "func", "doc": d["doc"], "area": os.path.dirname(d["file"])},
                           "次の関数を追加したプルリクエストの番号を尋ねる質問", "プルリクエストの番号（整数）",
                           {"type": "int", "value": int(m.group(1))}, ["grafana/" + d["file"], "grafana/CHANGELOG.md"],
                           {"commit": c[0], "date": c[1], "subject": c[2], "regex": regex}))
    return out


def s6_item(corpus, split, key, q, idents, subject, ptask, fmt, gold, ev, src):
    return {"base_id": f"{split}-{corpus}-s6-{key}", "corpus": corpus, "scenario": "S6", "split": split,
            "q_ident": q, "identifiers": idents, "subject": subject, "paraphrase_task": ptask,
            "answer_format": fmt, "gold": gold, "evidence_accept": ev,
            "review": {"required": False, "sample": True,
                       "check": "導入の commit が正しいか（名前の変更・移動・一度消して戻したもの）、版・番号・日付の対応"},
            "source": {"method": "git log -G / --diff-filter=A --follow, git tag --contains", **src}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--scenario", default="S2,S6")
    a = ap.parse_args()
    d = os.path.join(CAND, a.split)
    sc = a.scenario.split(",")
    if "S2" in sc:
        rows = []
        for c in ("c1", "c2", "c3"):
            rows += s2(c, a.split, N2[c])
        write_jsonl(os.path.join(d, "S2.jsonl"), rows)
        print("S2", collections.Counter(r["corpus"] for r in rows))
    if "S6" in sc:
        rows = s6_c1_code(a.split, N6[("c1", "wikictl")])
        rows += s6_pages("c1", "wiki", a.split, N6[("c1", "wiki")], "projects", "wiki の、")
        rows += s6_c2(a.split, N6[("c2", "grafana")])
        rows += s6_pages("c4", "website", a.split, N6[("c4", "website")], "content/ja/docs", "Kubernetes の日本語の文書の、")
        write_jsonl(os.path.join(d, "S6.jsonl"), rows)
        print("S6", collections.Counter(r["corpus"] for r in rows))


if __name__ == "__main__":
    main()
