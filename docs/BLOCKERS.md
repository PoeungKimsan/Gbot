# Blockers

Hosts that fail a mandatory startup gate are recorded here, and the run stops
there. A gate is never loosened to make a host pass: if a platform's bundled
SQLite is too old, this file is the record of it.

- **Gate:** `engine.runtime.gates.check_sqlite_gate`
- **Exit code:** `78` (`EX_CONFIG` from `sysexits.h`)
- **How to reproduce:** `uv run python -m engine.runtime.gates`

## Accepted SQLite baselines

| Baseline | Condition |
| --- | --- |
| `>= 3.51.3` | any version at or above the primary floor |
| `3.50.x` | line-scoped floor of `>= 3.50.7` |
| `3.44.x` | line-scoped floor of `>= 3.44.6` |

Anything below all three is rejected, including versions that compare
numerically higher but sit on an unpinned maintenance line (for example
`3.45.0`). The floors are read from `sqlite3.sqlite_version_info`, which
describes the library actually linked into the process, not what the OS
package manager reports as installed.

## Current status

No blockers recorded.
