import { OpsHeader } from "@/components/console/ops-header";

export default function InsightsLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <OpsHeader />
      <main id="main">{children}</main>
    </>
  );
}
