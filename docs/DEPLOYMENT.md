# Deployment

Phase 7 turns the repository into something that runs on a host. This document is the
contract the four systemd units in `systemd/` implement, the two runtime paths the deploy
script wires, and — most importantly — **the three things the shipped engine refuses to do
that the deployment currently works around rather than fixes**.

---

## 1. Layout on the host

```
/opt/xauusd/app            symlink -> the current release
/opt/xauusd/releases/      one directory per deployed release
/opt/xauusd/python          UV_PYTHON_INSTALL_DIR, a self-contained CPython
/etc/xauusd/prod.env        the units' EnvironmentFile, mode 0600
/var/lib/xauusd/journal     the engine's WAL journal       0770 xauusd-engine:xauusd-journal
/var/lib/xauusd/projection  the publisher's snapshots      0750 xauusd-pub:xauusd-proj
/var/lib/xauusd/backups     VACUUM INTO copies + integrity reports
```

### Accounts and groups

| Account | In group | May |
| --- | --- | --- |
| `xauusd-engine` | `xauusd-journal` | write the journal |
| `xauusd-pub` | `xauusd-proj` **and** `xauusd-journal` | read the journal, write the projection |
| `xauusd-web` | `xauusd-proj` | read the projection, nothing else |

`xauusd-web` is deliberately **not** in `xauusd-journal`. The dashboard has no path to
`journal.db` at all, which is the property `engine/tests/api/test_main.py` asserts by
deleting the database and watching every endpoint still answer 200. The unit-level form of
that guarantee is asserted in `engine/tests/deploy/test_systemd_units.py`, which fails if the
web unit ever gains the journal group.

### Creating the accounts

```bash
for group in xauusd-journal xauusd-proj; do
  getent group "$group" >/dev/null || groupadd --system "$group"
done
for user in xauusd-engine xauusd-pub xauusd-web; do
  id -u "$user" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$user"
done
usermod --append --groups xauusd-journal,xauusd-proj xauusd-pub
usermod --append --groups xauusd-proj xauusd-web
usermod --append --groups xauusd-journal xauusd-engine
```

The units fail to start (and `systemd-analyze verify` refuses them) if these do not exist,
which is deliberate: a unit that silently falls back to running as root is worse than one
that will not start.

---

## 2. Cutting and deploying a release

```bash
# On a build host (the dashboard needs the Node toolchain; the target does not).
cd web && npm ci && npm run build && cd ..
scripts/make_release.sh            # -> dist/releases/xauusd-<stamp>.tar.gz[.sha256]

# On the target.
sudo scripts/deploy_ubuntu.sh dist/releases/xauusd-<stamp>.tar.gz
```

`make_release.sh` bundles `engine/src`, `api`, `scripts`, `systemd`, `config`,
`pyproject.toml`, `uv.lock`, `.python-version` and the **built** `web/dist`. It fails if
`web/dist` is absent, because a release cut without a built dashboard deploys an API that
mounts nothing — and that must be a decision, not an accident discovered at 03:00.

`deploy_ubuntu.sh` runs its steps in an order that leaves the previous release serving if
anything fails:

1. extract into `/opt/xauusd/releases/<stamp>` — nothing live is touched;
2. `UV_PYTHON_INSTALL_DIR=/opt/xauusd/python uv sync --frozen --no-dev` — nothing live;
3. `python -m engine.runtime.gates` — the same gate and the same exit code (78) the engine
   uses, so a host that would fail startup fails the deploy;
4. **repoint the `/opt/xauusd/app` symlink** — the single atomic switch;
5. `systemctl daemon-reload` and restart.

Rollback is therefore one line, and it does not need the tarball:

```bash
ln -sfn /opt/xauusd/releases/<previous-stamp> /opt/xauusd/app && systemctl restart 'xauusd-*'
```

`uv sync --frozen` is the point of the `uv.lock` in the bundle: the deploy reproduces
exactly what CI resolved. `--no-dev` keeps the test toolchain off a trading host.

### Why the symlink, and what it does not fix

The engine writes `./journal.db` relative to its working directory, and the API reads
`./projection` relative to its own. Neither takes an environment override (see §4), so the
deploy supplies them as **symlinks inside the release directory**:

```
/opt/xauusd/app/journal.db   -> /var/lib/xauusd/journal/journal.db
/opt/xauusd/app/projection   -> /var/lib/xauusd/projection
```

SQLite follows the link, so the writes land on the state directories that systemd's
`StateDirectory=` and `ProtectSystem=strict` make writable — not on the read-only `/opt`
tree. This works, and it is what lets the units ship as specified. It is also, on purpose,
the *only* place these two paths are wired: a reader who has read §4 knows exactly where to
make the engine take them properly.

---

## 3. The publisher unit, and the loop it execs

Phase 5 built `ProjectionPublisher` and deliberately shipped no process — its contract is
"fold the journal aloud", and a caller could drive it. A unit needs something to exec, so
`scripts/publish_loop.py` is the smallest honest daemon:

* loop on `XAUUSD_PUBLISH_INTERVAL` (default 5 s, comfortably inside the API's own 10 s
  cache lifetime);
* if the journal does not exist yet, **wait** rather than exit — the publisher starts before
  the engine has written a row on a cold host, and a crash-loop over a missing file is not a
  recovery;
* if a fold fails for any other reason, log it and try the next one. An unknown event type
  is already ignored rather than fatal in `outbox.py` ("a new event type is a new phase");
  a daemon that dies on one would take the dashboard down with the engine;
* stop on SIGTERM **or SIGHUP**, and finish the fold it is in.

The one design point worth naming: `_Stop` derives from `BaseException`. An `Exception`
subclass would be caught by the loop's own generic handler, logged as a failed fold, and the
loop would carry on to the next sleep — turning an operator's `systemctl stop` into a
warning line in the journal. A second signal stops for real.

---

## 4. What the shipped engine refuses to do

These are not oversights; they are the reasons the deployment is shaped the way it is. Each
is recorded here rather than worked around inside the engine, because the engine's own
documents already take a position and this phase is not the phase to change it.

### 4.1 The engine will not start: there is no quote source

`python -m engine.runtime.supervisor` exits with a message, not a trace:

```
xauusd-engine: no quote source was injected; the live OANDA feed is wired by
the phase that runs the engine, not by this module
```

`ASSUMPTIONS.md` A96 records the reasoning: *a supervisor without a quote stream would
start, build bars out of nothing, and report a healthy day. Refusing loudly is the honest
instruction.* That is right, and it has a deployment consequence that is not right:

* the exit code is **1** (`SystemExit` with a string);
* the unit specifies `RestartPreventExitStatus=70 78`, so exit 1 is not on that list;
* the unit therefore **restarts every 5 seconds, forever**.

This is a crash-loop by construction, and it is the one thing in this phase that will bite
an operator within a minute of enabling the unit. The fix belongs wherever the live OANDA
feed is wired — that phase must either inject a transport or extend
`RestartPreventExitStatus` to include the "no feed wired yet" exit. **Nothing in `systemd/`
has been changed to hide it**, because the specified restart policy is correct for the
engine that *does* have a feed: 70 means "restarting loses history", 78 means "the host is
wrong", and both are the right codes to refuse.

To see this yourself, before wiring a feed:

```bash
systemctl start xauusd-engine
journalctl -u xauusd-engine -n 20    # the refusal, every five seconds
```

### 4.2 No environment override for the two runtime paths

`SupervisorSettings.journal_path` is a `Path` with no environment hook, and
`create_app(projection_path=...)` is a parameter uvicorn cannot pass. The deploy supplies
both as symlinks (§2). The clean fix is an `XAUUSD_JOURNAL_PATH` and
`XAUUSD_PROJECTION_PATH` read in each entry point, which would let the symlinks go — and
would make the release directory relocatable in the way `deploy_ubuntu.sh` already assumes.

### 4.3 The web unit cannot serve `web/dist` as a mount point

`api/main.py` mounts the bundle with `StaticFiles(directory=..., check_dir=False)` guarded by
an `is_dir()` test, so it works — but the *directory* is behind `ReadOnlyPaths`, which
systemd resolves at unit start. A release whose `web/dist` was built on a host with a
different `node_modules` layout is still fine, because only `web/dist` ships; but a deploy
that forgets to build it first produces a web service that serves JSON and no bundle, and
`make_release.sh` is the guard for that, not the unit.

---

## 5. The nightly backup

`xauusd-backup.timer` fires `xauusd-backup.service` at 01:17 local, `Persistent=true`, so
a host that was down still runs the missed night instead of silently skipping history. The
time is deliberately not a round hour: a host whose other crons all fire on the hour is a
host where 01:00 is the busiest minute it has, and the backup's whole job is to read the
journal while nobody else is writing.

`scripts/backup.py` does four things, in an order that matters:

1. **`VACUUM INTO`** — the only SQLite operation that yields a consistent snapshot of a
   database being written concurrently. A file copy of a WAL database can observe a
   half-committed row; this cannot.
2. **`PRAGMA integrity_check`** on the copy — because a copy that is consistent but corrupt
   is a copy that fails on restore day, which is the one day it matters.
3. **`verify_chain`** on the copy — the same function Phase 2's tests use to detect
   tampering, which is what makes a backup *restorable* rather than merely present.
4. **Rotation** past `XAUUSD_BACKUP_RETENTION_DAYS` (default 14), keyed on the **filename**,
   not the mtime: a file touched by a restore drill has a young mtime and an old name.

Steps 2 and 3 **delete** a copy that fails. A plausibly-named corrupt file in the backup
directory is worse than no backup, because it looks like a recovery. That deletion lives in
`run_backup` rather than in either validator so that a failure by *any* route — a raised
`BackupError`, a `ChainError`, an unexpected `Error` — still removes it.

Nothing in the backup opens the journal for writing: the source is `mode=ro`, and the
backup service's unit declares `ReadOnlyPaths=/var/lib/xauusd/journal`.

---

## 6. Verifying it

CI runs the verification on every push in the `deploy` job, because the units are only ever
run on Ubuntu and `systemd-analyze` is not available on macOS or Windows runners:

```bash
systemd-analyze verify systemd/xauusd-*.service systemd/xauusd-*.timer
bash -n scripts/make_release.sh scripts/deploy_ubuntu.sh   # via bash -n on each
uv run pytest engine/tests/deploy
```

`engine/tests/deploy/` is the machine-checkable half: it parses every unit and asserts the
directives in the Phase 7 brief, plus the cross-unit isolation contract (the web unit is not
a journal member; only the engine, the publisher and the backup touch the journal). On a
Windows dev host the signal-delivery tests skip, because Windows has no catchable SIGTERM —
`os.kill` terminates the process outright.
