#!/bin/bash
# ck: one index per repository, kept by ck in /work/<repo>/.ck (the overlay).
# ck works on the current directory, so each command runs inside the
# repository. ck --index is incremental (size and mtime, then a file hash;
# chunks are re-embedded by chunk hash); update runs it again. query uses
# --hybrid (regex + semantic, fused with RRF).
set -euo pipefail
export XDG_CACHE_HOME=/opt/ck-cache NO_COLOR=1
case "$1" in
  prepare) exit 0 ;;
  index|update)
    for r in $REPOS; do
      (cd "/work/$r" && ck --index .)
    done ;;
  query)
    for r in $REPOS; do
      (cd "/work/$r" && ck --hybrid -n --scores --topk 10 "$Q_SYMBOL" . | sed "s/\x1b\[[0-9;]*m//g") || true
    done ;;
  *) exit 2 ;;
esac
