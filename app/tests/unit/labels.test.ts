import { policyWords } from "@/lib/labels";

describe("policyWords", () => {
  it("shows the policy engine's evidence with the words every page uses", () => {
    expect(policyWords("MX-WINDOW-001 (legal, team_research): claim window 90 natural days, deadline 2026-09-16: inside"))
      .toBe("MX-WINDOW-001 (law, team_research): claim window 90 calendar days, deadline 2026-09-16: inside");
    expect(policyWords("SYN-AMOUNT-001 (synthetic_policy): USD 478.75 at or above review threshold"))
      .toBe("SYN-AMOUNT-001 (synthetic policy): USD 478.75 at or above review threshold");
  });

  it("leaves other text alone", () => {
    expect(policyWords("Charge identified by learned_ranker_disposition:disposition_multinomial_logreg_C1"))
      .toBe("Charge identified by learned_ranker_disposition:disposition_multinomial_logreg_C1");
  });
});
