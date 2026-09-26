import type { Metadata } from "next";
import { TraceView } from "@/components/audit/trace-view";

export const metadata: Metadata = { title: "Trace" };

export default async function TracePage({ params }: { params: Promise<{ traceId: string }> }) {
  const { traceId } = await params;
  return <TraceView traceId={decodeURIComponent(traceId)} />;
}
