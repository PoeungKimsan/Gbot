"""Machine-enforced repository invariants.

Every check here is a static scan over the source tree, so a violation fails CI
whether or not anybody ran the engine. Rule identifiers map to ``AGENTS.md``
section 2; the scanned layout is ``AGENTS.md`` section 3.

Nothing in this module may import the engine: enforcing the rules must not
depend on the code under test being importable.
"""

import ast
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

#: ``engine/tests/test_invariants.py`` -> repository root.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]
SRC_ROOT: Path = REPO_ROOT / "engine" / "src" / "engine"
API_ROOT: Path = REPO_ROOT / "api"

#: Rule (a): these packages must never host floating point.
#: Rule (a): these packages must never host floating point.
#:
#: ``feed`` and ``news`` are included because they now carry price arithmetic: ``feed``
#: parses OANDA prices and ``news`` computes spread ratios and rolling ATR. Keeping them
#: under the same scan is what stops a float from sneaking in at the ingest boundary.
FLOAT_FREE_PACKAGES: tuple[str, ...] = (
    "domain",
    "execution",
    "risk",
    "strategy",
    "journal",
    "market",
    "feed",
    "news",
)

#: Rule (b): SQL DDL shapes whose text is inspected for banned column types.
_DDL_RE = re.compile(
    r"\bcreate\s+(?:temp\s+|temporary\s+)?(?:table|index|view|trigger)\b"
    r"|\balter\s+table\b"
    r"|\bdrop\s+table\b",
    re.IGNORECASE,
)

#: Rule (b): column types that silently lose precision and must never appear.
_BANNED_SQL_TYPES: tuple[str, ...] = ("real", "float", "double")

#: Rule (d): marker text banned repository-wide (AGENTS.md section 2.4).
_AF_UNIX_MARKER: str = "AF_UNIX"

#: Never walk into these while scanning the tree.
_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".hypothesis",
        ".tox",
        ".coverage",
        "htmlcov",
    }
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _rel(path: Path) -> str:
    """Render ``path`` relative to the repository root."""
    return str(path.relative_to(REPO_ROOT))


def _iter_files(root: Path, suffix: str) -> Iterator[Path]:
    """Yield sorted ``*suffix`` files under ``root``, skipping build/cache dirs."""
    if not root.is_dir():
        return
    for path in sorted(root.rglob(f"*{suffix}")):
        if _EXCLUDED_DIRS.isdisjoint(path.relative_to(root).parts):
            yield path


def _parse(path: Path) -> ast.Module:
    """Parse a Python source file."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """Return the ``id()`` of every docstring constant in ``tree``.

    Docstrings are prose. Scanning them for code would produce false positives
    the moment documentation mentions a banned construct.
    """
    nodes: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            nodes.add(id(first.value))
    return nodes


def _string_nodes(tree: ast.Module) -> Iterator[ast.Constant | ast.JoinedStr]:
    """Yield every string literal and f-string in ``tree``, minus docstrings."""
    docstrings = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node
        elif isinstance(node, ast.JoinedStr):
            yield node


def _float_offences(source: str, filename: str) -> list[str]:
    """Return one message per ``float()`` call or float literal in ``source``."""
    tree = ast.parse(source, filename=filename)
    offenders: list[str] = []
    for node in ast.walk(tree):
        is_call = isinstance(node, ast.Call)
        is_builtin_float = is_call and isinstance(node.func, ast.Name) and node.func.id == "float"
        if is_builtin_float:
            offenders.append(f"{filename}:{node.lineno}: float() call")
        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            offenders.append(f"{filename}:{node.lineno}: float literal {node.value!r}")
    return offenders


def _banned_sql_types(text: str) -> tuple[str, ...]:
    """Return the banned column types present in a SQL DDL string."""
    if not _DDL_RE.search(text):
        return ()
    return tuple(
        name for name in _BANNED_SQL_TYPES if re.search(rf"\b{name}\b", text, re.IGNORECASE)
    )


# --------------------------------------------------------------------------- #
# rule (a): zero floating point in the numeric domain packages
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("package", FLOAT_FREE_PACKAGES)
def test_no_floating_point_in_domain_packages(package: str) -> None:
    """AGENTS.md 2.1 - no float literals or float() in the decimal packages."""
    root = SRC_ROOT / package
    if not root.is_dir():
        pytest.skip(f"{package}/ is not provisioned yet (AGENTS.md section 4)")

    offenders: list[str] = []
    for path in _iter_files(root, ".py"):
        offenders.extend(_float_offences(path.read_text(encoding="utf-8"), _rel(path)))

    assert not offenders, (
        "floating point is banned in these packages; use Decimal or integer ticks:\n"
        + "\n".join(offenders)
    )


def test_float_guard_detects_known_violations() -> None:
    """The guard itself must be non-vacuous."""
    source = "def compute():\n    total = 0.0\n    return float(total + 1.5)\n"
    offenders = _float_offences(source, "synthetic.py")

    assert offenders, "guard did not flag synthetic float usage"
    assert any("float() call" in line for line in offenders)
    assert any("float literal" in line for line in offenders)


def test_float_guard_accepts_decimal_arithmetic() -> None:
    """Decimal and integer sources must pass untouched."""
    source = (
        "from decimal import Decimal\n"
        "def compute() -> Decimal:\n"
        "    total = Decimal('0')\n"
        "    return total + Decimal('1.5')\n"
    )
    assert _float_offences(source, "synthetic.py") == []


# --------------------------------------------------------------------------- #
# rule (b): no approximate numeric column types in SQL DDL
# --------------------------------------------------------------------------- #
def test_no_banned_sql_column_types_in_python() -> None:
    """AGENTS.md 2.3 - REAL/FLOAT/DOUBLE would corrupt a Decimal ledger."""
    offenders: list[str] = []
    for root in (SRC_ROOT, API_ROOT):
        for path in _iter_files(root, ".py"):
            for node in _string_nodes(_parse(path)):
                text = node.value if isinstance(node, ast.Constant) else ast.unparse(node)
                hits = _banned_sql_types(text)
                if hits:
                    offenders.append(f"{_rel(path)}:{node.lineno}: banned type(s) {hits}")

    assert not offenders, (
        "use NUMERIC/DECIMAL or integer columns and convert to Decimal:\n" + "\n".join(offenders)
    )


def test_no_banned_sql_column_types_in_sql_files() -> None:
    """Same rule as above, applied to hand-written schema and migration files."""
    offenders: list[str] = []
    for path in _iter_files(REPO_ROOT, ".sql"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            hits = _banned_sql_types(line)
            if hits:
                offenders.append(f"{_rel(path)}:{lineno}: banned type(s) {hits}")

    assert not offenders, (
        "use NUMERIC/DECIMAL or integer columns:\n" + "\n".join(offenders)
    )


def test_banned_sql_type_guard_detects_known_violations() -> None:
    """The guard itself must be non-vacuous."""
    assert _banned_sql_types("CREATE TABLE ticks (ts INTEGER, price REAL)") == ("real",)
    assert _banned_sql_types("CREATE TABLE ticks (ts INTEGER, price DOUBLE)") == ("double",)
    assert _banned_sql_types("CREATE TABLE ticks (ts INTEGER, price TEXT)") == ()
    assert _banned_sql_types("select price from ticks") == ()


# --------------------------------------------------------------------------- #
# rule (c): no builtin hash(), which is salted and unusable for chaining
# --------------------------------------------------------------------------- #
def test_no_builtin_hash_calls_in_engine() -> None:
    """AGENTS.md 2.3 - hash-chain row identity requires sha256, not hash()."""
    offenders: list[str] = []
    for path in _iter_files(SRC_ROOT, ".py"):
        for node in ast.walk(_parse(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "hash"
            ):
                offenders.append(f"{_rel(path)}:{node.lineno}: builtin hash() call")

    assert not offenders, (
        "use hashlib.sha256() for row hashing; builtin hash() is salted per process:\n"
        + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- #
# rule (d): no AF_UNIX anywhere in the source tree
# --------------------------------------------------------------------------- #
def test_no_af_unix_sockets_in_source() -> None:
    """AGENTS.md 2.4 - the control plane is TCP, so AF_UNIX must never appear.

    The scan is AST-based: it flags real references to the symbol, so prose that
    merely explains why AF_UNIX is banned does not trip it.
    """
    offenders: list[str] = []
    for root in (SRC_ROOT, API_ROOT):
        for path in _iter_files(root, ".py"):
            for node in ast.walk(_parse(path)):
                is_name = isinstance(node, ast.Name) and node.id == _AF_UNIX_MARKER
                is_attr = isinstance(node, ast.Attribute) and node.attr == _AF_UNIX_MARKER
                if is_name or is_attr:
                    offenders.append(f"{_rel(path)}:{node.lineno}: {_AF_UNIX_MARKER} reference")

    assert not offenders, (
        "control plane uses TCP on loopback (engine.runtime.platform); AF_UNIX is banned:\n"
        + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- #
# phase 0 contract: the workspace provisions declared in AGENTS.md section 4
# --------------------------------------------------------------------------- #
def test_pyproject_registers_platform_markers() -> None:
    """Markers must be registered so --strict-markers cannot reject them."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = pyproject["tool"]["pytest"]["ini_options"]["markers"]
    # Markers may be a list of strings or a list of tables in older pytest.
    names = {str(entry).split(":")[0].strip() for entry in markers}

    assert "posix_only" in names
    assert "slow" in names


def test_pyproject_pins_mandatory_dependencies() -> None:
    """tzdata is mandatory for IANA zones; exactly one Parquet engine is allowed."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = {req.lower() for req in pyproject["project"]["dependencies"]}
    dev = {
        req.lower()
        for group in pyproject["dependency-groups"].values()
        for req in group
    }

    assert any(req.startswith("tzdata") for req in runtime), "tzdata must be a runtime dependency"
    assert "fastparquet" not in runtime | dev, "only one Parquet engine is permitted"
    assert "pyarrow" in runtime
    for required in ("pytest", "pytest-asyncio", "hypothesis", "ruff"):
        assert required in dev, f"{required} must be a dev dependency"


def test_python_version_is_pinned_to_3_12() -> None:
    """uv needs a single supported interpreter line."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["requires-python"] == ">=3.12,<3.13"
