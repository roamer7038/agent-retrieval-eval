#!/bin/sh
# PE3b: the L1 measurement run again over the final development questions.
#
#   ARE_DATA=<...> l1/pe3b.sh
#
# PE3 measured L1 over the questions as they stood then (144 bases, 288
# questions). PE2b..PE2d rebuilt them, so the set is now 177 bases and 354
# questions (284 of which are L1's: S6 and S8 are not part of L1). The same
# cells, the same rules and the same indexes are used; only the questions
# differ, so the two runs can be put side by side.
#
# Every cell runs alone: one slot, one measurement at a time, nothing else
# measuring beside it (the times are meant to be compared between tools).
# Slot a is used throughout, and slot c only for qmd, which needs the GPU;
# they never run at the same time because this script is sequential.
#
# The primary condition (--langs ja; the identifier tools are asked once, in
# the form id) is measured for every cell. The second condition, the English
# translation, is measured afterwards for the text tools, leaving out the
# cells of c3 whose search takes minutes (probe, semble, codebase-memory-mcp):
# those are recorded as not measured rather than measured badly.
set -eu
cd "$(dirname "$0")/.."
ARE_L1_RESULTS="${ARE_L1_RESULTS:-$PWD/results/pe3b-l1.jsonl}"
export ARE_L1_RESULTS
R=l1/run.py

# --- primary: the Japanese question (or the identifiers) -------------------
$R run --slot a --corpora c1 --langs ja --tools grep,ctags,global,ast-grep,probe,zoekt,wikictl,semble,ck,codebase-memory-mcp,codegraph,graphify,gitnexus,serena
$R run --slot c --corpora c1 --langs ja --tools qmd
$R run --slot a --corpora c4 --langs ja --tools grep,ctags,global,probe,zoekt,wikictl,semble,codebase-memory-mcp,codegraph,graphify,gitnexus
$R run --slot c --corpora c4 --langs ja --tools qmd
$R run --slot a --corpora c2 --langs ja --tools grep,ctags,global,ast-grep,probe,zoekt,semble,codebase-memory-mcp,codegraph,graphify,gitnexus,serena
$R run --slot c --corpora c2 --langs ja --tools qmd
$R run --slot a --corpora c3 --langs ja --tools grep,ctags,global,cscope,zoekt,codegraph,ast-grep,semble,codebase-memory-mcp,graphify,probe

# --- second condition: the English translation -----------------------------
$R run --slot a --corpora c1 --langs en --tools grep,probe,zoekt,wikictl,semble,ck,codebase-memory-mcp,gitnexus
$R run --slot c --corpora c1 --langs en --tools qmd
$R run --slot a --corpora c4 --langs en --tools grep,probe,zoekt,wikictl,semble,codebase-memory-mcp,gitnexus
$R run --slot c --corpora c4 --langs en --tools qmd
$R run --slot a --corpora c2 --langs en --tools grep,probe,zoekt,semble,codebase-memory-mcp,gitnexus
$R run --slot c --corpora c2 --langs en --tools qmd
$R run --slot a --corpora c3 --langs en --tools grep,zoekt
