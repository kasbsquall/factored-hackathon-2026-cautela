/**
 * Same-origin proxy to the Cautela API (live mode only).
 *
 * Why: the browser talks to one origin, and the console key never reaches the browser. The handler adds
 * X-Console-Key to /console/* from CAUTELA_CONSOLE_KEY (server env, never NEXT_PUBLIC_*), and only when
 * CAUTELA_CONSOLE_PROXY=enabled.
 *
 * Demo decision (SECURITY.md, "Human-agent console"): on the public demo the flag is on, so anyone who opens the app
 * can read the console and the audit trail of the synthetic conversations. The proxy bounds that: it forwards
 * console GETs only (every console route is a read), and the API rate limits console reads per visitor. In
 * production the console sits behind the bank's SSO and this flag stays off.
 *
 * Visitor address: with CAUTELA_PROXY_KEY set, every request carries X-Cautela-Proxy-Key and, on Vercel, the
 * visitor address in X-Cautela-Client. Vercel sets x-real-ip itself and overwrites what a client sends, so the value
 * is the platform's. The API trusts X-Cautela-Client only with the key; without it every visitor shares the
 * proxy's address. Headers from the browser are never passed through, so no caller can set either header.
 */
import { NextResponse, type NextRequest } from "next/server";

const UPSTREAM = (process.env.CAUTELA_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const ALLOWED = /^(auth\/(challenge|verify|logout)|demo\/(identities|outbox\/[\w-]+)|conversations\/(turn|(?!turn(\/|$))[\w-]+(\/(confirm|recognize|translate))?)|cases\/[\w-]+|console\/(handoffs(\/[\w-]+)?|traces\/[\w-]+|conversations(\/[\w-]+\/audit)?)|health)$/;

/** The visitor address as the hosting platform reports it; null where no trusted platform header exists. */
function visitorAddress(request: NextRequest): string | null {
  if (!process.env.VERCEL) return null;
  const ip = request.headers.get("x-real-ip") ?? request.headers.get("x-forwarded-for")?.split(",")[0];
  return ip?.trim() || null;
}

function refuse(status: number, code: string, message: string) {
  return NextResponse.json({ error: { code, message, trace_id: null, fields: [] } }, { status, headers: { "Cache-Control": "no-store" } });
}

async function forward(request: NextRequest, path: string[]) {
  if (process.env.NEXT_PUBLIC_API_MODE !== "live") return refuse(404, "not_found", "Not found.");
  const joined = path.join("/");
  if (!ALLOWED.test(joined)) return refuse(404, "not_found", "Not found.");

  const headers = new Headers({ Accept: "application/json" });
  const auth = request.headers.get("authorization");
  if (auth) headers.set("Authorization", auth);
  if (request.method === "POST") headers.set("Content-Type", "application/json");
  const proxyKey = process.env.CAUTELA_PROXY_KEY;
  const visitor = visitorAddress(request);
  if (proxyKey && visitor) {
    headers.set("X-Cautela-Proxy-Key", proxyKey);
    headers.set("X-Cautela-Client", visitor);
  }
  if (joined.startsWith("console/")) {
    if (request.method !== "GET") return refuse(405, "method_not_allowed", "The console is read-only.");
    const key = process.env.CAUTELA_CONSOLE_KEY;
    if (process.env.CAUTELA_CONSOLE_PROXY !== "enabled" || !key) {
      return refuse(403, "console_forbidden", "Console access is not enabled on this deployment.");
    }
    headers.set("X-Console-Key", key);
  }

  try {
    const upstream = await fetch(`${UPSTREAM}/${joined}`, {
      method: request.method,
      headers,
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
    });
    const body = await upstream.text();
    return new NextResponse(upstream.status === 204 ? null : body, {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return refuse(502, "upstream_unavailable", "The Cautela API could not be reached.");
  }
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: Context) {
  return forward(request, (await context.params).path);
}

export async function POST(request: NextRequest, context: Context) {
  return forward(request, (await context.params).path);
}
