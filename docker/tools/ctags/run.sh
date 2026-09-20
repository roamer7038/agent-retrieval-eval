#!/bin/bash
# universal-ctags: one tags file per repository in /index. ctags has no
# incremental mode; update rebuilds the files.
set -euo pipefail
case "$1" in
  prepare) exit 0 ;;
  index|update)
    for r in $REPOS; do
      ctags -R --fields=+nK --extras=+q -f "/index/$r.tags" "/work/$r"
    done ;;
  query)
    for r in $REPOS; do
      readtags -t "/index/$r.tags" -n -e - "$Q_SYMBOL" || true
    done ;;
  *) exit 2 ;;
esac
