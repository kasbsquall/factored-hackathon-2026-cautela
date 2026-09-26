"use client";

import { createContext, useContext } from "react";
import { usePathname } from "next/navigation";
import { MagnifyingGlass, WarningOctagon } from "@phosphor-icons/react";
import type { Handoff } from "@/lib/api/types";
import { ApiError, getApi } from "@/lib/api";
import { useResource, type Resource } from "@/lib/use-resource";
import { Notice } from "@/components/ui/notice";
import { Button, ButtonLink } from "@/components/ui/button";
import { OpsHeader } from "./ops-header";
import { HandoffQueue } from "./handoff-queue";
import { ReadyFile } from "./ready-file";
import styles from "./console-view.module.css";

const MOCK = process.env.NEXT_PUBLIC_API_MODE !== "live";

function CaseSkeleton() {
  return (
    <div className={styles.skeleton} aria-busy="true" aria-label="Loading the ready file">
      <span className="skeleton" style={{ height: 12, width: 180 }} />
      <span className="skeleton" style={{ height: 32, width: "55%" }} />
      <span className="skeleton" style={{ height: 24, width: "70%" }} />
      <div className={styles.skGrid}>
        <span className="skeleton" style={{ height: 160 }} />
        <span className="skeleton" style={{ height: 160 }} />
      </div>
    </div>
  );
}

export function CasePane({ id }: { id: string }) {
  const handoff = useResource(`handoff:${id}`, () => getApi().getHandoff(id));
  if (handoff.status === "loading") return <CaseSkeleton />;
  if (handoff.status === "error") {
    const missing = handoff.error instanceof ApiError && handoff.error.code === "not_found";
    return (
      <div className={styles.pad}>
        <Notice
          tone={missing ? "empty" : "error"}
          icon={missing ? <MagnifyingGlass aria-hidden /> : <WarningOctagon aria-hidden />}
          title={missing ? "This handoff does not exist" : "The ready file could not be loaded"}
          actions={missing ? <ButtonLink href="/console" size="sm">Back to the queue</ButtonLink> : <Button variant="secondary" size="sm" onClick={handoff.reload}>Retry</Button>}
        >
          {missing ? <>No handoff with id <span className="mono">{id}</span>.{MOCK ? " It may belong to a mock session that was reloaded." : null}</> : "Nothing was changed. Retry in a moment."}
        </Notice>
      </div>
    );
  }
  return <ReadyFile key={id} handoff={handoff.data} />;
}

const QueueContext = createContext<(Resource<Handoff[]> & { reload: () => void }) | null>(null);

/** Persists across /console and /console/[id], so the queue does not reload on every selection. */
export function ConsoleShell({ children }: { children: React.ReactNode }) {
  const queue = useResource("queue", () => getApi().listHandoffs());
  const path = usePathname();
  const routeId = path.startsWith("/console/") ? decodeURIComponent(path.slice("/console/".length)) : null;
  // /console shows the newest case beside the queue on wide screens only, so that row is a preview, not the current page.
  const preview = routeId ? null : queue.status === "ready" ? queue.data[0]?.handoff_id ?? null : null;
  return (
    <QueueContext.Provider value={queue}>
      <div className={styles.shell}>
        <OpsHeader />
        <div className={`${styles.layout} ${routeId ? styles.detail : styles.index}`}>
          <div className={styles.queue}>
            <HandoffQueue resource={queue} selectedId={routeId} previewId={preview} />
          </div>
          <main className={styles.case} id="main">{children}</main>
        </div>
      </div>
    </QueueContext.Provider>
  );
}

/** /console on a wide screen: open the newest handoff. */
export function FirstCase() {
  const queue = useContext(QueueContext);
  if (!queue || queue.status === "loading") return <CaseSkeleton />;
  if (queue.status === "error") return null;
  const first = queue.data[0];
  return first ? <CasePane id={first.handoff_id} /> : null;
}
