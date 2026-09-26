import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, within } from "@testing-library/react";
import { Decides } from "@/components/insights/decides";
import { DECISION } from "@/lib/insights";

const repo = join(__dirname, "..", "..", "..");

describe("decision rules copied from the service's own sources", () => {
  it("match the values in agent/tools/ranking.py and ml/reports/fitted.json", () => {
    const ranking = readFileSync(join(repo, "agent", "tools", "ranking.py"), "utf8");
    expect(ranking).toContain(`AMBIGUITY_MIN_TOP = ${DECISION.ambiguity.min_top.toFixed(2)}`);
    expect(ranking).toContain(`AMBIGUITY_MIN_MARGIN = ${DECISION.ambiguity.min_margin}`);
    const fitted = JSON.parse(readFileSync(join(repo, "ml", "reports", "fitted.json"), "utf8"));
    expect(fitted.systems.learned_ranker_disposition.policy).toMatchObject({
      t_act: DECISION.disposition.t_act, t_abstain: DECISION.disposition.t_abstain, act_floor: DECISION.disposition.act_floor,
    });
    const rules = readFileSync(join(repo, "agent", "policy", "rules.yaml"), "utf8");
    expect(rules).toContain(`threshold_usd: ${DECISION.amount_review.threshold_usd}`);
    expect(rules).toContain(`fraud_score_at_least: ${DECISION.fraud.score_at_least}`);
    expect(DECISION.claim_windows.map((w) => w.id)).toEqual(["MX-WINDOW-001", "CO-WINDOW-001", "CO-WINDOW-002", "AR-WINDOW-001", "AR-WINDOW-002"]);
  });
});

describe("Decides", () => {
  it("shows the four modes, the thresholds and every claim window with law or synthetic policy", () => {
    render(<Decides />);
    expect(screen.getByRole("heading", { level: 2, name: "How Cautela decides" })).toBeInTheDocument();
    for (const name of ["Acts alone", "Asks the customer", "Confirms before writing", "Hands off to a person"]) {
      expect(screen.getByRole("heading", { level: 3, name: new RegExp(name) })).toBeInTheDocument();
    }
    const thresholds = within(screen.getByRole("region", { name: "Thresholds" }));
    expect(thresholds.getByRole("rowheader", { name: "Match probability to act" }).closest("tr")).toHaveTextContent("≥ 0.79");
    expect(thresholds.getByRole("rowheader", { name: "Act floor" }).closest("tr")).toHaveTextContent("≥ 0.60");
    expect(thresholds.getByRole("rowheader", { name: /Rule baseline/ }).closest("tr")).toHaveTextContent("top < 0.60 or margin < 0.15");
    expect(thresholds.getByRole("rowheader", { name: "Human review amount" }).closest("tr")).toHaveTextContent("USD 450");
    expect(thresholds.getByRole("rowheader", { name: /Exchange rates/ }).closest("tr")).toHaveTextContent("COP 4,000, ARS 350, MXN 17");
    const windows = within(screen.getByRole("region", { name: "Claim windows" }));
    expect(windows.getAllByRole("row")).toHaveLength(1 + 5);
    expect(windows.getByText("MX-WINDOW-001").closest("tr")).toHaveTextContent("law");
    expect(windows.getByText("CO-WINDOW-002").closest("tr")).toHaveTextContent("synthetic policy");
    expect(screen.getByText(/Policy can only narrow a model proposal/)).toBeInTheDocument();
  });
});
