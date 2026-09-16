#!/usr/bin/env bash
# The one-off measurements the locking document quotes. Run setup.sh first.
#
# Each block prints a question and the answer Nix or git gave. Nothing here
# touches the network: the remotes are the bare repositories setup.sh made.
set -eu
S="${1:?usage: probes.sh <dir made by setup.sh>}"
g() { git -C "$S/work/a" "$@"; }

echo "== 1. Does a flake know its own revision, under --pure-eval? =="
cat > "$S/work/a/flake.nix" <<'NIX'
{ outputs = { self, ... }: { probe = { rev = self.rev or "ABSENT"; }; }; }
NIX
g add flake.nix; g commit -qm "probe flake"; g push -q origin HEAD:refs/heads/main
REV=$(g rev-parse HEAD)
nix eval --pure-eval --json "git+file://$S/remotes/a.git?rev=$REV#probe" 2>/dev/null

echo "== 2. ...and on a dirty working copy? =="
printf 'dirty\n' >> "$S/work/a/name.txt"
nix eval --json "$S/work/a#probe" 2>/dev/null
g checkout -q name.txt

echo "== 3. Can Nix fetch by an arbitrary ref? =="
U=$(git -C "$S/work/umbrella" rev-parse HEAD)
git -C "$S/work/umbrella" update-ref "refs/umbrella/a/$REV" "$U"
git -C "$S/work/umbrella" push -q origin "refs/umbrella/a/$REV"
echo -n "  impure: "
nix eval --impure --expr \
  "(builtins.fetchGit { url = \"file://$S/remotes/umbrella.git\"; ref = \"refs/umbrella/a/$REV\"; }).rev" 2>/dev/null
echo -n "  pure:   "
nix eval --pure-eval --expr \
  "(builtins.fetchGit { url = \"file://$S/remotes/umbrella.git\"; ref = \"refs/umbrella/a/$REV\"; }).rev" 2>&1 \
  | grep -o "doesn't fetch unlocked input" || true

echo "== 4. Is there a primop that reads a ref or a note? =="
nix eval --impure --expr \
  'builtins.filter (n: builtins.match ".*([Gg]it|[Rr]ef|[Nn]ote).*" n != null) (builtins.attrNames builtins)'

echo "== 5. Does a note change the commit it annotates? =="
BEFORE=$(g rev-parse HEAD)
g notes add -f -m "umbrella=$U"
echo "  before=${BEFORE:0:8} after=$(g rev-parse HEAD | cut -c1-8)"

echo "== 6. Which clone shapes carry the note? =="
g push -q origin refs/notes/commits
rm -rf "$S/probe-full" "$S/probe-shallow"
git clone -q "$S/remotes/a.git" "$S/probe-full"
echo -n "  plain clone:              "
git -C "$S/probe-full" notes show "$BEFORE" 2>&1 | head -1
git -C "$S/probe-full" fetch -q origin 'refs/notes/*:refs/notes/*'
echo -n "  ...plus a notes refspec:  "
git -C "$S/probe-full" notes show "$BEFORE" 2>&1 | head -1
git clone -q --depth 1 "file://$S/remotes/a.git" "$S/probe-shallow" 2>/dev/null
git -C "$S/probe-shallow" fetch -q origin 'refs/notes/*:refs/notes/*' 2>/dev/null || true
echo -n "  shallow, same refspec:    "
git -C "$S/probe-shallow" notes show "$BEFORE" 2>&1 | head -1

echo "== 7. Can a PR branch read the note on its merge-base? =="
g checkout -qb probe-pr
printf 'a contribution\n' >> "$S/work/a/name.txt"
g commit -qam "PR commit"
MB=$(g merge-base probe-pr origin/main)
echo -n "  note on merge-base ${MB:0:8}: "
g notes show "$MB" 2>&1 | head -1
echo "  PR head is $(g rev-parse --short probe-pr), untouched"
g checkout -q -

echo "== 8. Does passing sources stop the child resolver evaluating? =="
cat > "$S/child.nix" <<'NIX'
{ sources ? throw "THE CHILD RESOLVER RAN" }: { answer = sources.dep or "none"; }
NIX
echo -n "  sources supplied: "
nix eval --impure --expr "(import $S/child.nix { sources = { dep = \"from-umbrella\"; }; }).answer" 2>/dev/null
echo -n "  sources omitted:  "
nix eval --impure --expr "(import $S/child.nix { }).answer" 2>&1 | grep -om1 "THE CHILD RESOLVER RAN" || true
