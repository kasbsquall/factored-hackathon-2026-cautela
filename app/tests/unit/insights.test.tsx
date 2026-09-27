import { render, screen, within } from "@testing-library/react";
import { InsightsView } from "@/components/insights/insights-view";
import {
  BASELINE, COMPLAINT_FCR, DISPUTE, PROPOSED, complaintTypes, disputeChannelDenominator, disputeChannelMix,
  disputesByHour, disputesByWeekday, fcrByReason, interactionChannelMix, reasonLabel,
} from "@/lib/insights";

describe("insights data (copied from data_analytics/reports and ml/reports)", () => {
  it("carries the headline figures of the reports", () => {
    expect(DISPUTE).toMatchObject({ complaints: 12297, all: 67095, rank: 1, slaBreached: 2507 });
    expect(COMPLAINT_FCR).toMatchObject({ resolved: 51021, denominator: 117021 });
    expect(PROPOSED.correct_decision_rate.value).toBeCloseTo(0.898, 3);
    expect(BASELINE.correct_decision_rate.value).toBeCloseTo(0.822, 3);
    expect(PROPOSED.unsafe).toMatchObject({ count: 4, denominator: 1240 });
    expect(BASELINE.unsafe).toMatchObject({ count: 24, denominator: 1240 });
  });

  it("labels every contact reason in English", () => {
    const labels = fcrByReason.map((r) => r.label);
    expect(labels).toContain("Retention");
    expect(labels).not.toContain("Retención");
    expect(reasonLabel("Complaint")).toBe("Complaint");
  });

  it("has denominators that add up", () => {
    expect(disputeChannelDenominator).toBe(DISPUTE.complaints);
    expect(disputesByHour.reduce((s, b) => s + b.count, 0)).toBe(DISPUTE.complaints);
    expect(disputesByWeekday.reduce((s, b) => s + b.count, 0)).toBe(DISPUTE.complaints);
    expect(complaintTypes.reduce((s, t) => s + t.complaints, 0)).toBe(DISPUTE.all);
    for (const mix of [disputeChannelMix, interactionChannelMix]) {
      expect(mix.reduce((s, c) => s + c.share, 0)).toBeCloseTo(1, 3);
    }
  });
});

describe("InsightsView", () => {
  it("tells the story with units, denominators, both caveats and a table view per chart", () => {
    render(<InsightsView />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Why Cautela starts with unrecognized-charge disputes");
    expect(screen.getAllByText("12,297").length).toBeGreaterThan(0);
    expect(screen.getByText("of 67,095 complaints")).toBeInTheDocument();
    const correct = screen.getByText(/vs 82\.2% for the tuned rules baseline/).parentElement!;
    expect(correct).toHaveTextContent("89.8%");
    expect(screen.getByText(/of 1,240 cases, vs 24 for the baseline/)).toBeInTheDocument();
    expect(screen.getByText(/43\.6% first-contact resolution is not a dispute metric/)).toBeInTheDocument();
    expect(screen.getByText(/The dataset is synthetic and templated/)).toBeInTheDocument();
    const figures = screen.getAllByRole("figure");
    expect(figures.length).toBe(10);
    expect(screen.getAllByText("Show the numbers")).toHaveLength(figures.length);
    for (const f of figures) expect(within(f).getByText(/^Source:/)).toBeInTheDocument();
  });

  it("renders the business figures of insights.json with their units", () => {
    render(<InsightsView />);
    const ranking = screen.getByRole("region", { name: "Candidate workflows ranked by agent-hours" });
    const rows = within(ranking).getAllByRole("row");
    expect(rows).toHaveLength(8);
    const chosen = rows[7]!;
    expect(within(chosen).getByRole("rowheader")).toHaveTextContent("7");
    expect(chosen).toHaveTextContent("chosen");
    expect(chosen).toHaveTextContent("278.3 h");
    expect(screen.getByText(/Disputes rank 7 of 7 by these hours/)).toHaveTextContent("chosen for verifiability");
    expect(screen.getByText("50.9")).toBeInTheDocument();
    expect(screen.getByText("212.3")).toBeInTheDocument();
    expect(screen.getByText("USD 509 net per year")).toBeInTheDocument();
    expect(screen.getByText("USD 2,123 net per year")).toBeInTheDocument();
    expect(screen.getByText(/56\.9%, safe act rate \(component\)/)).toBeInTheDocument();
    expect(screen.queryByText(/placeholder \(illustrative placeholder\)/)).toBeNull();
    expect(screen.getByText("not defined")).toBeInTheDocument();
    expect(screen.getByText(/the score threshold adds nothing beyond the flag/)).toBeInTheDocument();
    expect(screen.getByText(/exists for 471 of 12,297 disputes/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Assumptions behind the business figures" })).toBeInTheDocument();
    expect(screen.queryByText(/Retención/)).toBeNull();
  });
});
