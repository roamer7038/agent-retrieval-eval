#!/bin/sh
# PE3: the preliminary L1 measurement over the development questions.
#
#   ARE_DATA=<...> l1/pe3.sh
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
