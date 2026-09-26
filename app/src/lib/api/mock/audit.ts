/**
 * In-memory audit log for mock mode, shaped like agent/security/audit.py.
 *
 * Differences from the backend, on purpose and visible in the UI as synthetic: the chain is per trace (the backend
 * chains every record of the process), and args_hash is a plain SHA-256 of the canonical arguments (the backend
 * uses a keyed hash). Record hashes are real SHA-256 over canonical JSON, so the tamper fixture really breaks.
 */
import type { AuditRecord, TraceView } from "../types";

export const GENESIS_HASH = "0".repeat(64);

type RecordBody = Omit<AuditRecord, "seq" | "prev_hash" | "record_hash" | "args_hash"> & { args: Record<string, unknown> | null };

function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, v]) => v !== undefined)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(",")}}`;
}

async function sha256(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export class MockAuditLog {
  private readonly bodies = new Map<string, RecordBody[]>();
  private readonly tampered = new Map<string, { seq: number; patch: Partial<AuditRecord> }>();

  record(body: RecordBody): void {
    const list = this.bodies.get(body.trace_id) ?? [];
    this.bodies.set(body.trace_id, [...list, body]);
  }

  /** Edits one record after it was hashed, the way an attacker editing the log file would. */
  tamper(traceId: string, seq: number, patch: Partial<AuditRecord>): void {
    this.tampered.set(traceId, { seq, patch });
  }

  has(traceId: string): boolean {
    return this.bodies.has(traceId);
  }

  traceIds(): string[] {
    return [...this.bodies.keys()];
  }

  async trace(traceId: string, checkedAt: string): Promise<TraceView | null> {
    const bodies = this.bodies.get(traceId);
    if (!bodies) return null;
    const records: AuditRecord[] = [];
    let prev = GENESIS_HASH;
    for (const [seq, { args, ...rest }] of bodies.entries()) {
      const argsHash = args === null ? null : await sha256(canonical(args));
      const unhashed = { ...rest, seq, args_hash: argsHash, prev_hash: prev };
      const recordHash = await sha256(canonical(unhashed));
      records.push({ ...unhashed, record_hash: recordHash });
      prev = recordHash;
    }
    const tamper = this.tampered.get(traceId);
    const final = tamper
      ? records.map((r) => (r.seq === tamper.seq ? { ...r, ...tamper.patch } : r))
      : records;
    const firstBad = await firstBrokenSeq(final);
    return {
      trace_id: traceId,
      records: final,
      chain: { status: firstBad === null ? "intact" : "broken", checked_at: checkedAt, first_bad_seq: firstBad },
    };
  }
}

/** Same check as AuditLog.verify_chain, but it reports where the chain breaks. */
export async function firstBrokenSeq(records: AuditRecord[]): Promise<number | null> {
  let prev = GENESIS_HASH;
  for (const record of records) {
    const { record_hash: stored, ...body } = record;
    if (record.prev_hash !== prev || (await sha256(canonical(body))) !== stored) return record.seq;
    prev = stored;
  }
  return null;
}
