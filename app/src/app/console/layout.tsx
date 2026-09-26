import { ConsoleShell } from "@/components/console/console-view";

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return <ConsoleShell>{children}</ConsoleShell>;
}
