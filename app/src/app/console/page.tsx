import type { Metadata } from "next";
import { FirstCase } from "@/components/console/console-view";

export const metadata: Metadata = { title: "Agent console" };

export default function ConsolePage() {
  return <FirstCase />;
}
