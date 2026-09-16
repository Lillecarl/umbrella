#!/usr/bin/env bash
set -eu
S="$1"; cd "$S/work"
g() { git -C "$1" "${@:2}"; }
commit_if_changed() { g "$1" add -A; if ! g "$1" diff --cached --quiet; then g "$1" commit -qm "$2"; return 0; fi; return 1; }
echo "=== Scenario A: pin-in-commit. Six rounds of trying to agree. ==="
printf '%-6s %-10s %-10s %-22s %s\n' round child umbrella "lock names THIS child?" "pin names THIS umbrella?"
for round in 1 2 3 4 5 6; do
  C=$(g a rev-parse HEAD)
  printf '{"sources":{"a":{"rev":"%s"}}}\n' "$C" > umbrella/nix/sources.lock
  commit_if_changed umbrella "lock a at ${C:0:8}" || true
  U=$(g umbrella rev-parse HEAD)
  printf '%s\n' "$U" > a/umbrella.rev
  commit_if_changed a "pin umbrella at ${U:0:8}" || true
  C2=$(g a rev-parse HEAD)
  locked=$(python3 -c "import json;print(json.load(open('umbrella/nix/sources.lock'))['sources']['a']['rev'])")
  pinned=$(cat a/umbrella.rev)
  printf '%-6s %-10s %-10s %-22s %s\n' "$round" "${C2:0:8}" "${U:0:8}" \
    "$([ "$locked" = "$C2" ] && echo YES || echo 'NO -- stale')" \
    "$([ "$pinned" = "$U" ] && echo YES || echo 'NO -- stale')"
done
