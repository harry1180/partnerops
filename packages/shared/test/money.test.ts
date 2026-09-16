import { describe, expect, it } from "vitest";
import { addMoney, formatMoney } from "../src/index";

describe("money helpers", () => {
  it("formats amounts without float artifacts", () => {
    expect(formatMoney("1234.56", "USD")).toBe("$1,234.56");
    expect(formatMoney("0.1", "USD")).toBe("$0.10");
    expect(formatMoney("-42.005", "USD")).toBe("-$42.01");
    expect(formatMoney("10", "EUR")).toBe("€10.00");
  });

  it("handles non-finite gracefully", () => {
    expect(formatMoney("abc")).toBe("—");
  });

  it("adds on integer cents with no drift", () => {
    expect(addMoney("0.1", "0.2")).toBe("0.30");
    expect(addMoney("10.005", "-0.005")).toBe("10.00");
    expect(addMoney("-1.00", "2.50")).toBe("1.50");
    expect(addMoney("999999999.99", "0.01")).toBe("1000000000.00");
  });
});
