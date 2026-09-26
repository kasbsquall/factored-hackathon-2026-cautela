export function sleep(ms: number): Promise<void> {
  return ms <= 0 ? Promise.resolve() : new Promise((resolve) => setTimeout(resolve, ms));
}

export function randomLatency([min, max]: [number, number]): number {
  return Math.round((min + Math.random() * (max - min)) * 10) / 10;
}

export function hex(length: number): string {
  const bytes = new Uint8Array(Math.ceil(length / 2));
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("").slice(0, length);
}

const HUMAN = /\b(persona|humano|asesor|agente|pessoa|atendente|humana)\b/i;
const OUT_OF_SCOPE = /\b(pr[eé]stamo|empr[eé]stimo|transferir|transfer[eê]ncia|aumento de cupo|limite|invers[ií]on|investimento)\b/i;

/**
 * Stand-in for the LLM understand step in mock mode. It only routes the two cases the flow must handle
 * differently; any other message is treated as an unrecognized charge and uses the customer's fixture hints.
 */
export function intentOf(text: string): string {
  if (HUMAN.test(text)) return "customer_requested_human";
  if (OUT_OF_SCOPE.test(text)) return "out_of_scope";
  return "unrecognized_charge";
}
