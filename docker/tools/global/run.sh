#!/bin/bash
# GNU global: GTAGS/GRTAGS/GPATH per repository in /index/<repo>. C (c3) uses
# the built-in parser (label native, as in the kernel's "make gtags"); the
# other corpora use universal-ctags for Go, TypeScript and Markdown (label
# new-ctags). update is global -u, which re-parses the changed files only.
set -euo pipefail
if [ "$CORPUS" = c3 ]; then export GTAGSLABEL=native; else export GTAGSLABEL=new-ctags; fi
case "$1" in
  prepare) exit 0 ;;
  index)
    for r in $REPOS; do
      mkdir -p "/index/$r"
      (cd "/work/$r" && gtags "/index/$r")
    done ;;
  update)
    for r in $REPOS; do
      (cd "/work/$r" && GTAGSDBPATH="/index/$r" GTAGSROOT="/work/$r" global -u)
    done ;;
  query)
    for r in $REPOS; do
      (cd "/work/$r" && export GTAGSDBPATH="/index/$r" GTAGSROOT="/work/$r"
       global -x -- "$Q_SYMBOL" || true
       global -rx -- "$Q_SYMBOL" | head -20 || true)
    done ;;
  *) exit 2 ;;
esac
