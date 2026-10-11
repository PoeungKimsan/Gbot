/**Exact decimal arithmetic for the display boundary.

The engine refuses a float anywhere in its domain (AGENTS.md 2.1) and hands the dashboard
*prices as integer ticks* and *money as decimal strings*. This module is the other end of
that bargain: it turns those wire values into text without ever routing them through an
IEEE-754 double.

That is not pedantry. ``200975 * 0.01`` is ``2009.7499999999998`` in binary floating point,
and a dashboard that prints ``$2009.7499999999998`` for a gold price is a dashboard nobody
trusts with the rest of the number. So multiplication happens on ``BigInt`` and the decimal
point is placed once, at the end.

Nothing here returns a ``number``. The web layer's only floats are the ones the engine's
own reporting module deliberately permits (``metrics.json`` confidence bounds, win rate),
which arrive as JSON numbers and stay that way.
*/

/** A base-10 fixed-point value: ``value = digits * 10^-scale``. */
export interface DecimalValue {
  /** Whether the value is negative. Never true for zero. */
  readonly negative: boolean;
  /** Significant digits, no leading zeros. ``"0"`` for zero. */
  readonly digits: string;
  /** How many of those digits sit right of the decimal point. */
  readonly scale: number;
}

const DECIMAL_PATTERN = /^([+-]?)(\d*)(?:\.(\d*))?$/;

/** Parse a decimal string such as ``"2009.75"``, ``"-.5"`` or ``"-1180.00"``.

Returns ``null`` for anything that is not a plain decimal literal: no exponent form, no
``nan``, no ``inf``, no surrounding whitespace. A publisher that wrote one of those has
written a bug, and a null here becomes a missing cell rather than a silently wrong one.
*/
export function parseDecimal(text: string): DecimalValue | null {
  const match = DECIMAL_PATTERN.exec(text);
  if (match === null) {
    return null;
  }
  const [, sign, integer = "", fraction = ""] = match;
  if (integer === "" && fraction === "") {
    return null;
  }

  let digits = integer + fraction;
  digits = digits.replace(/^0+/, "");
  if (digits === "") {
    return { negative: false, digits: "0", scale: 0 };
  }
  return {
    negative: sign === "-",
    digits,
    scale: fraction.length,
  };
}

/** Multiply a tick count by a decimal tick size exactly.

``ticksToDecimal(200975, parseDecimal("0.01"))`` is ``2009.75`` with no rounding step, so
rendering a tick price is a pure formatting operation and never an approximation.
*/
export function ticksToDecimal(
  ticks: number,
  tickSize: DecimalValue,
): DecimalValue | null {
  if (!Number.isInteger(ticks)) {
    return null;
  }
  if (tickSize.digits === "0") {
    return null;
  }
  const negative = (ticks < 0) !== tickSize.negative;
  const magnitude = BigInt(Math.abs(ticks)) * BigInt(tickSize.digits);
  if (magnitude === 0n) {
    return { negative: false, digits: "0", scale: 0 };
  }
  const digits = magnitude.toString();
  return { negative, digits, scale: tickSize.scale };
}

/** Options for :func:`formatDecimal`. */
export interface DecimalFormat {
  /** How many fraction digits to show. ``"all"`` (default) keeps the wire value as-is. */
  readonly fraction?: number | "all";
  /** Group the integer part with thin separators. Defaults to true. */
  readonly grouping?: boolean;
  /** Always render a leading ``+`` on non-negative values. Defaults to false. */
  readonly signed?: boolean;
}

/** Render a :class:`DecimalValue` as text.

Grouping uses a comma, which is what every figure in a trading terminal uses and what a
Western-locale reader parses without thinking.
*/
export function formatDecimal(value: DecimalValue, options: DecimalFormat = {}): string {
  const { fraction = "all", grouping = true, signed = false } = options;
  const scale = fraction === "all" ? value.scale : fraction;

  // Split the wire digits at the value's own decimal point, *then* re-fit the fraction.
  // Re-fitting first would silently rescale the number: `"2"` at scale 0 padded to two
  // fraction digits reads as `0.02` unless the digits are extended, not shifted.
  const integerRaw =
    value.digits.length > value.scale
      ? value.digits.slice(0, value.digits.length - value.scale)
      : "0";
  const fractionRaw =
    value.digits.length > value.scale
      ? value.digits.slice(value.digits.length - value.scale)
      : value.digits.padStart(value.scale, "0");
  const fractionDigits =
    scale >= value.scale ? fractionRaw.padEnd(scale, "0") : fractionRaw.slice(0, scale);

  const groupedInteger = grouping ? groupDigits(integerRaw) : integerRaw;
  const sign = value.negative ? "-" : signed ? "+" : "";
  const decimalPoint = scale === 0 ? "" : `.${fractionDigits}`;
  return `${sign}${groupedInteger}${decimalPoint}`;
}

/** Format a wire decimal string directly, or return ``null`` if it is not one. */
export function formatDecimalString(
  text: string | null,
  options: DecimalFormat = {},
): string | null {
  if (text === null) {
    return null;
  }
  const value = parseDecimal(text.trim());
  return value === null ? null : formatDecimal(value, options);
}



function groupDigits(digits: string): string {
  let out = "";
  for (let index = digits.length; index > 0; index -= 3) {
    const start = Math.max(0, index - 3);
    out = digits.slice(start, index) + (out === "" ? "" : `,${out}`);
  }
  return out;
}
