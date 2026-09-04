{
  mkShell,
  umbrella,
  # pkgs.jj is a JSON stream editor. jujutsu is the version control system.
  jujutsu,
  git,
}:
mkShell {
  packages = [
    umbrella
    jujutsu
    git
  ];
}
