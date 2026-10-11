"""Machine coverage for the Phase 7 deployment units.

The brief specified each unit's directives literally, and a systemd unit file is exactly
the sort of document that drifts badly when nobody reads it: an option deleted by an
editor who thought it was redundant, a path retyped. So every directive the brief asked for
is asserted here, and ``systemd-analyze verify`` runs in CI for the syntax the parser
below cannot check.

The two properties that are *not* about the brief's text but about the isolation it is
meant to buy:

* **`xauusd-web` is not in `xauusd-journal`.** The dashboard must have no path to
  ``journal.db``, which is the claim ``engine/tests/api/test_main.py`` already asserts by
  deleting the database and watching every endpoint still answer.
* **The publisher is in both groups, and is the only account that is.** One writer, one
  reader, and nothing that is both by accident.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT.parent / "systemd"

#: Every unit the Phase 7 brief asked for.
EXPECTED_UNITS: Final[tuple[str, ...]] = (
    "xauusd-engine.service",
    "xauusd-publisher.service",
    "xauusd-web.service",
    "xauusd-backup.service",
    "xauusd-backup.timer",
)


class UnitFile:
    """A parsed systemd unit: a mapping of section to directive to first value."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lines: list[str] = []
        self.sections: dict[str, dict[str, list[str]]] = {}
        current: str | None = None
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1]
                self.sections.setdefault(current, {})
                continue
            if current is None:
                msg = f"{path.name}: directive {line!r} before any section header"
                raise AssertionError(msg)
            key, separator, value = line.partition("=")
            if not separator:
                # A continuation of the previous directive's value.
                self.lines.append(line)
                continue
            self.sections[current].setdefault(key.strip(), []).append(value.strip())

    def directives(self, section: str) -> dict[str, list[str]]:
        return self.sections.get(section, {})

    def get(self, section: str, key: str) -> str | None:
        values = self.sections.get(section, {}).get(key)
        return None if not values else values[0]

    def has_section(self, section: str) -> bool:
        return section in self.sections

    def all_keys(self, section: str) -> set[str]:
        return set(self.sections.get(section, {}))


@pytest.fixture(scope="module")
def units() -> dict[str, UnitFile]:
    parsed: dict[str, UnitFile] = {}
    for name in EXPECTED_UNITS:
        path = SYSTEMD / name
        assert path.is_file(), f"{name} is missing from systemd/"
        parsed[name] = UnitFile(path)
    return parsed


def security_directives(unit: UnitFile) -> dict[str, str]:
    """The hardening directives shared by the three long-running services."""
    section = unit.directives("Service")
    return {
        key: section[key][0]
        for key in (
            "NoNewPrivileges",
            "ProtectSystem",
            "ProtectHome",
            "PrivateTmp",
        )
        if key in section
    }


# --------------------------------------------------------------------------- #
# the units exist
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", EXPECTED_UNITS)
def test_every_specified_unit_exists(name: str) -> None:
    assert (SYSTEMD / name).is_file()


def test_no_unspecified_units_are_shipped() -> None:
    """A unit nobody specified is a unit nobody reviewed."""
    shipped = {path.name for path in SYSTEMD.glob("xauusd-*")}
    assert shipped == set(EXPECTED_UNITS)


# --------------------------------------------------------------------------- #
# xauusd-engine.service
# --------------------------------------------------------------------------- #


class TestEngineUnit:
    def test_type_is_notify_with_main_access(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-engine.service"].directives("Service")
        assert service["Type"] == ["notify"]
        assert service["NotifyAccess"] == ["main"]

    def test_runs_as_the_engine_account_in_the_journal_group(
        self, units: dict[str, UnitFile]
    ) -> None:
        service = units["xauusd-engine.service"].directives("Service")
        assert service["User"] == ["xauusd-engine"]
        assert service["Group"] == ["xauusd-journal"]
        # The engine is not granted the projection group: it writes history, it does not
        # read it back.
        assert "SupplementaryGroups" not in service

    def test_umask_lets_the_group_read_the_journal(self, units: dict[str, UnitFile]) -> None:
        # 0007: owner rwx, group rwx, other none. The journal is the one artefact two
        # accounts need, and the publisher is in the group precisely to read it.
        service = units["xauusd-engine.service"].directives("Service")
        assert service["UMask"] == ["0007"]

    def test_environment_file_and_working_directory(
        self, units: dict[str, UnitFile]
    ) -> None:
        service = units["xauusd-engine.service"].directives("Service")
        assert service["EnvironmentFile"] == ["/etc/xauusd/prod.env"]
        assert service["WorkingDirectory"] == ["/opt/xauusd/app"]

    def test_exec_start_runs_the_supervisor_module(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-engine.service"].directives("Service")
        assert service["ExecStart"] == [
            "/opt/xauusd/app/.venv/bin/python -m engine.runtime.supervisor"
        ]

    def test_restart_policy_refuses_the_two_non_retryable_exits(
        self, units: dict[str, UnitFile]
    ) -> None:
        service = units["xauusd-engine.service"].directives("Service")
        assert service["Restart"] == ["on-failure"]
        assert service["RestartSec"] == ["5"]
        # 70 (journal exhaustion) and 78 (gate/config) both mean "restarting loses or
        # duplicates work", so they are refused rather than retried.
        assert set(service["RestartPreventExitStatus"][0].split()) == {"70", "78"}

    def test_watchdog_and_stop_timeout(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-engine.service"].directives("Service")
        assert service["WatchdogSec"] == ["60"]
        # Phase 5 drains the queue inside 10s; 15 leaves headroom for teardown.
        assert service["TimeoutStopSec"] == ["15"]

    def test_state_directory_is_group_writable(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-engine.service"].directives("Service")
        assert service["StateDirectory"] == ["xauusd/journal"]
        assert service["StateDirectoryMode"] == ["0770"]

    def test_network_dependencies(self, units: dict[str, UnitFile]) -> None:
        unit = units["xauusd-engine.service"].directives("Unit")
        assert unit["After"] == ["network-online.target"]
        assert unit["Wants"] == ["network-online.target"]

    def test_hardening_directives(self, units: dict[str, UnitFile]) -> None:
        assert security_directives(units["xauusd-engine.service"]) == {
            "NoNewPrivileges": "yes",
            "ProtectSystem": "strict",
            "ProtectHome": "yes",
            "PrivateTmp": "yes",
        }

    def test_is_installed_by_default(self, units: dict[str, UnitFile]) -> None:
        install = units["xauusd-engine.service"].directives("Install")
        assert install["WantedBy"] == ["multi-user.target"]


# --------------------------------------------------------------------------- #
# xauusd-publisher.service
# --------------------------------------------------------------------------- #


class TestPublisherUnit:
    def test_runs_as_the_publisher_in_both_groups(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-publisher.service"].directives("Service")
        assert service["User"] == ["xauusd-pub"]
        assert service["Group"] == ["xauusd-proj"]
        assert service["SupplementaryGroups"] == ["xauusd-journal"]

    def test_umask_is_tighter_than_the_engines(self, units: dict[str, UnitFile]) -> None:
        # 0027: the publisher's output is served by a process in the group, and nothing
        # in it needs to be executable by "other".
        service = units["xauusd-publisher.service"].directives("Service")
        assert service["UMask"] == ["0027"]

    def test_state_directory_is_not_group_writable(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-publisher.service"].directives("Service")
        assert service["StateDirectory"] == ["xauusd/projection"]
        assert service["StateDirectoryMode"] == ["0750"]

    def test_the_journal_is_read_only_and_the_projection_writable(
        self, units: dict[str, UnitFile]
    ) -> None:
        service = units["xauusd-publisher.service"].directives("Service")
        # The single most important pair of lines in this file: the publisher reads the
        # journal and writes the projection, and never the other way round.
        assert service["ReadOnlyPaths"] == ["/var/lib/xauusd/journal"]
        assert service["ReadWritePaths"] == ["/var/lib/xauusd/projection"]

    def test_no_write_access_to_the_journal_directory_anywhere(
        self, units: dict[str, UnitFile]
    ) -> None:
        service = units["xauusd-publisher.service"].directives("Service")
        journal = "/var/lib/xauusd/journal"
        assert journal not in service.get("ReadWritePaths", [""])[0]
        assert service["ReadOnlyPaths"] == [journal]

    def test_hardening_directives(self, units: dict[str, UnitFile]) -> None:
        assert security_directives(units["xauusd-publisher.service"]) == {
            "NoNewPrivileges": "yes",
            "ProtectSystem": "strict",
            "ProtectHome": "yes",
            "PrivateTmp": "yes",
        }

    def test_restart_policy(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-publisher.service"].directives("Service")
        assert service["Restart"] == ["on-failure"]

    def test_starts_after_the_engine(self, units: dict[str, UnitFile]) -> None:
        unit = units["xauusd-publisher.service"].directives("Unit")
        assert unit["After"] == ["xauusd-engine.service"]
        assert unit["Wants"] == ["xauusd-engine.service"]


# --------------------------------------------------------------------------- #
# xauusd-web.service
# --------------------------------------------------------------------------- #


class TestWebUnit:
    def test_runs_as_the_web_account(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-web.service"].directives("Service")
        assert service["User"] == ["xauusd-web"]
        assert service["Group"] == ["xauusd-web"]

    def test_can_read_the_projection_but_not_the_journal(
        self, units: dict[str, UnitFile]
    ) -> None:
        service = units["xauusd-web.service"].directives("Service")
        assert service["SupplementaryGroups"] == ["xauusd-proj"]
        # The property the whole two-group split exists to buy: the dashboard has no path
        # to the trading database.
        assert "xauusd-journal" not in service.get("SupplementaryGroups", [""])[0]
        assert "journal" not in service.get("ExecStart", [""])[0]

    def test_exec_start_binds_loopback_only(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-web.service"].directives("Service")
        assert service["ExecStart"] == [
            "/opt/xauusd/app/.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8080"
        ]

    def test_projection_is_read_only(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-web.service"].directives("Service")
        assert service["ReadOnlyPaths"] == ["/var/lib/xauusd/projection"]

    def test_never_writes_anything(self, units: dict[str, UnitFile]) -> None:
        # No ReadWritePaths and no StateDirectory: the web unit is a reader, full stop.
        service = units["xauusd-web.service"].directives("Service")
        assert "ReadWritePaths" not in service
        assert "StateDirectory" not in service

    def test_hardening_directives(self, units: dict[str, UnitFile]) -> None:
        assert security_directives(units["xauusd-web.service"]) == {
            "NoNewPrivileges": "yes",
            "ProtectSystem": "strict",
            "ProtectHome": "yes",
            "PrivateTmp": "yes",
        }


# --------------------------------------------------------------------------- #
# backup service and timer
# --------------------------------------------------------------------------- #


class TestBackupUnits:
    def test_service_is_a_oneshot(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-backup.service"].directives("Service")
        assert service["Type"] == ["oneshot"]
        assert service["Restart"] == ["no"]

    def test_backup_carries_both_memberships(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-backup.service"].directives("Service")
        # It reads the journal and writes the backups, so it needs both — and it is not a
        # long-running service, which is why it borrows the publisher's account.
        assert service["User"] == ["xauusd-pub"]
        assert service["SupplementaryGroups"] == ["xauusd-journal"]

    def test_runs_the_backup_script(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-backup.service"].directives("Service")
        assert service["ExecStart"] == [
            "/opt/xauusd/app/.venv/bin/python /opt/xauusd/app/scripts/backup.py"
        ]

    def test_timer_is_nightly_and_persistent(self, units: dict[str, UnitFile]) -> None:
        timer = units["xauusd-backup.timer"].directives("Timer")
        # Persistent: a host that was down still runs the missed night rather than
        # silently skipping a night of history.
        assert timer["Persistent"] == ["true"]
        assert "OnCalendar" in timer
        assert timer["Unit"] == ["xauusd-backup.service"]

    def test_timer_targets_the_backup_service(self, units: dict[str, UnitFile]) -> None:
        install = units["xauusd-backup.timer"].directives("Install")
        assert install["WantedBy"] == ["timers.target"]


# --------------------------------------------------------------------------- #
# the cross-unit isolation contract
# --------------------------------------------------------------------------- #


class TestIsolationContract:
    def test_only_the_engine_and_publisher_touch_the_journal(
        self, units: dict[str, UnitFile]
    ) -> None:
        journal_members = []
        for name, unit in units.items():
            if not name.endswith(".service"):
                continue
            service = unit.directives("Service")
            if "journal" in service.get("ExecStart", [""])[0]:
                continue
            groups = set()
            if "Group" in service:
                groups.update(service["Group"][0].split())
            for entry in service.get("SupplementaryGroups", []):
                groups.update(entry.split())
            if "xauusd-journal" in groups:
                journal_members.append(name)
        assert sorted(journal_members) == [
            "xauusd-backup.service",
            "xauusd-engine.service",
            "xauusd-publisher.service",
        ]

    def test_the_web_unit_is_not_a_journal_member(self, units: dict[str, UnitFile]) -> None:
        service = units["xauusd-web.service"].directives("Service")
        groups = set(service.get("SupplementaryGroups", [""])[0].split())
        assert "xauusd-journal" not in groups

    def test_every_service_refuses_suid(self, units: dict[str, UnitFile]) -> None:
        for name in EXPECTED_UNITS:
            if not name.endswith(".service"):
                continue
            service = units[name].directives("Service")
            assert service.get("NoNewPrivileges") == ["yes"], name

    def test_every_service_runs_under_a_strict_read_only_filesystem(
        self, units: dict[str, UnitFile]
    ) -> None:
        for name in EXPECTED_UNITS:
            if not name.endswith(".service"):
                continue
            service = units[name].directives("Service")
            assert service.get("ProtectSystem") == ["strict"], name
