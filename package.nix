{
  lib,
  # build
  buildPythonApplication,
  hatchling,
  # deps
  pygit2,
  # runtime
  git,
  # tests
  pytestCheckHook,
  jujutsu,
  cacert,
}:
buildPythonApplication {
  pname = "umbrella";
  version = "0.1.0";
  src = lib.cleanSource ./.;
  pyproject = true;

  build-system = [ hatchling ];
  dependencies = [ pygit2 ];

  # libgit2 covers every local read and write, so only git has to be on PATH.
  # jj is deliberately absent: jj mode is opt in, and a user who wants it has
  # jj installed already. Pinning a jujutsu here would drive their repo with a
  # different version from the one they run by hand.
  makeWrapperArgs = [
    "--prefix PATH : ${lib.makeBinPath [ git ]}"
  ];

  pythonImportsCheck = [ "umbrella" ];

  # The suite builds real repositories in the sandbox, with bare origins over
  # file paths. It needs both binaries, and jujutsu here is a test dependency
  # only: the program itself never assumes jj is installed.
  #
  # nix is deliberately not here. The two commands that need it are answered
  # from the lab instead: the sandbox has no network, and recursive nix is off.
  nativeCheckInputs = [
    pytestCheckHook
    git
    jujutsu
  ];

  # pygit2 loads the TLS certificate locations when it is imported, and the
  # sandbox has none. The suite never opens a connection: every remote in it is
  # a path on disk. This only gets the import to succeed.
  preCheck = ''
    export SSL_CERT_FILE=${cacert}/etc/ssl/certs/ca-bundle.crt
  '';

  meta = {
    description = "Drive a repo whose nix/sources.lock names other projects";
    mainProgram = "umbrella";
    maintainers = [ lib.maintainers.lillecarl ];
  };
}
