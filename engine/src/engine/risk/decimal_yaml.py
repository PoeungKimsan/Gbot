"""YAML loading that never lets a float into a risk document.

YAML is a convenience format with a money-shaped hole in it: ``0.01`` parses as a
binary float, so an unquoted rate silently becomes the nearest representable double
and every downstream calculation inherits that error. This module refuses such a file
at load time. Quoting is the fix, and it is enforced rather than documented.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Final

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "DecimalYamlError",
    "decimal_scalar",
    "load_decimal_yaml",
]

#: Keys of a ``Decimal`` field that must always be accepted from an integer form,
#: so a limit may be written as ``3`` rather than ``"3"``.
_MAX_KEY_PATH: Final[int] = 64


class DecimalYamlError(ValueError):
    """A risk configuration file is unusable: missing, malformed, or float-valued."""


def load_decimal_yaml(path: Path | str) -> dict[str, object]:
    """Parse a YAML document, refusing any float it contains.

    Args:
        path: Path to the YAML file.

    Returns:
        The document as a mapping. Numeric scalars arrive as ``int`` or ``str``;
        a float never makes it out of here.

    Raises:
        DecimalYamlError: if the file is absent, unparseable, not a mapping, has
            non-string keys, or carries a float anywhere in the tree.
    """
    location = Path(path)
    if not location.is_file():
        raise DecimalYamlError(f"config not found at {location}")

    try:
        text = location.read_text(encoding="utf-8")
    except OSError as exc:
        raise DecimalYamlError(f"config {location} is unreadable: {exc}") from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DecimalYamlError(f"config {location} is not valid YAML: {exc}") from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise DecimalYamlError(
            f"config {location} must be a mapping, got {type(raw).__name__}"
        )

    _reject_floats(raw, location=location, path="")
    return dict(raw)


def _reject_floats(node: object, *, location: Path, path: str) -> None:
    """Walk a parsed document and refuse the first float, naming where it was."""
    if isinstance(node, float):
        raise DecimalYamlError(
            f"{location}: '{path or 'document'}' is a float ({node!r}); quote it so "
            "it parses as an exact Decimal"
        )
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                raise DecimalYamlError(
                    f"{location}: keys must be strings, got {type(key).__name__}"
                )
            child = f"{path}.{key}" if path else key
            if len(child) > _MAX_KEY_PATH:
                child = child[-_MAX_KEY_PATH:]
            _reject_floats(value, location=location, path=child)
        return
    if isinstance(node, list):
        for index, value in enumerate(node):
            child = f"{path}[{index}]"
            _reject_floats(value, location=location, path=child)


def decimal_scalar(mapping: Mapping[str, object], key: str) -> Decimal:
    """Read ``key`` as an exact Decimal.

    An ``int`` and a quoted string are both acceptable unambiguously. A ``bool`` is
    not an integer here, and neither is a float: neither has a decimal meaning that
    can be recovered after YAML has parsed it.

    Args:
        mapping: The already float-checked document.
        key: The scalar to read.

    Returns:
        The value as a finite :class:`~decimal.Decimal`.

    Raises:
        DecimalYamlError: if the key is absent, or the value is not an int or a
            parseable, finite string.
    """
    if not isinstance(mapping, dict):
        raise DecimalYamlError(
            f"'{key}' requires a mapping, got {type(mapping).__name__}"
        )
    if key not in mapping:
        raise DecimalYamlError(f"missing '{key}'")

    value = mapping[key]
    if value is None or isinstance(value, (bool, float, dict, list)):
        raise DecimalYamlError(
            f"'{key}' must be an int or a quoted Decimal string, got "
            f"{type(value).__name__}"
        )

    try:
        parsed = Decimal(value.strip() if isinstance(value, str) else value)
    except (InvalidOperation, ValueError, ArithmeticError) as exc:
        raise DecimalYamlError(f"'{key}' is not a decimal: {value!r}") from exc

    if not parsed.is_finite():
        raise DecimalYamlError(f"'{key}' must be finite, got {value!r}")
    return parsed
