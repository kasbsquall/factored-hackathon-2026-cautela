import type { Metadata } from "next";
import { InsightsView } from "@/components/insights/insights-view";

export const metadata: Metadata = {
  title: "Insights",
  description: "Why unrecognized-charge disputes: complaint volume, SLA, resolution time, channels, demand, and the offline evaluation.",
};

export default function InsightsPage() {
  return <InsightsView />;
}
