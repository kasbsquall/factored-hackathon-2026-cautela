import type { Metadata } from "next";
import { InsightsView } from "@/components/insights/insights-view";

export const metadata: Metadata = {
  title: "Insights",
  description: "Why unrecognized-charge disputes: a record settles every outcome. Workflow ranking by agent-hours, savings range, cost per resolution, thresholds, demand and the offline evaluation.",
};

export default function InsightsPage() {
  return <InsightsView />;
}
