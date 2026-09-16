#!/usr/bin/env bash
# Build the sandbox the locking document measures: four bare repositories
# used as real remotes, and three working copies. No network.
set -eu
S="${1:?usage: setup.sh <dir>}"
rm -rf "$S"; mkdir -p "$S"/{remotes,work}
cd "$S/remotes"
for r in umbrella a b c; do git init -q --bare "$r.git"; git -C "$r.git" symbolic-ref HEAD refs/heads/main; done
cd "$S/work"
for r in a b c; do
  git init -q "$r"; cd "$r"
  git config user.email sandbox@example.invalid; git config user.name sandbox
  printf 'name: %s\n' "$r" > name.txt
  git add -A; git commit -qm "init $r"
  git remote add origin "$S/remotes/$r.git"; git push -q origin HEAD:refs/heads/main
  cd ..
done
git init -q umbrella; cd umbrella
git config user.email sandbox@example.invalid; git config user.name sandbox
mkdir -p nix; printf '{}\n' > nix/sources.lock
git add -A; git commit -qm "init umbrella"
git remote add origin "$S/remotes/umbrella.git"; git push -q origin HEAD:refs/heads/main
echo "sandbox ready at $S"
