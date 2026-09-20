#!/bin/bash
# cscope: C only (c3). Every .c/.h/.S file of the tree, all architectures, with
# the inverted index (-q) and without /usr/include (-k). A second run with the
# same cscope.out reuses the unchanged files (cscope's own incremental update).
set -euo pipefail
[ "$CORPUS" = c3 ] || exit 3
cd /work/linux
build() {
  find . -path ./.git -prune -o -type f \( -name '*.[chS]' \) -print | sort > /index/cscope.files
  cscope -b -q -k -i /index/cscope.files -f /index/cscope.out
}
case "$1" in
  prepare) exit 0 ;;
  index|update) build ;;
  query)
    cscope -d -L -1 "$Q_SYMBOL" -f /index/cscope.out || true
    cscope -d -L -3 "$Q_SYMBOL" -f /index/cscope.out | head -20 || true ;;
  *) exit 2 ;;
esac
