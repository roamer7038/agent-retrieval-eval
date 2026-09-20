#!/usr/bin/env python3
"""Candidates for the scenarios whose gold is a place in the documents: S4
(design reasons), S5 (procedures), S7 on documents (enumerations from the
frontmatter) and S9 (documents <-> code, including one step of indirection).

    gen/cand_docs.py [--split dev] [--scenario S4,S5,S7,S9]

S4 and S5 pick a section by its shape (a heading or words that mark a
reason; a numbered list or shell commands) and leave the question and the
key points to gen/phrase.py (written from the section by the local LLM) and
to a person. S7 counts pages from the parsed YAML frontmatter. S9 links a
term the documents write in backticks (a setting key, a rule name) to the Go
files that hold the same string literal or struct tag (goanalyze
-parse-only), and back. "Handles" is read widely there: a file that reads or
writes the value, or changes what it does by the value, handles the term, and
so does a file that takes the value from a short function that returns it
(one hop in the call graph of gen/analyze.sh, the graph S3 uses); a
file where the name only sits in a list is not the gold (gen/judge.py
s9check asks the judge, s9apply moves the ones it calls a list into the
optional part of the gold).
Output: gold-work/cand/<split>/S4.jsonl, S5.jsonl, S7-docs.jsonl, S9.jsonl
"""
import argparse
import collections
import os
import re
import subprocess

import yaml

from common import CAND, OUT, dropped_base_ids, is_test_path, read_jsonl, repo_dir, rng, split_of, write_jsonl

# What "handles" means for S9, in the question and in the judge's prompt.
# It covers one step of indirection: a file that takes the value from a short
# function that returns it (a getter) handles it as much as a file that reads
# the field (the decision of 2026-09-20 on S9).
S9_HANDLE_JA = ("その値を読み書きするか、その値によって処理を変えるファイル"
                "（値を返す短い関数を通して受け取って使う場合も含む。名前が一覧・表・コメントに並ぶだけのファイルは含めない）")

FENCE = re.compile(r"^\s*(```|~~~)")
HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def md_files(corpus, repo, roots, exclude=()):
    base = repo_dir(corpus, repo)
    out = []
    for root in roots:
        for dp, dn, fn in os.walk(os.path.join(base, root)):
            dn[:] = sorted(x for x in dn if x != ".git")
            for f in sorted(fn):
                if f.endswith(".md"):
                    rel = os.path.relpath(os.path.join(dp, f), base)
                    if not any(re.search(e, rel) for e in exclude):
                        out.append(rel)
    return out


def frontmatter(text):
    if not text.startswith("---\n"):
        return {}, 0
    end = text.find("\n---", 4)
    if end < 0:
        return {}, 0
    try:
        fm = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        fm = {}
    return (fm if isinstance(fm, dict) else {}), text[:end + 4].count("\n") + 1


def sections(corpus, repo, path):
    """Sections of a Markdown file: heading, level, 1-based line range,
    body text. Headings inside code fences are not headings."""
    text = open(os.path.join(repo_dir(corpus, repo), path), errors="replace").read()
    fm, skip = frontmatter(text)
    lines = text.split("\n")
    secs, cur, fence = [], None, False
    for i, line in enumerate(lines, 1):
        if i <= skip:
            continue
        if FENCE.match(line):
            fence = not fence
        m = None if fence else HEAD.match(line)
        if m:
            if cur:
                cur["end"] = i - 1
                secs.append(cur)
            cur = {"file": path, "level": len(m.group(1)), "heading": m.group(2), "line": i, "body": []}
        elif cur:
            cur["body"].append(line)
    if cur:
        cur["end"] = len(lines)
        secs.append(cur)
    for s in secs:
        s["text"] = "\n".join(s["body"]).strip()
        del s["body"]
    return fm, secs


def counterpart(path):
    """The English page of a Japanese kubernetes/website page, and back."""
    if path.startswith("content/ja/"):
        return "content/en/" + path[len("content/ja/"):]
    if path.startswith("content/en/"):
        return "content/ja/" + path[len("content/en/"):]
    return None


def exists(corpus, repo, path):
    return path and os.path.exists(os.path.join(repo_dir(corpus, repo), path))


def base_item(split, corpus, scen, key, repo, sec, extra):
    ev = [f"{repo}/{sec['file']}"]
    cp = counterpart(sec["file"]) if corpus == "c4" else None
    if cp and exists(corpus, repo, cp):
        ev.append(f"{repo}/{cp}")
    item = {
        "base_id": f"{split}-{corpus}-{scen.lower()}-{key}",
        "corpus": corpus, "scenario": scen, "split": split,
        "section": {"repo": repo, "file": sec["file"], "heading": sec["heading"], "lines": [sec["line"], sec["end"]]},
        "evidence_accept": ev,
    }
    item.update(extra)
    return item


# ---------------------------------------------------------------- S5

STEP = re.compile(r"^\s{0,3}\d+\.\s+\S")
SHELL = re.compile(r"^\s*(\$ |wikictl |git |kubectl |grafana |docker |helm |curl |sudo |make |go |npm |yarn |kubeadm )")


def is_procedure(sec, min_steps):
    lines = sec["text"].split("\n")
    steps = sum(1 for l in lines if STEP.match(l))
    cmds = sum(1 for l in lines if SHELL.match(l))
    return steps >= min_steps or (cmds >= 2 and steps >= 1), steps, cmds


S5_POOLS = {
    "c1": ("wiki", ["projects", "global", "machines"], [r"/raw/"], 2),
    "c2": ("grafana", ["docs/sources"], [r"/whatsnew/", r"/shared/", r"/release-notes/"], 3),
    "c4": ("website", ["content/ja/docs/tasks", "content/ja/docs/setup"], [], 3),
}


def s5(split, corpus, n):
    repo, roots, excl, min_steps = S5_POOLS[corpus]
    pool = []
    for f in md_files(corpus, repo, roots, excl):
        _, secs = sections(corpus, repo, f)
        for s in secs:
            ok, steps, cmds = is_procedure(s, min_steps)
            if ok and 200 <= len(s["text"]) <= 6000:
                pool.append(dict(s, steps=steps, cmds=cmds))
    pool = [s for s in pool if split_of(f"{corpus}:S5:{s['file']}:{s['heading']}") == split]
    r = rng(split, "S5", corpus)
    r.shuffle(pool)
    out, files, dropped = [], set(), dropped_base_ids()
    for s in pool:
        if len(out) == n:
            break
        if s["file"] in files:
            continue  # one section per page
        key = re.sub(r"[^A-Za-z0-9]+", "-", s["file"][:-3])[-50:] + f"-L{s['line']}"
        if f"{split}-{corpus}-s5-{key}" in dropped:
            continue  # audited as an invalid question or wrong key points
        files.add(s["file"])
        out.append(base_item(split, corpus, "S5", key, repo, s, {
            "qgen": {"kind": "procedure", "text": s["text"][:3500], "heading": s["heading"]},
            "answer_format": "手順の要点を箇条書きで（コマンドや設定の名前はそのまま）",
            "gold": {"type": "rubric", "points": None,
                     "ref": [{"repo": repo, "file": s["file"], "lines": [s["line"], s["end"]]}]},
            "review": {"required": True,
                       "check": "質問が節の手順を一意に指すか。要点（LLM が節から抜き出したもの）が正しく、過不足が無いか。"
                                "同じ手順を書いた別の節・ページが無いか（あれば根拠の範囲に足す）",
                       "machine_flags": {"steps": s["steps"], "commands": s["cmds"]}},
            "source": {"method": "numbered list / shell commands in a Markdown section"},
        }))
    return out


def comment_start(corpus, repo, path, line):
    """The first line of the comment block that sits right above line."""
    try:
        lines = open(os.path.join(repo_dir(corpus, repo), path), errors="replace").read().split("\n")
    except OSError:
        return line
    i = line - 1
    while i - 1 >= 0 and lines[i - 1].lstrip().startswith("//"):
        i -= 1
    return i + 1


# ---------------------------------------------------------------- S4

REASON_HEAD = re.compile(r"(?i)(理由|なぜ|背景|why|motivation|rationale|decision|context|決定)")
REASON_TEXT = re.compile(r"(?i)(理由は|ため、|ためである|because|the reason|rationale|so that|in order to|this is why)")


def s4(split, corpus, n):
    r = rng(split, "S4", corpus)
    pool = []
    if corpus == "c1":
        # the whole wiki, and a section that states a reason twice counts even
        # without a reason heading (the rule of c2 and c4): with the sections
        # the audits of PE2b threw away, the projects/ pool alone had two
        # sections left in dev
        for f in md_files("c1", "wiki", ["projects", "global", "machines"], [r"/raw/"]):
            _, secs = sections("c1", "wiki", f)
            for s in secs:
                adr = "/decisions/adr-" in f
                if (adr and re.search(r"(?i)^(context|decision|決定|背景|理由)", s["heading"])) or \
                        (not adr and REASON_TEXT.search(s["text"]) and
                         (REASON_HEAD.search(s["heading"]) or len(REASON_TEXT.findall(s["text"])) >= 2)):
                    if 80 <= len(s["text"]) <= 4000:
                        pool.append(("wiki", s, None))
    elif corpus == "c2":
        for f in md_files("c2", "grafana", ["contribute", "docs/sources"], [r"/whatsnew/", r"/release-notes/"]):
            _, secs = sections("c2", "grafana", f)
            for s in secs:
                if REASON_TEXT.search(s["text"]) and (REASON_HEAD.search(s["heading"]) or
                                                      len(REASON_TEXT.findall(s["text"])) >= 2):
                    if 200 <= len(s["text"]) <= 4000:
                        pool.append(("grafana", s, None))
        # design reasons written in Go doc comments
        seen = set()
        for d in read_jsonl(os.path.join(OUT, "c2-go.jsonl")):
            if d["rec"] != "def" or d["test"] or not d["file"].startswith("pkg/"):
                continue
            if (d["file"], d["line"]) in seen:
                continue
            seen.add((d["file"], d["line"]))
            if re.search(r"(?i)\b(because|the reason|so that|in order to|we (do|use|chose|need))\b", d["doc"]) \
                    and len(d["doc"]) >= 150 and not re.search(r"/(testing|test|tests|fakes?|mocks?)/", d["file"]):
                # the reason is in the doc comment above the definition, so
                # the range of the evidence starts at the first line of that
                # comment block, not at the definition (PE2b review: golds
                # whose range left the reason out)
                start = comment_start(corpus, "grafana", d["file"], d["line"])
                s = {"file": d["file"], "heading": d["id"], "line": start, "end": d["end"], "text": d["doc"]}
                pool.append(("grafana", s, "code"))
    elif corpus == "c4":
        for f in md_files("c4", "website", ["content/ja/docs"]):
            _, secs = sections("c4", "website", f)
            for s in secs:
                if (REASON_HEAD.search(s["heading"]) or len(REASON_TEXT.findall(s["text"])) >= 2) and \
                        REASON_TEXT.search(s["text"]) and 200 <= len(s["text"]) <= 4000:
                    pool.append(("website", s, None))
    pool = [p for p in pool if split_of(f"{corpus}:S4:{p[1]['file']}:{p[1]['heading']}") == split]
    r.shuffle(pool)
    out, files, dropped = [], set(), dropped_base_ids()
    want_code = n // 2 if corpus == "c2" else 0   # half from Go doc comments
    for repo, s, kind in pool:
        if len(out) == n:
            break
        if s["file"] in files:
            continue
        ncode = sum(1 for o in out if o["qgen"].get("where") == "code")
        if corpus == "c2" and kind == "code" and ncode >= want_code:
            continue
        if corpus == "c2" and kind is None and len(out) - ncode >= n - want_code:
            continue
        key = re.sub(r"[^A-Za-z0-9]+", "-", s["file"])[-50:] + f"-L{s['line']}"
        if f"{split}-{corpus}-s4-{key}" in dropped:
            continue  # audited as an invalid question or key points that are not a reason
        files.add(s["file"])
        out.append(base_item(split, corpus, "S4", key, repo, s, {
            "qgen": {"kind": "reason", "text": s["text"][:3500], "heading": s["heading"], "where": kind or "doc"},
            "answer_format": "理由を 1〜3 文で",
            "gold": {"type": "rubric", "points": None,
                     "ref": [{"repo": repo, "file": s["file"], "lines": [s["line"], s["end"]]}]},
            "review": {"required": True,
                       "check": "質問が設計の理由を問うもので、節の記述だけから答えられるか。要点が理由を正しく表すか。"
                                "理由を別の場所（ADR・リリースノート・コミット）にも書いていれば根拠の範囲に足す",
                       "machine_flags": {"where": kind or "doc"}},
            "source": {"method": "reason heading or reason words in a section / Go doc comment"},
        }))
    return out


# ---------------------------------------------------------------- S7 (documents)

def s7_docs(split, corpus, n):
    r = rng(split, "S7", corpus, "docs")
    fams = []
    if corpus == "c1":
        for f in md_files("c1", "wiki", ["."]):
            pass
        pages = {}
        for f in md_files("c1", "wiki", ["projects", "global", "machines"]):
            fm, _ = frontmatter(open(os.path.join(repo_dir("c1", "wiki"), f), errors="replace").read())
            pages[f] = fm
        by_dir_type = collections.defaultdict(list)
        for f, fm in pages.items():
            if fm.get("type"):
                by_dir_type[(os.path.dirname(f), fm["type"])].append(f)
        for (d, t), fs in by_dir_type.items():
            if 3 <= len(fs) <= 30:
                fams.append(("count-type", {"dir": d, "type": t}, sorted(fs)))
        adr = collections.defaultdict(list)
        for f in pages:
            if re.search(r"/decisions/adr-\d+", f):
                adr[os.path.dirname(f)].append(f)
        for d, fs in adr.items():
            fams.append(("count-adr", {"dir": d}, sorted(fs)))
    elif corpus == "c4":
        gates_dir = "content/en/docs/reference/command-line-tools-reference/feature-gates"
        gates = {}
        for f in md_files("c4", "website", [gates_dir]):
            if f.endswith("_index.md"):
                continue
            fm, _ = frontmatter(open(os.path.join(repo_dir("c4", "website"), f), errors="replace").read())
            if fm.get("stages"):
                gates[fm.get("title") or os.path.basename(f)[:-3]] = (f, fm["stages"])
        by_ver = collections.defaultdict(list)
        for name, (f, stages) in gates.items():
            for st in stages:
                v = str(st.get("fromVersion", ""))
                by_ver[(st.get("stage"), v)].append(name)
        for (stage, v), names in by_ver.items():
            if stage in ("alpha", "beta", "stable", "deprecated") and 3 <= len(names) <= 25:
                fams.append(("gates", {"stage": stage, "version": v, "dir": gates_dir}, sorted(names)))
        gl = {}
        gdir = "content/en/docs/reference/glossary"
        for f in md_files("c4", "website", [gdir]):
            fm, _ = frontmatter(open(os.path.join(repo_dir("c4", "website"), f), errors="replace").read())
            for t in fm.get("tags") or []:
                gl.setdefault(t, []).append(fm.get("id") or os.path.basename(f)[:-3])
        for t, ids in gl.items():
            if 3 <= len(ids) <= 20:
                fams.append(("glossary", {"tag": t, "dir": gdir}, sorted(ids)))
    fams = [f for f in fams if split_of(f"{corpus}:S7d:{f[0]}:{sorted(f[1].items())}") == split]
    r.shuffle(fams)
    out, dropped = [], dropped_base_ids()
    for kind, p, items in fams:
        if len(out) == n:
            break
        # the directory tells apart two counts of the same type in different places
        key = re.sub(r"[^A-Za-z0-9]+", "-", "-".join(str(v) for k, v in sorted(p.items())
                                                     if k != "dir" or kind.startswith("count")))[-60:].strip("-")
        if f"{split}-{corpus}-s7-{kind}-{key}" in dropped:
            continue
        if kind == "count-type":
            q = f"wiki の `{p['dir']}/` 直下のページのうち、frontmatter の `type` が `{p['type']}` のものはいくつあるか。"
            gold, fmt = {"type": "int", "value": len(items)}, "整数"
            # the directory is the scope of the count, not a name to hide
            ev, ids = [f"wiki/{p['dir']}/"], [p["type"]]
            ptask = "wiki のある場所にある、ある種類のページの数を尋ねる質問"
        elif kind == "count-adr":
            q = f"wiki の `{p['dir']}/` にある ADR（ファイル名が `adr-` で始まるもの）はいくつあるか。"
            gold, fmt = {"type": "int", "value": len(items)}, "整数"
            ev, ids = [f"wiki/{p['dir']}/"], []
            ptask = "wiki のある場所にある設計判断の記録の数を尋ねる質問"
        elif kind == "gates":
            q = (f"Kubernetes の文書に載っている feature gate（削除されたものを含む）のうち、Kubernetes {p['version']} から"
                 f" `{p['stage']}` の段階になったものをすべて挙げよ。")
            gold, fmt = {"type": "set", "match": "name", "value": items}, "feature gate の名前の一覧"
            ev, ids = [f"website/{p['dir']}/", "website/content/en/docs/reference/command-line-tools-reference/feature-gates-removed/"], \
                [p["stage"]]  # the version is the condition of the question
            ptask = "ある版である段階になった Kubernetes の機能の切り替えをすべて挙げさせる質問"
        else:
            q = f"Kubernetes の文書の用語集（glossary）で、タグ `{p['tag']}` が付いている用語をすべて挙げよ。"
            gold, fmt = {"type": "set", "match": "name", "value": items}, "用語の id（ファイル名から .md を除いたもの）の一覧"
            ev, ids = [f"website/{p['dir']}/"], [p["tag"]]
            ptask = "Kubernetes の用語集で、ある分類に属する用語をすべて挙げさせる質問"
        out.append({
            "base_id": f"{split}-{corpus}-s7-{kind}-{key}", "corpus": corpus, "scenario": "S7", "split": split,
            "family": kind, "q_ident": q, "identifiers": ids,
            "subject": {"label": key, "kind": kind, "doc": str(p), "area": p.get("dir", "")},
            "paraphrase_task": ptask, "answer_format": fmt, "gold": gold, "evidence_accept": ev,
            "review": {"required": False, "sample": True,
                       "check": "frontmatter の解釈（段階の版の書き方、タグの表記揺れ）と、対象の範囲（直下か再帰か）"},
            "source": {"method": "YAML frontmatter parsed with PyYAML"},
        })
    return out


# ---------------------------------------------------------------- S9

def lits(corpus):
    by = collections.defaultdict(set)
    for r in read_jsonl(os.path.join(OUT, f"{corpus}-go-parse.jsonl")):
        if r["rec"] == "lit" and not is_test_path(r["file"]):
            by[r["value"]].add(r["file"])
    return by


ASSIGN = re.compile(r"^\s*(?:(\w+)\.)?([A-Za-z_]\w*)\s*(?:,\s*\w+\s*)?:?=\s*(.*)$")


def cfg_fields(corpus, repo, files, term, window=30):
    """The struct fields a setting's value ends up in, read from the lines
    that hold the key in the files that read the configuration. A value may
    pass through one or two local variables first (`cdnURL` -> `parsedCDNURL`
    -> `cfg.CDNRootURL`), so the locals are followed inside a window."""
    fields = set()
    for f in files:
        try:
            lines = open(os.path.join(repo_dir(corpus, repo), f), errors="replace").read().split("\n")
        except OSError:
            continue
        for i, line in enumerate(lines):
            if f'"{term}"' not in line:
                continue
            m = ASSIGN.match(line)
            if not m:
                continue
            recv, name, _ = m.groups()
            if recv and name[:1].isupper():
                fields.add(name)
                continue
            locals_ = {name}
            for j in range(i + 1, min(i + window, len(lines))):
                m2 = ASSIGN.match(lines[j])
                if not m2:
                    continue
                r2, n2, rhs = m2.groups()
                if not any(re.search(rf"\b{re.escape(x)}\b", rhs) for x in locals_):
                    continue
                if r2 and n2[:1].isupper():
                    fields.add(n2)
                elif not r2:
                    locals_.add(n2)
    return {f for f in fields if len(f) >= 8}


def field_lines(corpus, repo, fields):
    """(file, line) of every non-test line that reads one of those fields."""
    out = []
    for fld in sorted(fields):
        got = subprocess.run(["git", "-C", repo_dir(corpus, repo), "grep", "-nE", rf"\.{fld}\b", "--", "*.go"],
                             capture_output=True, text=True).stdout.splitlines()
        for line in got:
            f, ln = line.split(":", 2)[:2]
            if not is_test_path(f) and ln.isdigit():
                out.append((f, int(ln), fld))
    return out


def value_users(corpus, repo, fields, exclude):
    """The files that read one of those fields: the code whose behaviour the
    value changes, which the question asks for as well as the file that reads
    the key (the decision of 2026-09-20)."""
    out = {f for f, _, _ in field_lines(corpus, repo, fields)}
    return sorted(out - set(exclude))


# What counts as a way of reading a value rather than a piece of work of its
# own. `func (cfg *Cfg) GetContentDeliveryURL(prefix string) (string, error)`
# is one: a method (so the value comes from its own receiver, not from a
# configuration handed to it), 12 lines, and it gives the caller a value back.
# The audit of PE2c measured why each part is needed: without them the hop
# picks up constructors that are handed the whole configuration
# (`NewEvaluatorFactory(cfg, ...)`), helpers that turn a setting into
# something else for everyone (`GetGravatarUrl(cfg, email)`), and functions
# that only report an error (`declareFixedRoles`), none of which give their
# caller the value of the setting (results/pe2c.md).
ACCESSOR_MAX_LINES = 12


REPO_OF = {"c1": ("c1", "wikictl"), "c2": ("c2", "grafana"), "c3": ("c3", "linux")}


def returns_a_value(corpus, path, line, most=5):
    """True when the function declared at that line gives its caller something
    other than an error. Read from the declaration, which the analyzer does
    not keep: the text between the end of the parameters and the brace."""
    c, repo = REPO_OF[corpus]
    try:
        lines = open(os.path.join(repo_dir(c, repo), path), errors="replace").read().split("\n")
    except OSError:
        return False
    text = "\n".join(lines[line - 1:line - 1 + most])
    depth, groups, start, brace = 0, [], None, None
    for i, ch in enumerate(text):
        if ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                groups.append((start, i))
        elif ch == "{" and depth == 0:
            brace = i
            break
    if brace is None or len(groups) < 2:   # not a method declaration
        return False
    res = text[groups[1][1] + 1:brace].strip().strip("()").strip()
    return any(r.strip().split()[-1] != "error" for r in res.split(",") if r.strip())


class GoGraph:
    """The call graph of a corpus (gen/analyze.sh, the same records S3 uses),
    loaded once: which functions a line belongs to, and who calls them."""
    _cache = {}

    def __init__(self, corpus):
        self.corpus = corpus
        self.defs_by_file = collections.defaultdict(list)
        self.callers = collections.defaultdict(set)
        self.loose = collections.defaultdict(set)
        self.generated = set()
        for r in read_jsonl(os.path.join(OUT, f"{corpus}-go.jsonl")):
            if r["rec"] == "def":
                if r.get("generated"):
                    self.generated.add(r["file"])
                if r["kind"] in ("func", "method") and not r["test"]:
                    self.defs_by_file[r["file"]].append(r)
            elif r["rec"] == "call" and not r["test"] and r.get("callee_pkg"):
                # a call, not a reference: a file that hands the function over
                # as a value (a route handler, a wiring table) never receives
                # what it returns. A call the compiler resolves to this very
                # function ("static") is kept apart from one an analysis of the
                # possible types reaches ("vta", "invoke", "dynamic"), which
                # says the call may land here, not that it does: S3 treats
                # those as optional callers and S9 does the same.
                (self.callers if r["kind"] == "static" else self.loose)[
                    (r["callee_pkg"], r["callee"])].add(r["file"])

    @classmethod
    def get(cls, corpus):
        if corpus not in cls._cache:
            cls._cache[corpus] = cls(corpus)
        return cls._cache[corpus]

    def accessors(self, lines):
        """The short methods that hold one of those (file, line) reads and
        give a value back: a way for another file to read the setting."""
        out = []
        for f, ln, fld in lines:
            for d in self.defs_by_file.get(f, ()):
                if (d["kind"] == "method" and d["line"] <= ln <= d["end"]
                        and d["end"] - d["line"] + 1 <= ACCESSOR_MAX_LINES
                        and returns_a_value(self.corpus, f, d["line"])):
                    out.append((d, fld))
        return out

    def hops(self, lines, exclude, loose=False):
        """One step of indirection: the files that call a short method that
        reads the value (a getter), so they take the value without naming the
        key or the field. Returns {file: the getter it calls}; with loose the
        calls that only an analysis of the possible types reaches, which
        belong in the optional part of the gold."""
        out, src = {}, self.loose if loose else self.callers
        for d, fld in self.accessors(lines):
            for f in sorted(src.get((d["pkg"], d["id"]), ())):
                # generated code (the wiring of the dependency injector) calls
                # such a function without using the value
                if not is_test_path(f) and f not in exclude and f not in self.generated:
                    out.setdefault(f, d["id"])
        return out


def camel(term):
    """The camelCase spelling of a setting key, as the frontend writes it
    (`signout_redirect_url` -> `signoutRedirectUrl`)."""
    parts = [p for p in re.split(r"[_.\-]+", term) if p]
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:]) if len(parts) > 1 else term


def mentions(corpus, repo, term, exclude, ext=("*.go", "*.ini", "*.md", "*.ts", "*.tsx"), also=()):
    """Files that write the term but do not handle it: naming one is neither
    required nor wrong (gold "optional"). The other spellings of the same
    thing (the camelCase key, the struct field it is kept in) go in too, so
    that a file which carries the value without being required does not count
    against an answer."""
    words = {term, camel(term)} | set(also)
    args = []
    for w in sorted(words):
        args += ["-e", w]
    got = subprocess.run(["git", "-C", repo_dir(corpus, repo), "grep", "-lF", *args, "--", *ext],
                         capture_output=True, text=True).stdout.split()
    return sorted({p for p in got if not is_test_path(p)} - set(exclude))


def s9(split, corpus, n):
    r = rng(split, "S9", corpus)
    by = lits(corpus)
    pool = []
    if corpus == "c1":
        repo, code_repo = "wiki", "wikictl"
        files = md_files("c1", "wiki", ["projects/wikictl"])
        by_term = collections.defaultdict(list)
        for f in files:
            _, secs = sections("c1", "wiki", f)
            for s in secs:
                for t in set(re.findall(r"`([a-z][a-z0-9_.]{3,40})`", s["text"])):
                    by_term[t].append(s)
        for t, secs in by_term.items():
            code = sorted(by.get(t, ()))
            if 1 <= len(code) <= 3 and ("_" in t or "." in t):
                pool.append((t, secs, code))
    else:
        repo, code_repo = "grafana", "grafana"
        f = "docs/sources/setup-grafana/configure-grafana/_index.md"
        _, secs = sections("c2", "grafana", f)
        parent = None
        docs_text = {}
        for g in md_files("c2", "grafana", ["docs/sources"], [r"/whatsnew/", r"/release-notes/"]):
            docs_text[g] = open(os.path.join(repo_dir("c2", "grafana"), g), errors="replace").read()
        for s in secs:
            if s["level"] == 3:
                m = re.fullmatch(r"`\[([a-z0-9_.\-]+)\]`", s["heading"])
                parent = m.group(1) if m else None
            if s["level"] == 4 and parent and re.fullmatch(r"`[a-z][a-z0-9_]{3,50}`", s["heading"]):
                t = s["heading"].strip("`")
                code = sorted(x for x in by.get(t, ()) if x.startswith("pkg/"))
                if 1 <= len(code) <= 3 and len(s["text"]) >= 60:
                    # every documentation page that writes the key in backticks
                    pages = [dict(s, parent=parent)] + [
                        {"file": g, "line": 1, "end": 1, "heading": "", "text": ""}
                        for g, txt in sorted(docs_text.items()) if g != f and f"`{t}`" in txt]
                    pool.append((t, pages, code))
    pool = [p for p in pool if split_of(f"{corpus}:S9:{p[0]}") == split]
    r.shuffle(pool)
    out, dropped = [], dropped_base_ids()
    for t, secs, code in pool:
        if len(out) == n:
            break
        s = secs[0]
        direction = "doc2code" if len(out) % 2 == 0 else "code2doc"
        key = re.sub(r"[^A-Za-z0-9]+", "-", t) + "-" + direction
        if f"{split}-{corpus}-s9-{key}" in dropped:
            continue
        pages = sorted({x["file"] for x in secs})
        if direction == "doc2code":
            # the files that read the key, plus the files whose behaviour the
            # value changes (they hold the field, not the string), which the
            # review of PE2b found missing from the gold, plus one step of
            # indirection: the files that take the value from a short function
            # that returns it (PE2c; the call graph is the one S3 uses)
            fields = cfg_fields(corpus, code_repo, code, t)
            flines = field_lines(corpus, code_repo, fields) if fields else []
            users = sorted({f for f, _, _ in flines} - set(code))
            hops = GoGraph.get(corpus).hops(flines, set(code) | set(users)) if flines else {}
            if corpus == "c2" and (not fields or len(users) + len(hops) > 4):
                continue  # the value cannot be followed to its users: not askable
            want = sorted(set(code) | set(users) | set(hops))
            # the word that shows why each file is in the gold, for the judge
            # (s9check) to look at: an indirect file never writes the key
            gold_terms = {f: ([t] if f in code else []) + sorted(fields) +
                          ([hops[f].split(".")[-1]] if f in hops else []) for f in want}
            via = {f: ("key" if f in code else "getter" if f in hops else "field") for f in want}
            where = f"wiki の `{s['file']}`" if corpus == "c1" else f"Grafana の設定の文書（`{s['file']}`）の `[{s.get('parent', '')}]` の節"
            q = (f"{where} で説明されている `{t}` を、{code_repo} のソースコードのどのファイルが扱っているか"
                 f"（{S9_HANDLE_JA}）。")
            # a call that only an analysis of the possible types reaches is
            # neither required nor wrong, like the optional callers of S3
            loose = GoGraph.get(corpus).hops(flines, set(want), loose=True) if flines else {}
            gold = {"type": "files", "repo": code_repo, "value": want,
                    "optional": sorted(set(mentions(corpus, code_repo, t, want, also=sorted(fields))) | set(loose))}
            ev = [f"{code_repo}/{c}" for c in want]
            fmt = f"{code_repo} リポジトリの根からのファイルのパスの一覧"
            ptask = ("文書で説明されている次の設定・項目を、ソースコードのどのファイルが扱っている"
                     "（値を読み書きする、値で処理を変える、値を返す関数を通して受け取って使う）かを尋ねる質問")
        else:
            gold_terms = {p: [t] for p in pages}
            via = {p: "page" for p in pages}
            q = (f"{code_repo} のソースコード（`{code[0]}`）が扱っている `{t}` について、"
                 f"その内容を説明している文書のページはどれか（名前が一覧に並ぶだけのページは含めない）。")
            gold = {"type": "files", "repo": repo, "value": pages, "match": "any",
                    "optional": mentions(corpus, repo, t, pages, ext=("*.md",))}
            ev = [f"{repo}/{p}" for p in pages]
            fmt = f"{repo} の根からの文書のページのパスの一覧"
            ptask = "ソースコードで扱っている次の設定・項目について説明している文書のページを尋ねる質問"
        item = base_item(split, corpus, "S9", key, repo, s, {
            "direction": direction, "q_ident": q, "identifiers": [t, s["file"]] + code,
            "subject": {"label": t, "kind": "term", "doc": s["text"][:600], "area": s["heading"]},
            "paraphrase_task": ptask, "answer_format": fmt, "gold": gold,
            "review": {"required": True,
                       "check": "文書の記述とコードの対応が正しいか（同じ文字列でも別の意味で使っていないか）。"
                                f"正解のファイルが「{S9_HANDLE_JA}」に当たるか（当たらないものは gold.optional へ）"},
            "source": {"method": "backticked term in a document == Go string literal / struct tag (go/parser); "
                                 "for doc2code also the files that read the struct field the value is kept in, "
                                 "and the files that call a short function returning it (call graph, one hop)",
                       "code_files": code, "doc_sections": [(x["file"], x["line"]) for x in secs][:10],
                       "gold_via": via, "gold_terms": gold_terms},
        })
        item["evidence_accept"] = ev
        out.append(item)
    return out


# S4 draws three times as deep as the other scenarios (the decision of
# 2026-09-20): its candidates are thrown away by the audit and by the check on
# the paraphrase more often than the rest, and the number of bases is what
# suffers. Drawing deeper is the same answer that worked for S8 in PE2b.
# c4 and c2 draw deeper still (PE2d): 18 candidates gave 6 bases for c4 where
# 8 are wanted, and the check on the key points, which drops about one in five
# candidates, costs c2 two of the eight it had. The draw is a prefix of one
# shuffled pool, so raising the number leaves the candidates already drawn
# where they are and only adds more after them. c1 keeps its number although
# its pool holds three sections in dev: there is nothing deeper to draw there
# (results/pe2c.md, 2.3). The pools of the test split are larger, because no
# audit has taken sections out of them yet (results/pe2d.md).
N = {"S4": {"c1": 24, "c2": 26, "c4": 30}, "S5": {"c1": 6, "c2": 6, "c4": 8}, "S7": {"c1": 4, "c4": 5},
     "S9": {"c1": 8, "c2": 8}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--scenario", default="S4,S5,S7,S9")
    a = ap.parse_args()
    d = os.path.join(CAND, a.split)
    sc = a.scenario.split(",")
    for s, fn, name in (("S4", s4, "S4.jsonl"), ("S5", s5, "S5.jsonl"), ("S7", s7_docs, "S7-docs.jsonl"),
                        ("S9", s9, "S9.jsonl")):
        if s in sc:
            rows = []
            for c, k in N[s].items():
                rows += fn(a.split, c, k)
            write_jsonl(os.path.join(d, name), rows)
            print(s, collections.Counter(r["corpus"] for r in rows))


if __name__ == "__main__":
    main()
