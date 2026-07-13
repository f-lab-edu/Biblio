import type { NextRequest } from "next/server";
import { getGoogleIdToken } from "@/lib/server/gcp-id-token";

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

const API_PREFIX = "/api/v1/";

function trimTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

function selectBackendBase(firstSegment: string): string | undefined {
  return firstSegment === "search" ? process.env.SEARCH_SERVICE_URL : process.env.CORE_API_URL;
}

function buildTargetUrl(request: NextRequest, firstSegment: string): string | null {
  const pathname = request.nextUrl.pathname;
  if (!pathname.startsWith(API_PREFIX)) return null;

  const base = selectBackendBase(firstSegment);
  if (!base) return null;

  const remainingPath = pathname.slice(API_PREFIX.length);
  return `${trimTrailingSlash(base)}${API_PREFIX}${remainingPath}${request.nextUrl.search}`;
}

function shouldUseIamAuth(): boolean {
  return process.env.PROXY_USE_IAM_AUTH !== "false";
}

async function buildProxyRequest(
  request: NextRequest,
  targetUrl: string,
  targetBase: string
): Promise<Request> {
  const headers = new Headers(request.headers);
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
  };
  if (request.body) {
    init.body = request.body;
    init.duplex = "half";
  }

  const proxyRequest = new Request(targetUrl, init);
  if (shouldUseIamAuth()) {
    const idToken = await getGoogleIdToken(new URL(targetBase).origin);
    if (idToken) {
      proxyRequest.headers.set("X-Serverless-Authorization", `Bearer ${idToken}`);
    }
  }
  return proxyRequest;
}

async function proxy(request: NextRequest, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  const firstSegment = path[0] ?? "";
  const targetBase = selectBackendBase(firstSegment);
  const targetUrl = buildTargetUrl(request, firstSegment);
  if (!targetBase || !targetUrl) {
    return new Response(null, { status: 404 });
  }

  const proxyRequest = await buildProxyRequest(request, targetUrl, targetBase);
  return fetch(proxyRequest);
}

export function GET(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export function POST(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export function PATCH(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export function DELETE(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}
