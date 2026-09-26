import type { Metadata } from "next";
import { TraceList } from "@/components/audit/trace-list";

export const metadata: Metadata = { title: "Audit trail" };

export default function AuditPage() {
  return <TraceList />;
}
