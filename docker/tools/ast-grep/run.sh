#!/bin/bash
# ast-grep has no index; query parses every file of a supported language and
# matches the identifier as a pattern. Not applicable to c4 (documents only).
set -euo pipefail
[ "$CORPUS" != c4 ] || exit 3
case "$1" in
  prepare|index|update) exit 0 ;;
  query)
    for r in $REPOS; do
      ast-grep run --pattern "$Q_SYMBOL" --heading never "/work/$r" | head -50 || true
    done ;;
  *) exit 2 ;;
esac
