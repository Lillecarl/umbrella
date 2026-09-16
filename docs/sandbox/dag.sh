#!/usr/bin/env bash
set -eu
S="$1"; cd "$S/work"
g() { git -C "$1" "${@:2}"; }
commit_if_changed() { g "$1" add -A; if ! g "$1" diff --cached --quiet; then g "$1" commit -qm "$2"; echo 1; else echo 0; fi; }
# Graph: c is a leaf; b depends on c; a depends on b and c.  (Same shape as
# ghanix <- user-mode-nixos <- easykubenix <- nixkube.)
declare -A DEPS=( [c]="" [b]="c" [a]="b c" )
ORDER="c b a"
write_lock() {
  local name="$1" out="{" first=1
  for d in ${DEPS[$name]}; do
    [ $first -eq 1 ] || out="$out,"; first=0
    out="$out\"$d\":\"$(g "$d" rev-parse HEAD)\""
  done
  printf '{"deps":%s}}\n' "$out}" | sed 's/}}}$/}}/' > "$name/deps.lock"
}
echo "=== Scenario B: a lockfile per child, one topological pass ==="
echo "--- seed every lock ---"
for n in $ORDER; do write_lock "$n"; commit_if_changed "$n" "seed lock" >/dev/null; done
echo "--- now change the leaf c, then one pass in topological order ---"
printf 'leaf change\n' >> c/name.txt; commit_if_changed c "a real change to the leaf" >/dev/null
rewrites=0
for n in $ORDER; do
  write_lock "$n"
  n_rew=$(commit_if_changed "$n" "follow dependencies")
  rewrites=$((rewrites + n_rew))
  printf '  %s -> %s  %s\n' "$n" "$(g "$n" rev-parse --short HEAD)" "$([ "$n_rew" = 1 ] && echo 'rewritten' || echo 'unchanged')"
done
echo "--- second pass: does anything still disagree? ---"
again=0
for n in $ORDER; do write_lock "$n"; again=$((again + $(commit_if_changed "$n" "second pass"))); done
echo "  commits in pass 1: $rewrites (for ONE leaf change)"
echo "  commits in pass 2: $again  <- 0 means the pass converged"
