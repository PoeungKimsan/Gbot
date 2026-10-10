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

**A58 — The SQLite startup gate is not run in CI.** The `test` matrix links the *host's*
`libsqlite3` on Linux and macOS (ubuntu-latest ships 3.45.1, macos-latest ~3.43–3.50.4),
which is below every accepted baseline, so `uv run python -m engine.runtime.gates` exits 78
on all three runners and `pytest` never executes (run #3, commit `2590802`). A gate is never
loosened to make a host pass — that is the whole point of `docs/BLOCKERS.md` — and a
GitHub runner is an ephemeral, non-deployment host: the gate exists to protect the host that
writes the journal, where WAL behaviour and the hash chain have to be trustworthy. The gate
therefore runs there (`uv run python -m engine.runtime.gates`, recording blockers in
`docs/BLOCKERS.md`) and CI keeps only the informational "Report loaded SQLite" step, so a
runner's library version stays visible in the log. This supersedes the gate step added in
A32. Restore that step if a runner image ever ships SQLite >= 3.51.3.

**A59 — The gate's host-coupled tests skip on a blocked host instead of failing.** Until now
`engine/tests/runtime/test_gates.py` asserted the *live* host's library against the floor, so
every GitHub runner failed 3 of 614 tests (run #5: `3 failed, 611 passed`; ubuntu links SQLite
3.45.1, macos and windows 3.49.1, on Pythons 3.12.3/3.12.10 rather than this host's 3.12.14). A
host below every floor is a *blocked host*, not a code bug — the gate records it in
`docs/BLOCKERS.md` and stops the engine with exit 78 — so the suite now reports that state as
an explicit skip pointing at the gate CLI, and the two host-coupled logic tests take an injected
version through the A28 parameter. The floors, the rejection table, the bypass-env test and the
gate CLI are unchanged, and `test_host_sqlite_library_meets_the_gate` still asserts on hosts
that do meet a baseline.

---

## Phase 3 (Risk, Metrics & Reporting)

**A60 — `config/risk.yaml` holds exactly two numbers.** `risk_per_trade` (sizing) and
`daily_loss_limit_r` (kill-switch) are the only values Phase 3 reads. A third knob would be a
placeholder for a policy that does not exist yet, which is how configuration files accrue dead
weight. Every value is quoted, and an unquoted `0.01` is refused at load time.

**A61 — One unit is one troy ounce; sizing invents no contract multiplier.** Quantities come
from `InstrumentSpec.trade_units_precision` (5 for XAU_USD) and are checked against
`minimum_trade_size` (1). The notional is `units * price`, the same formula
`engine.domain.positions.Position.notional` uses — verified by test rather than assumed, so a
future instrument with a real contract multiplier cannot be silently mis-sized.

**A62 — Units floor, with ROUND_FLOOR, and a below-minimum result is a *condition* not an
error.** Rounding to *nearest* could commit more of the account than the fraction allowed;
flooring can only leave budget unused. That direction is property-tested over arbitrary
equity/stop/fraction triples. `BelowMinimumTradeSize` is deliberately not `...Error` — it is a
declined opportunity (small account, wide stop), and N818 is suppressed for `risk/*` with the
same reasoning recorded for `JournalExhausted`.

**A63 — The trading day is anchored to 17:00 New York *wall clock* and is calendar-based.**
The day is `[17:00 D, 17:00 D+1)` in `America/New_York`, named by the date it starts, and
half-open so the 17:00 instant itself belongs to the new day. It is deliberately *not*
DST-fixed: 17:00 local is 22:00 UTC in January and 21:00 UTC in July, so the day containing
the spring-forward is 23 real hours and the one containing the fall-back is 25. Weekends and
holidays are ignored — that is the session layer's business, and dropping a Saturday's loss
would be worse than counting it.

**A64 — Realized PnL accumulates; unrealized PnL is a mark that replaces.** Recording an open
mark of 120 and then 35 leaves the day at 35, not 155. Total = realized + unrealized, which is
what equity actually is.

**A65 — The kill-switch latches, and only the day rollover clears it.** `evaluate()` and
`reset()` are the same rollover check, so a mid-loss request to resume trading is refused by
construction rather than by an `if` someone could later delete. A profitable trade, or five,
does not clear it either.

**A66 — The kill-switch persists through an injected sink, not a database import.** AGENTS.md
section 3 forbids cross-layer imports, and `risk` importing `engine.journal` would be exactly
that. So `risk` depends only on `domain`: it submits `RISK_KILLSWITCH` events to an `EventSink`
protocol, and restores from an iterable of `DomainEvent`s someone else read out of the journal.
The tests wire a real `JournalWriter` (submit through the bounded queue, read back through a
read-only connection, stop, restart) — precisely the adapter the runtime phase will own.

**A67 — A restart reconciles the latch against the current trading day, silently.** A latch set
for a day that ended while the process was down is cleared in memory without journalling a
re-arm: the rollover is what would have cleared it, and re-journalling a transition nobody was
alive to observe would make the journal lie about when the engine re-armed.

**A68 — Restore ignores foreign events and tolerates malformed rows.** A caller may hand
`KillSwitch.restore()` every event in the journal; only `RISK_KILLSWITCH` rows are folded, and a
row whose trading day will not parse is skipped rather than guessed at.

**A69 — Only *realized* loss trips the switch.** The tracker carries unrealized marks, but the
latch is evaluated on realized R. Tripping on an intraday mark-to-market drawdown would stop
the engine while its stops are still doing the work, which turns a normal holding period into a
forced flat.

**A70 — Exits are always allowed while tripped.** Blocking an exit is how a stop becomes a
blow-up. `adjudicate()` therefore permits `OrderRole.EXIT` unconditionally, cancels resting
*entries* (a resting limit is an armed entry, so cancelling it is the point), and blocks
submissions that never reached the venue.

**A71 — `metrics/` is float-free by omission, deliberately.** `FLOAT_FREE_PACKAGES` does not
list it, so `performance.py` may hold floats: AGENTS.md 2.1 permits them at the
display/serialization boundary, and a report is that boundary. `risk/` joining the tree *did*
switch that package from skipping to enforcing, which is the point of the scan.

**A72 — Decimal becomes float exactly once, in `metrics.performance._finite`.** Everything
after that conversion is float arithmetic. No other module converts, and no float crosses back
into an engine computation: the report is the sink.

**A73 — Profit factor is `None` when there are no losses, never `Infinity`.** An undefined
ratio printed as `inf` is a value no consumer can act on. The same applies to win rate and
expectancy on an empty dataset, which report `None` rather than `0.0` masquerading as a
measured result.

**A74 — The bootstrap uses a private generator, a fixed seed, and linear-interpolated
percentiles.** `random.Random(BOOTSTRAP_SEED)` means the interval is a function of the trades
and the seed, never of the process's global RNG state — asserted by a test that perturbs the
global generator first. 10,000 resamples and the 2.5th/97.5th percentiles are the usual
defaults; the seed is pinned for the life of the report format so historical numbers do not
shift under a re-read. Below 30 trades the interval is not computed at all, and the point
estimates are still reported.

---

## Phase 4 (Strategy & Replay)

**A75 — `strategy.yaml` is quoted exactly like `risk.yaml`, and is loaded through the same
`decimal_yaml`.** One config boundary, one rule. The document holds nine knobs: two fractal/
sweep counts, a look-back window, three thresholds (all Decimals), an expiry in bars, a stop
buffer in ticks, and a reward multiple. The committed thresholds are the ones the brief
names; the windows are module constants rather than config, because the silver bullet's
03:00/10:00/16:45 are the strategy rather than a tunable of it — the same call the Phase 3
kill-switch made for the 17:00 rollover.

**A76 — The `strategy_version` is the SHA-256 of the *parsed* document's canonical JSON.**
Deliberately not of the file's bytes: a reformatted or reordered file is the same policy, and
the canonical JSON makes that structural. Every edited value moves the digest, because a
rerun under a changed threshold is a different strategy. The version is stamped as a
`RUN_STARTED` event payload — the journal has no header table, so the run's first event *is*
the header — and `run_header_event()` carries only strings so the header canonicalizes
stably and puts no Decimal in the journal's amount column.

**A77 — A bar's `mid_ohlc` is the whole input to the detectors.** The strategy never reads bid
or ask: price discovery is mid-side, and the bid/ask split is the fill model's business
(Phase 2). That is why `Bar.mid_ohlc` is derived rather than stored, and it is what makes a
tick-streamed bar and a batch-built bar produce identical signals.

**A78 — The look-ahead prevention is structural, and the test proves it by mutation.** During
the London window the London daily range is not *filtered out*, it is *absent*: a session
range is handed to the detectors only at the instant it concludes, so a 03:00 bar cannot
reference a range that concludes at 05:00. `permitted_levels()` is the queryable form of the
policy, and it has two halves — the window decides *which* ranges a setup may read, and the
date decides *which day's*. A test rebuilds the London range with its decisive extreme an
hour after the window closes and shows the order never moves; a second test asserts the
permitted levels' *dates*, because the fixture prints the same prices every day and only a
date can tell a stale level from a live one. Both guards were verified by mutation: removing
either one fails the suite.

**A79 — Sweeps target *levels*, and the silver bullet only trades session-range levels.** The
detector's sweep scanner accepts any extreme, fractal swings and concluded ranges alike,
because that is the detector's contract. The silver bullet's `_permitted` then requires the
swept level's source to be a session range, which is what makes the reference guard total: a
fractal sweep can be emitted by the detector and still never be traded. The MSS, by contrast,
breaks a *fractal* swing — the reclaim's own structural point — so all four detectors are
load-bearing.

**A80 — Levels are remembered for two days of M5 bars and then dropped.** `drop_levels()`
is memory management for a process that runs for weeks, and it is the *caller's* decision
because only the caller knows what "unreachable" means. The horizon is 576 bars; anything
older is refused by the date filter anyway.

**A81 — Order ids are `run_id-sb-NNNN`, counted per strategy instance.** Deterministic and
namespaced, so two replays of the same run_id produce byte-identical journals, and the
journal of a live run and a backtest of the same run are comparable.

**A82 — The replay evaluates orders and positions *before* the strategy sees the bar.** An
order placed as bar N's close arrives did not exist while bar N traded, so it cannot fill on
bar N. A position opened by a fill on bar N *is* then tested against bar N, which is the
"entry filled and immediately stopped out" case AGENTS.md 2.5 demands — it falls out of the
ordering rather than needing a special case. The stop triggers on a touch and fills at the
bar extreme plus slippage; the target is a limit, so it needs a tick of travel and is
therefore *harder* to hit than the stop. Both directions of the pessimism are tested.

**A83 — The no-repaint proof compares a run over `bars[:n]` with the full run truncated at
bar `n`.** That is the definition, and it is the only thing a backtest can honestly assert:
a prefix run shares no state with the full run, so any dependence on a bar that had not
printed shows up as a difference. The ATR is cross-checked twice more — streaming against a
batch recomputation, and tick-streamed bars against a batch fold of the same quotes, through
the Phase 1 `BarReconciler`. All three guards were verified by mutation: a one-point change
in the batch ATR fails two of them.

**A84 — The stop sits beyond the *swept extreme*, not beyond the level.** A sweep pierces the
level by at least `sweep_min_ticks`, and the buffer is measured from where the price
actually traded, so the stop is `breach - stop_buffer_ticks` for a long. Measuring from the
level would let a deeper sweep tighten the stop, which is the optimistic direction.

**A85 — A below-zero R setup is declined and recorded, not silently dropped.** A shallow
reclaim can put the gap's midpoint inside the stop, and there is no order that expresses
"risk nothing". `DeclinedSetup` keeps the reason inspectable; a test asserts the sweep stands
and no order is placed. `quantity_fn` defaults to one troy ounce and is injected, so
fixed-fractional sizing (Phase 3's `size_position`) is a Phase 5 wiring decision rather than
a Phase 4 dependency — `strategy/` importing `risk/` beyond the config guards would be a
cross-layer import AGENTS.md section 3 forbids.

**A86 — The strategy refuses an incomplete bar, a non-M5 bar, and out-of-order bars, loudly.**
An incomplete bar has no final close, so every value computed from it would change; a bar
out of order would poison the session buckets. Both are defects, not conditions, so they
raise rather than degrade.

**A87 — The replay's bar loop does not sleep, read the clock, or touch the network.** Every
slippage draw comes from `derive_seed(run_id, order_id)`, so two replays of the same run are
byte-identical, and two replays under different run ids are honestly different runs. That is
what makes a backtest a record rather than a story.

---

## Phase 5 (Runtime Supervisor, Control Plane & Projections)

**A88 — The Phase 0 `AF_UNIX` ban is scoped to the control plane, with one exemption.**
`runtime/systemd.py` names `socket.AF_UNIX`, because `sd_notify` has no other API: systemd's
notification socket is a unix datagram target and nothing else. The exemption is narrow and
tested (`test_the_af_unix_exemption_is_exactly_the_notify_socket` asserts the set is exactly
that one file), and the socket is one-way and systemd-owned, so it cannot carry a command the
way the Phase 0 ban feared. This is a scope correction to a rule written before notifications
existed, not a loosening to make a host pass — and it is recorded here rather than done
silently.

**A89 — The supervisor's exit codes are nested, not parallel.** A blocked start returns 78
*and writes nothing*, because `_begin` has not opened the journal yet; an exhausted journal
returns 70 *and still drains*, because a journal that cannot accept one event may have others
in flight. Both end in the same `_end()`, so a caller cannot get a clean stop for the wrong
reason. A feed failure is neither: the stream dying is journalled as `FEED_STATE_CHANGED` and
the process stops with 0, because systemd is the thing that should restart it.

**A90 — The shutdown budget is honoured in wall-clock time, with both barriers off the loop.**
`JournalWriter.drain` and `stop` block by design, so they run through `asyncio.to_thread`;
a barrier that blocked the loop would make the 10-second deadline unenforceable, which is the
whole point of having one. The bound is asserted with a journal whose drain and stop each
block for five seconds against a two-second budget, and the assertion is the waiter's own
timeout -- so a supervisor that overran its budget simply would not be there. The budget is a
settings field defaulting to the phase's ten seconds, so the deadline is testable at speed;
the drain is capped at half of it, so the stop still has room. Both mutations that remove the
deadline were checked against the suite.

**A91 — The gate that blocks risk is one question, asked in one place.** `runner.advance(bar,
entries=False)` settles exits without asking the strategy for setups, so a PAUSE command, a
tripped kill-switch and a news blackout all funnel through `entries=` and the strategy layer
never learns about any of them. Exits always settle: a blocked exit is how a stop becomes a
blow-up, the same rule the Phase 3 kill-switch enforces on its side.

**A92 — The health probe answers for three things, and a withheld ping is the signal.** The
event loop (a beat the watchdog task records), the journal worker thread, and the bounded
queue. A ping withheld because a dependency is unhealthy is what makes systemd restart a
wedged engine; the probe is in the runtime because it is the only layer that knows all three.

**A93 — The publisher reads the canonical event envelope, one level down.** A journal row's
`payload` column holds the whole canonical event the writer hashed, so the action's fields
live under `payload["payload"]`. A fold that forgets that reads an empty document and reports
a healthy engine with no trades — found by a test, and now pinned by one.

**A94 — The projection's atomic write retries its rename.** `Path.replace` is atomic
everywhere, but on Windows it fails outright while a reader holds the destination open, which
a dashboard reader will briefly every time it serves a page. The retry is in `atomic_write_json`
and the rename is still atomic from the reader's side, because readers only ever open the real
path.

**A95 — The API answers 200 with no database at all.** That is the point of the projection:
the dashboard cannot stop the trader, so a test deletes the journal and asserts every endpoint
still answers. A missing snapshot is a 503 with `cache-control: no-store`, so the next request
re-checks rather than serving a stale "not ready" for ten seconds.

**A96 — `main()` takes its quote source as a parameter.** The live OANDA wiring belongs to the
phase that runs a live engine; a supervisor that cannot be built without a broker connection
cannot be tested, and one that silently invents a transport is worse than one that refuses.
Running `python -m engine.runtime.supervisor` therefore exits with that instruction rather than
trading a day that never happened.

**A97 — The strategy layer gained two seams in Phase 5, both one-directional.**
`BacktestRunner.advance(bar, entries=)` and the `observer` callback exist so the runtime can
drive the same fill loop and record what it did, without the strategy importing a journal or a
gate. `SilverBulletStrategy.withdraw_order` is the book owner's own withdrawal path. Phase 4's
invariants are unchanged and still pass — a live run and a replay are the same code.

**A98 — The news calendar is injected, not loaded at startup.** `NewsCalendar` needs a FRED
transport, so the supervisor takes one or runs without it (spread and ATR gating still apply).
A supervisor that cannot start without the network is a supervisor that cannot be tested, and
the runtime is the layer where that dependency would be legitimate anyway.
