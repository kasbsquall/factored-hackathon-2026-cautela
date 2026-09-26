import { fireEvent, render, screen, within } from "@testing-library/react";
import { NarrationPanel } from "@/components/customer/narration-panel";
import type { TurnRecord } from "@/components/customer/flow-types";
import type { TurnResponse } from "@/lib/api/types";
import captured from "../fixtures/live-turns.json";

const normal = (captured as unknown as Record<string, TurnResponse[]>).normal!;
const records: TurnRecord[] = [
  { id: "t1", cause: { kind: "message" }, turn: normal[0]! },
  { id: "t2", cause: { kind: "recognize", recognized: false }, turn: normal[1]! },
  { id: "t3", cause: { kind: "confirm", accept: true }, turn: normal[2]! },
];

describe("NarrationPanel", () => {
  it("is labeled for reviewers and explains what to do before any turn", () => {
    render(<NarrationPanel turns={[]} pending={null} failed={false} active={false} />);
    expect(screen.getByRole("heading", { level: 2, name: "What the system did (for reviewers)" })).toBeInTheDocument();
    expect(screen.getByText(/Log in as a test customer and send a message/)).toBeInTheDocument();
  });

  it("renders one block per turn with its cause, stage, steps, rules and a link to the audit trace", () => {
    render(<NarrationPanel turns={records} pending={null} failed={false} active />);
    const turns = screen.getAllByRole("listitem").filter((li) => li.getAttribute("aria-labelledby"));
    expect(turns).toHaveLength(3);
    const first = within(turns[0]!);
    expect(first.getByRole("heading", { name: /^Turn 1\s*Customer sent a message$/ })).toBeInTheDocument();
    expect(first.getByText("MX-WINDOW-001")).toBeInTheDocument();
    expect(first.getByText("law")).toBeInTheDocument();
    expect(first.getByRole("link", { name: /^Audit trace\s*\(opens in a new tab\)$/ })).toHaveAttribute("href", `/audit/${normal[0]!.trace_id}`);
    expect(within(turns[2]!).getByText(/read back from the case store: status "open" matches/)).toBeInTheDocument();
    expect(screen.getByText("3 turns")).toBeInTheDocument();
  });

  it("shows a skeleton for the turn in flight, never an empty or zero result", () => {
    render(<NarrationPanel turns={records.slice(0, 1)} pending={{ kind: "option", index: 2 }} failed={false} active />);
    expect(screen.getByText("Turn 2")).toBeInTheDocument();
    expect(screen.getByText("Customer picked option 2")).toBeInTheDocument();
    expect(screen.getByText("Waiting for the service to return its trail")).toBeInTheDocument();
  });

  it("says when the last request failed without a trail, and collapses behind a button on narrow screens", () => {
    render(<NarrationPanel turns={records.slice(0, 1)} pending={null} failed active />);
    expect(screen.getByText(/failed before the service returned a turn/)).toBeInTheDocument();
    const disclose = screen.getByRole("button", { name: /Show the explanation/ });
    expect(disclose).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(disclose);
    expect(screen.getByRole("button", { name: /Hide the explanation/ })).toHaveAttribute("aria-expanded", "true");
  });
});
