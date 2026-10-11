# systemd units

Four units, one timer. They are the deployment surface of this repository, and the
deployment contract they encode is the three-users / two-groups split in the Phase 7
brief:

```
xauusd-engine   runs the trading process   group xauusd-journal
xauusd-pub      folds the journal aloud    group xauusd-proj  + xauusd-journal
xauusd-web      serves the projections     group xauusd-web   + xauusd-proj
```

The groups are what make the isolation real rather than aspirational:

* **`xauusd-journal`** can read the journal. Only the engine and the publisher are in it.
  `xauusd-web` is not, so the web process cannot open `journal.db` even if it wanted to —
  which is exactly the property `api/main.py` claims and `engine/tests/api/test_main.py`
  already asserts by deleting the database and watching the endpoints still answer.
* **`xauusd-proj`** can read the projection directory. Only the publisher and the web
  service are in it, so the projection is writable by exactly one process and readable by
  exactly the one that serves it.

`api/main.py` mounts `web/dist` when it is present and serves the three snapshots from
`projection/`. Both are read-only for the web unit: `ReadOnlyPaths` is stated explicitly
so that a future edit to the unit does not silently make them writable.

## Layout on the host

```
/opt/xauusd/app          symlink to the current release
/opt/xauusd/releases/    one directory per deployed release
/opt/xauusd/python       UV_PYTHON_INSTALL_DIR, a self-contained CPython
/etc/xauusd/prod.env     EnvironmentFile, 0600
/var/lib/xauusd/journal      the engine's WAL journal, 0770 xauusd-engine:xauusd-journal
/var/lib/xauusd/projection   the publisher's atomic snapshots, 0750 xauusd-pub:xauusd-proj
/var/lib/xauusd/backups      VACUUM INTO copies plus integrity reports
```

`systemd-analyze verify` runs in CI on every push. It needs the three accounts and two
groups to exist, so the CI job creates them before verifying; see `.github/workflows/ci.yml`.
