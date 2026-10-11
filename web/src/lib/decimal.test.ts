/**
 * Exact decimal arithmetic — the reason the dashboard never prints $2009.7499999999998.
 *
 * The engine publishes prices as integer ticks and money as decimal strings. Turning a tick
 * count back into a price is a multiplication, and doing it in IEEE-754 introduces exactly
 * the class of error the engine spent two phases avoiding. So it is done on `BigInt` here.
 */

import { describe, expect, it } from "vitest";
import {
  formatDecimal,
  parseDecimal,
  ticksToDecimal,
  formatDecimalString,
} from "./decimal";
import { currency, duration, price, rMultiple, shortHash } from "./format";

describe("parseDecimal", () => {
  it("keeps the fraction exactly as written", () => {
    expect(parseDecimal("2009.75")).toEqual({ negative: false, digits: "200975", scale: 2 });
    expect(parseDecimal("-1180.00")).toEqual({ negative: true, digits: "118000", scale: 2 });
    expect(parseDecimal("-.5")).toEqual({ negative: true, digits: "5", scale: 1 });
    expect(parseDecimal("3.40")).toEqual({ negative: false, digits: "340", scale: 2 });
  });

  it("normalises zero", () => {
    expect(parseDecimal("0")).toEqual({ negative: false, digits: "0", scale: 0 });
    expect(parseDecimal("0.00")).toEqual({ negative: false, digits: "0", scale: 0 });
  });

  it("refuses anything that is not a plain decimal", () => {
    for (const bad of ["", "  ", "abc", "1e5", "NaN", "Infinity", "1_000", "1,000"]) {
      expect(parseDecimal(bad)).toBeNull();
    }
  });
});

describe("ticksToDecimal", () => {
  it("multiplies a tick count by a tick size exactly", () => {
    const unit = parseDecimal("0.01")!;
    expect(formatDecimal(ticksToDecimal(200_975, unit)!, { grouping: false })).toBe("2009.75");
    expect(formatDecimal(ticksToDecimal(0, unit)!, { grouping: false })).toBe("0");
    // The case binary floating point gets wrong: 20,097,500 ticks at $0.01 is $200,975,
    // and it arrives here with its two fraction digits intact.
    expect(formatDecimal(ticksToDecimal(20_097_500, unit)!, { grouping: false })).toBe(
      "200975.00",
    );
  });

  it("handles a fractional tick size", () => {
    const unit = parseDecimal("0.001")!;
    expect(formatDecimal(ticksToDecimal(1_234, unit)!, { grouping: false, fraction: 3 })).toBe(
      "1.234",
    );
  });

  it("carries the sign", () => {
    const unit = parseDecimal("0.01")!;
    const value = ticksToDecimal(-350, unit)!;
    expect(value.negative).toBe(true);
    expect(formatDecimal(value, { grouping: false })).toBe("-3.50");
  });

  it("refuses a fractional tick count", () => {
    expect(ticksToDecimal(200.5, parseDecimal("0.01")!)).toBeNull();
  });
});

describe("formatting", () => {
  it("groups the integer part", () => {
    expect(formatDecimalString("-1180.00")).toBe("-1,180.00");
    expect(formatDecimalString("3.40", { signed: true })).toBe("+3.40");
  });

  it("renders a price to two decimals", () => {
    expect(price(200_975, "0.01")).toBe("2,009.75");
    expect(price(200_640, "0.01")).toBe("2,006.40");
    expect(price(200_612, "0.01")).toBe("2,006.12");
    expect(price(null, "0.01")).toBeNull();
    expect(price(200_975, null)).toBeNull();
  });

  it("renders a whole tick count with its zeros rather than dropping them", () => {
    // The regression that motivated lib/decimal.ts: "2" needs "2.00", not "0.02".
    expect(formatDecimalString("2", { fraction: 2 })).toBe("2.00");
    expect(formatDecimalString("3.40", { fraction: 2 })).toBe("3.40");
    expect(formatDecimalString("3.4", { fraction: 2 })).toBe("3.40");
    expect(formatDecimalString("-1180.00", { fraction: 2 })).toBe("-1,180.00");
    expect(formatDecimalString("2009.75")).toBe("2,009.75");
    expect(formatDecimalString("0", { fraction: 2 })).toBe("0.00");
  });

  it("renders an R multiple signed and with its unit", () => {
    expect(rMultiple("2")).toBe("+2.00 R");
    expect(rMultiple("-1")).toBe("-1.00 R");
    expect(rMultiple(null)).toBeNull();
  });

  it("renders money with its prefix and its sign", () => {
    expect(currency("-1180.00")).toBe("-$1,180.00");
    expect(currency("360.00", { signed: true })).toBe("+$360.00");
    expect(currency("360.00")).toBe("$360.00");
    expect(currency(null)).toBeNull();
  });

  it("renders durations in the shapes a countdown uses", () => {
    expect(duration(45)).toBe("45s");
    expect(duration(3_657)).toBe("1h 00m 57s");
    expect(duration(90_061)).toBe("1d 01h");
    expect(duration(-5)).toBe("--");
  });

  it("shortens a digest without hiding it", () => {
    const hash = "9f2c4a7b1e5d8034c6a9b2f7d4e1c8a03b5f6d9e2c7a4b1f8d3e6c0a5b9f2d1e";
    expect(shortHash(hash)).toBe("9f2c4a7b…2d1e");
    expect(shortHash("abc")).toBe("abc");
  });
});
