import type { Metadata } from "next";
import { CasePane } from "@/components/console/console-view";

export const metadata: Metadata = { title: "Ready file · Agent console" };

export default async function HandoffPage({ params }: { params: Promise<{ handoffId: string }> }) {
  const { handoffId } = await params;
  return <CasePane id={decodeURIComponent(handoffId)} />;
}
