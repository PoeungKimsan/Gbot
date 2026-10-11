"""Machine coverage for the shell scripts.

The shell scripts are not Python, so these tests check what a *reader* of them needs to
know: that the release bundle carries the pieces the deploy requires, and that the deploy
refuses to link anything before the gates have passed.

The two properties worth pinning:

* **`make_release.sh` requires `web/dist`.** A release cut without a built dashboard
  deploys an API that mounts nothing, and the deploy is not allowed to say so on the
  operator's behalf at 03:00.
* **`deploy_ubuntu.sh` verifies before it repoints the symlink.** The symlink is the single
  switch, so anything that fails must fail before it moves.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT.parent / "scripts"

RELEASE_SCRIPT = SCRIPTS / "make_release.sh"
DEPLOY_SCRIPT = SCRIPTS / "deploy_ubuntu.sh"

#: The repository paths a release must contain for the deploy to work.
REQUIRED_IN_RELEASE = (
    "engine/src/engine",
    "api",
    "scripts",
    "systemd",
    "config",
    "pyproject.toml",
    "uv.lock",
    "web/dist",
)


def _bash() -> str:
    """A bash interpreter, or a skip if the host has none (Windows dev hosts)."""
    found = shutil.which("bash")
    if found is None:
        pytest.skip("no bash on this host")
    return found


def script_is_executable(path: Path) -> bool:
    mode = path.stat().st_mode
    return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def test_both_scripts_are_executable() -> None:
    """The exec bit is the one thing a Windows checkout cannot preserve by itself.

    `git ls-files -s` reports the *stored* mode, which is what a fresh clone on Linux gets.
    Reading `Path.stat().st_mode` on a Windows dev host reports the mode of the working-tree
    copy, which is always 0644 there — so a Windows-only check would pass a repository whose
    scripts nobody can run. The index mode is the one that matters, and the check is
    deliberately not skipped on Windows.
    """
    git = shutil.which("git")
    if git is None:  # pragma: no cover - no git on a host is itself a broken checkout
        pytest.skip("no git on this host, so the stored mode cannot be read")

    for script in (RELEASE_SCRIPT, DEPLOY_SCRIPT):
        result = subprocess.run(
            [git, "ls-files", "-s", "--", str(script.relative_to(ROOT.parent))],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(ROOT.parent),
        )
        assert result.returncode == 0, f"git ls-files failed for {script.name}"
        mode = result.stdout.split()[0]
        assert mode == "100755", f"{script.name} is mode {mode}, expected 100755"


@pytest.mark.skipif(sys.platform == "win32", reason="needs bash")
def test_both_scripts_pass_bash_syntax_check() -> None:
    bash = _bash()
    for script in (RELEASE_SCRIPT, DEPLOY_SCRIPT):
        result = subprocess.run(
            [bash, "-n", str(script)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


@pytest.mark.skipif(sys.platform == "win32", reason="needs bash")
def test_both_scripts_use_strict_mode() -> None:
    """A deploy script without `set -euo pipefail` continues past a failed step."""
    for script in (RELEASE_SCRIPT, DEPLOY_SCRIPT):
        text = script.read_text(encoding="utf-8")
        assert "set -euo pipefail" in text, script.name


# --------------------------------------------------------------------------- #
# make_release.sh
# --------------------------------------------------------------------------- #


def test_the_release_script_bundles_everything_the_deploy_needs() -> None:
    text = RELEASE_SCRIPT.read_text(encoding="utf-8")

    for entry in REQUIRED_IN_RELEASE:
        if entry == "engine/src/engine":
            # The bundle carries the tree, not a leaf inside it; the deploy checks the leaf.
            assert "engine/src" in text, "engine/src is not in the release bundle"
            continue
        assert entry in text, f"{entry} is not in the release bundle"


def test_the_release_script_requires_the_built_dashboard() -> None:
    """The one check that turns a missing web/dist into a failed cut, not a failed deploy."""
    text = RELEASE_SCRIPT.read_text(encoding="utf-8")
    assert "web/dist" in text
    assert "npm ci && npm run build" in text  # the remediation an operator is told to run


def test_the_release_script_excludes_the_venv_and_node_modules() -> None:
    text = RELEASE_SCRIPT.read_text(encoding="utf-8")

    # The dashboard's node_modules is the one that must never ship; the rest are the usual
    # caches and the venv `uv sync` recreates anyway.
    for excluded in ("web/node_modules", ".venv", "__pycache__", "*.db"):
        assert f"--exclude='{excluded}'" in text, excluded


def test_the_release_script_never_bundles_a_database() -> None:
    """A journal inside a release tarball is a copy of the trading history on a CDN."""
    text = RELEASE_SCRIPT.read_text(encoding="utf-8")
    assert "*.db" in text
    assert "*.db-wal" in text


def test_the_release_script_produces_a_checksum() -> None:
    text = RELEASE_SCRIPT.read_text(encoding="utf-8")
    assert "sha256sum" in text


# --------------------------------------------------------------------------- #
# deploy_ubuntu.sh
# --------------------------------------------------------------------------- #


def test_the_deploy_script_extracts_under_releases() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "/opt/xauusd/releases" in text
    assert "RELEASES=" in text


def test_the_deploy_script_repoints_the_app_symlink_atomically() -> None:
    """`ln -sfn; mv -Tf` is the swap that makes a concurrent reader safe."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "APP_LINK=" in text
    assert "ln -sfn" in text
    assert "mv -Tf" in text


def test_the_deploy_script_syncs_with_the_deployment_python() -> None:
    """UV_PYTHON_INSTALL_DIR keeps the interpreter out of root's home directory."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "UV_PYTHON_INSTALL_DIR=" in text
    assert "uv sync --frozen --no-dev" in text


def test_the_deploy_script_runs_the_gates_before_switching() -> None:
    """A host that would fail startup must fail the deploy, not the engine."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "engine.runtime.gates" in text
    assert "78" in text

    # Comments are stripped before comparing: the header comment also mentions `ln -sfn`,
    # and comparing against prose rather than against the command would pass a deploy whose
    # gates run after the switch.
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    gates = code.index("engine.runtime.gates")
    switch = code.index("ln -sfn")
    assert gates < switch, "the gates must run before the symlink is repointed"


def test_the_deploy_script_reloads_and_restarts_the_units() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "systemctl daemon-reload" in text
    for unit in ("xauusd-engine", "xauusd-publisher", "xauusd-web"):
        assert unit in text


def test_the_deploy_script_documents_a_rollback() -> None:
    """A deploy that cannot be undone is a deploy nobody runs at 03:00."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "rollback" in text.lower()


def test_the_deploy_script_refuses_to_run_as_a_normal_user() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "EUID" in text
    assert "must run as root" in text


def test_the_deploy_script_wires_the_two_runtime_paths() -> None:
    """The engine writes ./journal.db and the API reads ./projection, both relative.

    Neither takes an environment override in the shipped version, so the deploy supplies
    them as symlinks into the state directories. That is what makes the units'
    ProtectSystem=strict and StateDirectory= actually work.
    """
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "/var/lib/xauusd/journal/journal.db" in text
    assert "/var/lib/xauusd/projection" in text


def test_the_deploy_script_validates_the_shipped_units() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "systemd-analyze verify" in text


def test_the_deploy_script_validates_the_archive_layout() -> None:
    """Extracting into an unexpected layout is how a deploy points at yesterday."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "tar -tzf" in text
    assert "unexpected archive layout" in text


@pytest.mark.skipif(sys.platform == "win32", reason="needs bash")
def test_the_deploy_script_refuses_without_a_tarball(tmp_path: Path) -> None:
    bash = _bash()
    result = subprocess.run(
        [bash, str(DEPLOY_SCRIPT), str(tmp_path / "missing.tar.gz")],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "EUID": "1000"},
    )
    # Refuses before touching anything, and says why.
    assert result.returncode != 0
    assert "not found" in result.stderr or "must run as root" in result.stderr
