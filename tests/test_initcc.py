"""Project settings that point Claude's worktree hooks at this program."""

from __future__ import annotations

import json

import pytest

from conftest import Checkout

from umbrella.initcc import HOOKS, discovered_plugins


def _settings(checkout: Checkout, local: bool = False) -> dict:
    name = "settings.local.json" if local else "settings.json"
    return json.loads((checkout.path / ".claude" / name).read_text())


def _commands(data: dict, event: str) -> list[str]:
    return [
        hook["command"]
        for entry in data["hooks"][event]
        for hook in entry["hooks"]
    ]


def test_initcc_declares_both_worktree_hooks(single: Checkout) -> None:
    assert single.cli("initcc") == 0

    data = _settings(single)
    for event, command in HOOKS.items():
        assert command in _commands(data, event)


def test_initcc_writes_the_shared_file_by_default(single: Checkout) -> None:
    assert single.cli("initcc") == 0

    assert (single.path / ".claude" / "settings.json").exists()
    assert not (single.path / ".claude" / "settings.local.json").exists()


def test_initcc_can_write_the_local_file(single: Checkout) -> None:
    assert single.cli("initcc", "--local") == 0

    assert (single.path / ".claude" / "settings.local.json").exists()
    assert not (single.path / ".claude" / "settings.json").exists()


def test_initcc_keeps_everything_already_in_the_file(single: Checkout) -> None:
    """Whatever else is in there is somebody's decision, not ours."""
    path = single.path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "theirs"}]}
                    ]
                },
            }
        )
    )

    assert single.cli("initcc") == 0

    data = _settings(single)
    assert data["model"] == "opus"
    assert _commands(data, "PreToolUse") == ["theirs"]
    assert HOOKS["WorktreeCreate"] in _commands(data, "WorktreeCreate")


def test_initcc_keeps_a_worktree_hook_that_is_already_there(single: Checkout) -> None:
    path = single.path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "hooks": {
                    "WorktreeCreate": [
                        {"hooks": [{"type": "command", "command": "some other script"}]}
                    ]
                }
            }
        )
    )

    assert single.cli("initcc") == 0

    assert _commands(_settings(single), "WorktreeCreate") == [
        "some other script",
        HOOKS["WorktreeCreate"],
    ]


def test_initcc_is_idempotent(single: Checkout) -> None:
    assert single.cli("initcc") == 0
    first = _settings(single)

    assert single.cli("initcc") == 0

    assert _settings(single) == first


def test_initcc_disables_a_plugin_for_this_project(single: Checkout) -> None:
    """Hooks merge, so turning the other one off is the only way to be alone."""
    assert single.cli("initcc", "--disable-plugin", "jj-worktrees@skills-dir") == 0

    assert _settings(single)["enabledPlugins"] == {"jj-worktrees@skills-dir": False}


def test_initcc_explains_why_it_disabled_a_plugin(
    single: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    assert single.cli("initcc", "--disable-plugin", "jj-worktrees@skills-dir") == 0

    assert "all of them run" in capsys.readouterr().out


def test_initcc_says_nothing_about_plugins_when_none_are_disabled(
    single: Checkout, capsys: pytest.CaptureFixture[str]
) -> None:
    assert single.cli("initcc") == 0

    assert "all of them run" not in capsys.readouterr().out


# -- naming the plugins that stay on ---------------------------------------


def _skills(tmp_path, *names: str):
    root = tmp_path / "skills"
    for name in names:
        manifest = root / name / ".claude-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": name, "version": "0.1.0"}))
    return root


def test_discovered_plugins_reads_the_manifests(tmp_path) -> None:
    root = _skills(tmp_path, "wrapty", "jj-worktrees")

    assert discovered_plugins(root) == [
        "jj-worktrees@skills-dir",
        "wrapty@skills-dir",
    ]


def test_discovered_plugins_ignores_a_directory_with_no_manifest(tmp_path) -> None:
    root = _skills(tmp_path, "wrapty")
    (root / "just-a-skill").mkdir()

    assert discovered_plugins(root) == ["wrapty@skills-dir"]


def test_discovered_plugins_survives_a_broken_manifest(tmp_path) -> None:
    root = _skills(tmp_path, "wrapty")
    broken = root / "broken" / ".claude-plugin" / "plugin.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{not json")

    assert discovered_plugins(root) == ["wrapty@skills-dir"]


def test_disabling_one_plugin_names_the_others_as_enabled(
    single: Checkout, tmp_path
) -> None:
    """A discovered plugin is in no settings file, so writing the key without
    naming it is what would turn it off, if the key is read as an allowlist."""
    root = _skills(tmp_path, "wrapty", "jj-worktrees")

    assert (
        single.cli(
            "initcc",
            "--disable-plugin",
            "jj-worktrees@skills-dir",
            "--skills-dir",
            str(root),
        )
        == 0
    )

    assert _settings(single)["enabledPlugins"] == {
        "jj-worktrees@skills-dir": False,
        "wrapty@skills-dir": True,
    }


def test_naming_the_others_never_overrides_a_choice_already_made(
    single: Checkout, tmp_path
) -> None:
    root = _skills(tmp_path, "wrapty", "jj-worktrees")
    path = single.path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"enabledPlugins": {"wrapty@skills-dir": False}}))

    assert (
        single.cli(
            "initcc",
            "--disable-plugin",
            "jj-worktrees@skills-dir",
            "--skills-dir",
            str(root),
        )
        == 0
    )

    assert _settings(single)["enabledPlugins"]["wrapty@skills-dir"] is False


def test_nothing_is_named_when_no_plugin_is_disabled(
    single: Checkout, tmp_path
) -> None:
    root = _skills(tmp_path, "wrapty")

    assert single.cli("initcc", "--skills-dir", str(root)) == 0

    assert "enabledPlugins" not in _settings(single)


def test_a_missing_skills_directory_is_not_an_error(
    single: Checkout, tmp_path
) -> None:
    assert (
        single.cli(
            "initcc",
            "--disable-plugin",
            "jj-worktrees@skills-dir",
            "--skills-dir",
            str(tmp_path / "nowhere"),
        )
        == 0
    )

    assert _settings(single)["enabledPlugins"] == {"jj-worktrees@skills-dir": False}


def test_initcc_refuses_a_settings_file_that_is_not_an_object(
    single: Checkout,
) -> None:
    path = single.path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2, 3]")

    assert single.cli("initcc") == 1


def test_initcc_works_on_an_umbrella_too(checkout: Checkout) -> None:
    assert checkout.cli("initcc") == 0

    assert HOOKS["WorktreeRemove"] in _commands(_settings(checkout), "WorktreeRemove")


# -- what the hooks actually run -------------------------------------------


def test_initcc_uses_a_bare_name_by_default(single: Checkout) -> None:
    assert single.cli("initcc") == 0

    assert _commands(_settings(single), "WorktreeCreate") == [
        "umbrella hook worktree-create"
    ]


def test_initcc_can_name_an_absolute_command(single: Checkout) -> None:
    """Claude runs a hook with the environment it started in, not a dev shell."""
    assert single.cli("initcc", "--command", "/opt/bin/umbrella") == 0

    data = _settings(single)
    assert _commands(data, "WorktreeCreate") == [
        "/opt/bin/umbrella hook worktree-create"
    ]
    assert _commands(data, "WorktreeRemove") == [
        "/opt/bin/umbrella hook worktree-remove"
    ]


def test_a_different_command_is_added_rather_than_replacing(single: Checkout) -> None:
    assert single.cli("initcc") == 0
    assert single.cli("initcc", "--command", "/opt/bin/umbrella") == 0

    assert _commands(_settings(single), "WorktreeCreate") == [
        "umbrella hook worktree-create",
        "/opt/bin/umbrella hook worktree-create",
    ]
