import { dateOnly, labelAmount, labelDate, labelText, money, parseTxLabel } from "@/lib/format";
import vectors from "../../../docs/format-vectors.json";

type Lang = "es" | "pt";
import { summarize } from "@/lib/audit";
import { MockCautelaApi } from "@/lib/api/mock/mock-api";

describe("format", () => {
  it("formats amounts with the currency code and local separators", () => {
    expect(money(48900, "COP", "es")).toBe("COP 48.900");
    expect(money(9840, "MXN", "es")).toBe("MXN 9,840.00");
  });

  it("keeps the calendar day of date-only values", () => {
    expect(dateOnly("2026-09-29", "es")).toContain("29");
  });

  it("reads the service's charge labels and keeps unknown shapes as sent", () => {
    const label = parseTxLabel("30/05/2026, Marketplace Uno, S.A., 5335.32 MXN");
    expect(label).toMatchObject({ when: "30/05/2026", who: "Marketplace Uno, S.A.", amount: 5335.32, currency: "MXN" });
    expect(labelAmount(label!, "es")).toBe("MXN 5,335.32");
    expect(parseTxLabel("**** 4417")).toBeNull();
    expect(labelAmount(parseTxLabel("30/05/2026, Tienda, ?")!, "es")).toBe("?");
  });

  // Same vectors as tests/agent/test_fmt.py: reply bubbles (service) and cards (app) must print the same text.
  it.each(vectors.money)("formats $amount $currency in $lang like the service's replies", (v) => {
    expect(money(v.amount, v.currency, v.lang as Lang)).toBe(v.text);
  });

  it.each(vectors.date)("formats $value in $lang like the service's replies", (v) => {
    expect(dateOnly(v.value, v.lang as Lang)).toBe(v.text);
  });

  it.each(vectors.label)("prints label $label in $lang like the service's replies", (v) => {
    expect(labelText(v.label, v.lang as Lang)).toBe(v.text);
  });

  it("keeps a label date of another shape as sent", () => {
    expect(labelDate({ when: "ayer", who: "x", amount: 1, currency: "USD", rawAmount: "1 USD" }, "es")).toBe("ayer");
  });

  it("sums LLM cost from the priced calls of a trace", async () => {
    const trace = await new MockCautelaApi({ latency: [0, 0] }).getTrace("tr_5b2e9c41a7d03f6e");
    const s = summarize(trace.records);
    expect(s.llmCalls).toBe(2);
    expect(s.llmCostUsd).toBeCloseTo(0.001664 + 0.001607, 6);
    expect(s.verified).toBe(1);
  });
});
