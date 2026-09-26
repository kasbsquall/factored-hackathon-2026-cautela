/**
 * Same-origin proxy to the Cautela API (live mode only).
 *
 * Why: the browser talks to one origin, and the console key never reaches the browser. The handler adds
 * X-Console-Key to /console/* from CAUTELA_CONSOLE_KEY (server env, never NEXT_PUBLIC_*), and only when
 * CAUTELA_CONSOLE_PROXY=enabled. With the flag on, anyone who can open this app can read the console: put the app
 * behind the console's own access control before enabling it outside a local demo.
 */
import { NextResponse, type NextRequest } from "next/server";

const UPSTREAM = (process.env.CAUTELA_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const ALLOWED = /^(auth\/(challenge|verify|logout)|demo\/(identities|outbox\/[\w-]+)|conversations\/(turn|[\w-]+(\/(confirm|recognize))?)|cases\/[\w-]+|console\/(handoffs(\/[\w-]+)?|traces\/[\w-]+|conversations\/[\w-]+\/audit)|health)$/;

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
  if (joined.startsWith("console/")) {
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
