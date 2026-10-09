# Assumptions

Every judgement call made during Phase 0 (Workspace Provisioning & Gates). Each entry
is a decision that was not fully determined by the Phase 0 brief, the alternative that
was rejected, and how to change it if the call was wrong.

---

## A. Repository structure

**A1 — One distribution, two importable packages.** The brief said "one uv-managed project"
with `engine` (src layout) and `api` (top-level) importable. Implemented as a single
`pyproject.toml` building one distribution named `xauusd-lab`, with hatchling mapping two
paths into the wheel:

```toml
[tool.hatch.build.targets.wheel]
packages = ["engine/src/engine", "api"]
```

Verified: `uv build` emits a wheel containing both `engine/...` and `api/__init__.py`, and
`import engine, api` both resolve to in-repo paths. *Rejected:* a `[tool.uv.workspace]` with
two member projects — that is two projects, and the brief said one.

**A2 — Build backend is hatchling, not setuptools.** It is uv's default and it maps arbitrary
relative paths to wheel packages explicitly. Swapping to setuptools later means changing
`[build-system]` and nothing else.

**A3 — `engine/` has no nested `pyproject.toml`.** `engine/tests/` is not a package; pytest
collects it via `testpaths`. This keeps the nested-project boundary out of the design until a
phase actually needs it.

**A4 — `web/` and `systemd/` were not created.** They would be empty directories, and Phase 0
explicitly forbids scaffolding for future phases. The CI `web` job and the `.gitignore` /
`.gitattributes` rules are already in place for when they arrive.

**A5 — `api/__init__.py` and `engine/__init__.py` are docstring-only.** No re-exports, so no
import-time coupling exists before the owning phase ships.

---

## B. Toolchain

**A6 — A `.python-version` file pinning `3.12` was added**, beyond the brief. Without it
`uv run` can select a newer system interpreter on hosts that have one; this host has Python
3.14, and 3.14 would have silently violated `requires-python = ">=3.12,<3.13"`. Install it
anywhere a checkout is expected to work without `.venv` yet.

**A7 — mypy is NOT installed or configured.** The brief's dev dependency list is exactly
`pytest`, `pytest-asyncio`, `hypothesis`, `ruff`. Adding mypy would have added a dependency
outside that list, while `AGENTS.md` sections 5 and 7 referenced `uv run mypy src`. That
conflict was resolved in favour of the dependency list and **`AGENTS.md` was amended
surgically** (sections 5 and 7 now say `ruff` only) rather than leaving instructions that no
agent could execute. Add mypy and re-enable those lines when the phase specifying it starts.

**A8 — `asyncio_mode = "auto"`.** Lets later phases write bare `async def test_...`
functions without a per-test decorator. Set to `strict` if explicit markers are preferred.

**A9 — `addopts = "--strict-markers"`.** An unregistered marker becomes a hard error instead
of a warning, which is what makes the `posix_only` / `slow` registration in `pyproject.toml`
actually meaningful.

**A10 — `ruff` rule set.** `E, W, F, I, UP, B, C4, SIM, RUF, N, S, ANN, PTH, ERA, TID` at
line length 100. `PTH` forces `pathlib` (which also matters for A15's socket-path guard),
`ANN` forces annotations so the `Decimal` invariant has a type surface to check, `ERA` keeps
commented-out code out of a codebase whose rules are absolute. Loosen or extend in
`pyproject.toml` § `[tool.ruff.lint]`.

**A11 — `uv.lock` is generated from the ranges in `pyproject.toml`, not written by hand.**
`uv sync --frozen` (the CI command) reproduces exactly what CI installs.

---

## C. Machine-enforced invariants (`engine/tests/test_invariants.py`)

**A12 — The six float-free packages do not exist yet, so those six parametrized cases
`skip` rather than pass vacuously.** Phase 0 provisions only `runtime/`. Once
`domain/`, `execution/`, `risk/`, `strategy/`, `journal/`, `market/` appear, the same tests
stop skipping and start enforcing. To prove the guard is not hollow, two non-vacuous
self-tests run each detector against synthetic source known to violate the rule.

**A13 — SQL DDL detection is pattern-scoped, not blanket.** Only string literals matching a
DDL statement shape (`CREATE TABLE/INDEX/VIEW/TRIGGER`, `ALTER TABLE`, `DROP TABLE`) are
inspected for `REAL` / `FLOAT` / `DOUBLE`. A blanket string search would flag any prose or
log message containing "double". Docstrings are excluded from string scanning via an AST
pass that collects docstring node identities. `.sql` files are scanned line-by-line.

**A14 — The SQL detector depends on the parser folding implicit string concatenation** into a
single `ast.Constant`, so `"CREATE TABLE t (" "price REAL)"` is caught as one node. DDL
assembled at runtime from a list of fragments is outside a static scan's reach; that is a
known limit of static enforcement, not an oversight.

**A15 — `hash()` is detected as a bare `ast.Name` call.** `obj.hash(...)` and
`hashlib.sha256(...)` are `ast.Attribute` and are deliberately not flagged, which is correct:
only the builtin is salted per process and useless for hash chaining.

**A16 — `AF_UNIX` is enforced repo-wide as a fourth machine invariant**, beyond the three the
brief listed, because `AGENTS.md` section 2.4 is mandatory and Phase 0 code must not use it.
The detector flags *symbol references* (`Name` and `Attribute` nodes named `AF_UNIX`), not raw
text — a text-based version false-positived on `platform.py`'s own docstring explaining why
`AF_UNIX` is banned, which is exactly the kind of self-defeating guard this suite must not
contain.

**A17 — Extra Phase-0 contract tests were added** to `test_invariants.py`: that the `posix_only`
and `slow` markers are registered in `pyproject.toml`, that `tzdata` is a runtime dependency,
that `pyarrow` is present and `fastparquet` is absent, and that `requires-python` is still
`>=3.12,<3.13`. These make the Phase-0 brief itself machine-checked rather than only asserted
in prose.

---

## D. Control plane (`engine/runtime/platform.py`)

**A18 — One code path, verified by test.** No `sys.platform`, `os.name`, `platform.system`,
or the literals `win32` / `darwin` / `linux` may appear in `platform.py`; a test scans for
each. Loopback TCP is the single implementation for all three platforms, so the AF_UNIX
path-length problem cannot arise.

**A19 — Token file format.** `config/control.token` is read as the first non-blank,
non-comment line. An optional `KEY=` prefix (e.g. `XAUUSD_CONTROL_TOKEN=...`) is stripped and
surrounding quotes removed, so the file works whether it holds a bare token or a stray
env-style entry. Comments and blank lines are skipped. A whitespace-only `XAUUSD_CONTROL_TOKEN`
in the environment is treated as *unset* and falls through to the file.

**A20 — Port parsing follows `int()` semantics.** `" 8765 "`, `"+8765"` and `"8_765"` all
parse to 8765; `"8765.0"`, `"0x10"` and `"80,80"` raise `ControlPortError`. An empty or
whitespace-only value falls back to the default rather than failing, so hosts that never
touch the control plane still boot. Both behaviours are pinned by tests.

**A21 — Two override env vars exist and are deliberate.** `XAUUSD_REPO_ROOT` relocates the
repo root (containers, non-checkout installs) and `XAUUSD_CONFIG_DIR` relocates the config
directory, so a token can live outside the checkout. Both are consumed by
`engine.runtime.repository_root()` / `control_token_path()`.

**A22 — `repository_root()` raises instead of guessing.** It prefers the override, then walks
up from `engine/runtime/__init__.py`, then from the cwd for non-editable installs, and
raises `RuntimeError` if it finds no `pyproject.toml`. Returning a wrong path silently would
make the gate write blockers into the wrong repository.

---

## E. Gate policy (`engine/runtime/gates.py`)

**A23 — The version predicate is implemented exactly as specified:** pass if
`version >= (3, 51, 3)`, or `line == (3, 50)` and `version >= (3, 50, 7)`, or
`line == (3, 44)` and `version >= (3, 44, 6)`. The important consequence, tested by
`test_legacy_floors_are_line_scoped` and the `REJECTED_SQLITE` table: a version such as
`3.45.0` or `3.49.999` fails even though it compares *lower* than `3.50.7`, because it sits on
an unpinned maintenance line. Conversely `3.60.0` passes the first clause as written. Both
edges are pinned by tests so the reading cannot drift.

**A24 — Exit code 78 is `EX_CONFIG` from `sysexits.h`**, exposed as `EXIT_BLOCKED`, so
supervisors can tell a host-configuration problem from a crash.

**A25 — The gate is not bypassable, and that is tested.** Plausible future override names
(`XAUUSD_SKIP_GATES`, `XAUUSD_ALLOW_OLD_SQLITE`, `XAUUSD_SQLITE_FLOOR`, `XAUUSD_NO_GATES`) are
pinned in `BYPASS_ENV_VARS`; setting them must not change the outcome. Enforcing "never
loosen" mechanically is the point.

**A26 — `docs/BLOCKERS.md` was pre-created with "No blockers recorded."** so the gate has a
discoverable target and git records the document. Failures append a timestamped fingerprint
(host, machine, python, `sqlite_version`, `sqlite_version_info`, exit code, reason); the file
is never rewritten, so history is preserved.

**A27 — No blocker on this host.** uv's managed CPython 3.12.14 links SQLite **3.53.1**, which
satisfies the primary floor. Note that the macOS *system* Python 3.14.7 links SQLite **3.50.4**
and would have been rejected — which is precisely why A6 (`.python-version`) and the
`astral-sh/setup-uv` interpreter matter.

**A28 — `version_info` is accepted as a parameter** on `evaluate_sqlite_version` and
`check_sqlite_gate` so the whole gate can be tested without monkeypatching `_sqlite3`.

---

## F. Config, ignore rules, CI

**A29 — `config/prod.env.example` documents only the variables Phase 0 actually reads**
(`XAUUSD_CONTROL_PORT`, `XAUUSD_CONTROL_TOKEN`) and says so explicitly. Adding OANDA
credentials or feed settings would be scaffolding Phase 2's surface.

**A30 — `python-dotenv` is pinned but no Phase-0 code loads `.env`.** The dependency is
required by the brief; the `.env` bootstrap belongs to the runtime supervisor phase. This is
the one place where a pinned dependency has no consumer yet, and it is noted here on purpose.

**A31 — Extra `.gitignore` entries beyond the brief:** `*.parquet`, `data/raw/`, `data/cache/`,
`*.log`, and OS/editor files. Not requested, but the layout implies Parquet artifacts and raw
market data, and an accidental multi-hundred-MB binary commit is the kind of mistake this file
exists to prevent. Remove them if the team prefers to let each phase declare its own
artifact rules.

**A32 — CI added two steps beyond the brief's four:** a `uv run ruff check .` job on
ubuntu-latest, and a `uv run python -m engine.runtime.gates` step before pytest. The lint job
implements "configured test/lint gates" from the Phase-0 scope; the gate step makes a bad
SQLite host fail the build with the gate's own exit code instead of a confusing assertion.

**A33 — CI uses `astral-sh/setup-uv@v10.2.0`**, the current release at the time of writing
(v10.2.0, 2026-09-21). Bump with uv releases; the `python-version` input is unchanged across
the majors in use. The `web` job is guarded by `hashFiles('web/package.json') != ''` and stays
dormant until the dashboard phase creates the file, so it cannot fail in the meantime.

**A34 — `concurrency.cancel-in-progress: true`** on the workflow, so superseded pushes stop
spending runner minutes. Harmless to drop if full per-push history is wanted.

---

## G. Verification performed

| Check | Command | Result |
| --- | --- | --- |
| Environment parity | `uv sync --frozen` | `Checked 40 packages in 1ms` |
| Startup gate | `uv run python -m engine.runtime.gates` | `ok: sqlite 3.53.1`, exit 0 |
| Tests | `uv run pytest` | **75 passed, 6 skipped** |
| Lint | `uv run ruff check .` | `All checks passed!` |
| Packaging | `uv build` | wheel ships `api/` + `engine/` |
| Imports | `python -c "import engine, api"` | resolve to in-repo paths |
| `.gitignore` | `git check-ignore` | all 9 probe paths correct, `config/prod.env.example` tracked |
| `.gitattributes` | CRLF probe committed | index stored LF (`a\n b\n`) |

The 6 skips are the parametrized float-package cases for the six packages Phase 0 does not
create (A12). They become enforcing as soon as those packages exist.

---

## Phase 1 (Market Data, Feed & News Infrastructure)

**A35 — The six float-free packages gained `feed` and `news`.** Phase 1 puts real price
arithmetic in both: `feed` parses OANDA prices and `news` computes spread ratios and a rolling
ATR. Leaving them outside the scan would let a float enter at the ingest boundary or the
breaker. Scan the same way as Phase 0 — by AST, so prose mentioning `float(` is not flagged.

**A36 — `asyncio` boundary floats are confined and documented.** `feed/oanda_client`
computes every duration as an exact `Decimal` and converts to a `timedelta` through
`dt.timedelta(microseconds=int(...))`. The single unavoidable float is
`duration.total_seconds()` inside the default sleeper, which `asyncio.sleep` requires. That
function is the only one, and every test injects a sleeper, so the float never reaches engine
arithmetic and never appears in a test.

**A37 — Jitter is integer-derived.** `_uniform_jitter` draws from
`random.Random().randrange(...)` and divides Decimals, so no float is ever formed. A
`# noqa: S311` records that this randomness is explicitly not a security decision — it only
spreads reconnect attempts so many workers do not retry in lockstep.

**A38 — Backoff delays are measured in connection attempts, and the terminal state is always
`FAILED`.** `max_attempts` counts connection attempts (not sleeps), so a run with *n* scripts
and budget *n* uses exactly *n* connections and performs *n-1* backoffs. `STALE_FEED` is
transient and observable through `on_state_change`; when the budget runs out the stream reports
`FAILED` so a supervisor never mistakes a stale feed for a clean exit.

**A39 — Staleness is strictly greater than the window.** The phase says "> 10s", so the
detector treats exactly 10s as alive and 10s *plus one microsecond* as stale. Both edges are
pinned by tests, and the heartbeat interval (5s) and threshold (10s) live as module constants
rather than being scattered.

**A40 — A quote-only quiet market is not stale.** Quotes quiet but heartbeats flowing is a
normal idle market; only both quiet together trips staleness. Before the first heartbeat ever
arrives, heartbeats are treated as quiet so the quote side alone can decide.

**A41 — The session anchor is wall-clock, and the tests encode the consequence.** New York is
UTC-5 in winter and UTC-4 in summer, so the 17:00 close is 22:00 UTC in January and 21:00 UTC
in July. `bucket_start` floors on absolute UTC seconds and is therefore DST-immune; only
`SessionAnchor` knows about wall clock. A session's 24 wall-clock hours span a *23-hour* UTC
delta across a spring-forward, which is why the anchor can never be expressed as a fixed UTC
hour.

**A42 — Reconciliation tolerance is one pip, and a pip is 0.0001 for gold.** OANDA's XAU_USD
quotes at 0.01 but defines a pip as 0.0001, so the tolerance is derived from the loaded
`InstrumentSpec.pip` and not from the display tick. A difference of *exactly* one pip is
agreement; anything strictly greater is reported. Mid-side values are compared too.

**A43 — An incomplete streamed bar is "not yet arrived", not "absent".** The reconciler
reports `MISSING` only when the stream produced no bar at all for a bucket, so a bar that
happens to be open when reconciliation runs does not look like a data gap.

**A44 — FRED is authoritative, Forex Factory is advisory.** FRED supplies *which day* a
release lands on and a failure propagates. The YAML supplies *what time of day*, in New York
wall clock, because FRED does not publish release times. Forex Factory can only add
corroborating events: a 429 or a malformed payload logs a warning and yields nothing, and an
advisory event can never create a blackout on its own.

**A45 — The spread breaker uses a median, not a mean.** A single wide quote during a news
spike would poison an average and either block everything or desensitise the breaker. The
median is recomputed rather than maintained incrementally: the window is small and correctness
matters more than the saving. The even-count midpoint is quantized to the coarser input
exponent so no phantom precision is invented.

**A46 — Under-populated breakers stay permissive rather than blocking by default.** With fewer
than `minimum_spread_samples` observations there is no spread block, because blocking on a
median of two values is noise. Again: the breaker reports *why* it has nothing to say.

**A47 — `feed` and `news` were added to the float-free invariant set, so the six Phase-0 skip
cases became four.** `domain`, `execution`, `risk`, `strategy` and `journal` still skip, since
those packages do not exist yet. `market`, `feed` and `news` are now actively enforced.

---

## Phase 2 (Domain, Journal & Execution)

**A48 — One sign convention, stated once.** A ledger balance is **debits less credits**.
Debit-normal accounts (`CASH`, `FEES_PAID`, `REALIZED_PNL`, `UNREALIZED_PNL`, `MARGIN_USED`)
read positive; credit-normal accounts (`EQUITY`, `PAYABLE_TO_BROKER`) read negative. No
uniform formula can make both kinds of account positive at once — that asymmetry is exactly
what makes the trial balance sum to zero. `equity()` is the single place the sign is
resolved, so callers never handle it.

**A49 — A transaction's postings must cover distinct accounts.** Two debits to `CASH` in one
transaction is legal double-entry but lossy for this schema, because the journal keeps one
amount per account. Refused at construction.

**A50 — Re-applying the same `transaction_id` raises.** Replay is the reason: a journal
replayed from the start must reproduce the live ledger, and a repeated tail must not
double-count. The same shape appears in the journal writer, where every `event_id` is
`UNIQUE`.

**A51 — Stop-loss orders trigger on a *touch*, limit orders need a *tick of travel*.** These
are deliberately different and both pessimistic:

- A resting stop becomes a market order the instant the price reaches its level, so it
  triggers at `level` and fills at the bar extreme plus slippage. Requiring overshoot would
  let a stop sit unfilled while the price ran straight through it.
- A limit needs the price to trade *through* the level by a full tick. A touch at the level
  with no trade through is an order sitting unfilled, and assuming otherwise is optimistic.

The comparison in `is_price_past_level` is inclusive at the boundary, because "at least one
tick of travel" means reaching `level + tick` already qualifies.

**A52 — Slippage is integer-tick-based, not float-based.** `random.Random(seed).randrange(0,
max+1)` returns an integer, so the resulting price is exact. The seed is
`int.from_bytes(sha256(f"{run_id}:{order_id}").digest()[:8], "big")`, matching the phase
specification verbatim, and every fill journals its seed and slippage so a replay is
byte-reproducible. A limit fill carries no slippage, because its price is guaranteed.

**A53 — `asyncio` and `threading` boundary floats are confined.** `stop(timeout=...)` and
`drain(timeout=...)` accept an `int` or a `timedelta`; `_seconds_of` converts via integer
microseconds into a float, which is what `asyncio.sleep` and `Thread.join` require. The
result of that arithmetic never touches a price or a balance. The same reasoning covers
`time.monotonic()` in `drain`, which must wait on wall-clock time rather than a test's
injected clock, because it is a shutdown barrier.

**A54 — `JournalExhausted` is deliberately not named `JournalExhaustedError`.** Ruff's N818
wants an `Error` suffix; this is a *condition* with its own exit code (70), caught precisely
at the boundary that maps it. Renaming it would read as a bug rather than a deliberate
state, so the rule is suppressed per-file with that reasoning recorded in `pyproject.toml`.

**A55 — `engine/tests/` is now a package.** Two `test_properties.py` files (domain and
journal) collided under pytest's rootdir-relative module naming — the classic "import file
mismatch" — which broke collection of the whole suite. `__init__.py` files in
`engine/tests/` and each subdirectory give each test module a unique package-qualified
name. This also fixes the earlier problem where running a single test file needed
`engine/tests` on `sys.path`.

**A56 — `OrderStatus.is_terminal` / `is_open` were broken at runtime and the tests never
called them.** Enum sibling members are not in scope inside a property body the way plain
class attributes are, so `FILLED` raised `NameError`. Ruff's `F821` caught what the tests
missed, which is the intended division of labour: the static guard finds what runtime
coverage did not. Both properties now reference members through the class.

**A57 — Hashes use SHA-256, never the builtin `hash()`.** The Phase 0 invariant test caught
three `hash()` calls in `__hash__` methods. The builtin is salted per process, so a hash
computed in one run would not match the same value in another — fatal for anything keyed
across a restart. Each `__hash__` now derives from `hashlib.sha256`.
