#!/usr/bin/env bash
# The umbrella that locks the nearest ancestor of HEAD. One network call.
set -eu
UMBRELLA_URL="${1:?usage: walkback.sh <umbrella url> <child name>}"
CHILD="${2:?}"
# Every child revision the umbrella has ever locked, as a lookup table.
declare -A LOCKED
while read -r umbrella_rev refname; do
  LOCKED["${refname#refs/umbrella/"$CHILD"/}"]="$umbrella_rev"
done < <(git ls-remote "$UMBRELLA_URL" "refs/umbrella/$CHILD/*")
# Walk HEAD backwards and take the first one that is in it.
steps=0
while read -r sha; do
  if [[ -v "LOCKED[$sha]" ]]; then
    echo "found after $steps step(s): child ${sha:0:8} -> umbrella ${LOCKED[$sha]:0:8}"
    exit 0
  fi
  steps=$((steps + 1))
done < <(git rev-list HEAD)
echo "no ancestor of HEAD has been locked" >&2
exit 1
