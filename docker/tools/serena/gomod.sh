#!/bin/sh
# Fill $GOMODCACHE with the modules that the Go code of <repo-url> at <commit>
# needs: only the go.mod/go.sum/go.work files of that commit are checked out.
set -eu
url=$1 commit=$2
d=$(mktemp -d)
cd "$d"
git init -q
git remote add origin "$url"
git fetch -q --depth 1 --filter=blob:none origin "$commit"
git sparse-checkout set --no-cone '/go.work' '/go.work.sum' '**/go.mod' '**/go.sum'
git checkout -q FETCH_HEAD
if [ -f go.work ]; then
  # every module of the workspace
  go work edit -json | jq -r '.Use[].DiskPath' | while read -r m; do (cd "$m" && go mod download); done
fi
go mod download
cd /
rm -rf "$d"
