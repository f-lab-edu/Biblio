import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const getGoogleIdToken = vi.fn();
vi.mock("@/lib/server/gcp-id-token", () => ({
  getGoogleIdToken: (audience: string) => getGoogleIdToken(audience),
}));

import * as route from "../route";

type Method = "GET" | "POST" | "PATCH" | "DELETE";

function context(path: string[]) {
  return { params: Promise.resolve({ path }) };
}

function request(url: string, init: RequestInit = {}) {
  const req = new Request(url, init) as Request & {
    nextUrl: { pathname: string; search: string };
  };
  const parsed = new URL(url);
  req.nextUrl = { pathname: parsed.pathname, search: parsed.search };
  return req;
}

function backendResponse(body: string, status = 200, headers?: HeadersInit) {
  return new Response(body, { status, headers });
}

async function call(method: Method, url: string, init: RequestInit = {}) {
  return route[method](request(url, { ...init, method }), context(url.split("/api/v1/")[1].split("?")[0].split("/")));
}

describe("Next API proxy route", () => {
  beforeEach(() => {
    vi.stubEnv("CORE_API_URL", "https://core.example");
    vi.stubEnv("SEARCH_SERVICE_URL", "https://search.example");
    vi.stubEnv("PROXY_USE_IAM_AUTH", "false");
    getGoogleIdToken.mockReset();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(backendResponse("ok")));
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("routes search paths to search-service and reattaches /api/v1", async () => {
    await call("POST", "https://front.test/api/v1/search", {
      body: JSON.stringify({ query: "q" }),
      headers: { "Content-Type": "application/json" },
    });

    expect(fetch).toHaveBeenCalledTimes(1);
    const forwarded = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(forwarded.url).toBe("https://search.example/api/v1/search");
    expect(forwarded.method).toBe("POST");
  });

  it("routes non-search paths to core-api and preserves colon paths", async () => {
    await call("POST", "https://front.test/api/v1/videos:batch-delete", {
      body: JSON.stringify({ video_ids: ["v1"] }),
      headers: { "Content-Type": "application/json" },
    });

    const forwarded = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(forwarded.url).toBe("https://core.example/api/v1/videos:batch-delete");
  });

  it("preserves query strings for search history", async () => {
    await call("GET", "https://front.test/api/v1/search/history?project_id=p%201");

    const forwarded = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(forwarded.url).toBe("https://search.example/api/v1/search/history?project_id=p%201");
  });

  it("forwards cookies csrf content-type authorization and body", async () => {
    await call("PATCH", "https://front.test/api/v1/projects/p1", {
      body: JSON.stringify({ title: "새 제목" }),
      headers: {
        Authorization: "Bearer app-jwt",
        Cookie: "biblio_access_token=auth",
        "Content-Type": "application/json",
        "X-CSRF-Token": "csrf",
      },
    });

    const forwarded = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(forwarded.headers.get("authorization")).toBe("Bearer app-jwt");
    expect(forwarded.headers.get("cookie")).toBe("biblio_access_token=auth");
    expect(forwarded.headers.get("content-type")).toBe("application/json");
    expect(forwarded.headers.get("x-csrf-token")).toBe("csrf");
    expect(await forwarded.text()).toBe(JSON.stringify({ title: "새 제목" }));
  });

  it("returns backend status body and multiple set-cookie headers unchanged", async () => {
    const headers = new Headers({ "Content-Type": "application/json" });
    headers.append("Set-Cookie", "biblio_access_token=a; Path=/; HttpOnly");
    headers.append("Set-Cookie", "biblio_csrf_token=b; Path=/");
    vi.mocked(fetch).mockResolvedValueOnce(backendResponse("{\"ok\":true}", 201, headers));

    const response = await call("POST", "https://front.test/api/v1/auth/login", {
      body: JSON.stringify({ email: "a@b.com", password: "pw" }),
      headers: { "Content-Type": "application/json" },
    });

    expect(response.status).toBe(201);
    expect(await response.text()).toBe("{\"ok\":true}");
    expect(response.headers.getSetCookie()).toEqual([
      "biblio_access_token=a; Path=/; HttpOnly",
      "biblio_csrf_token=b; Path=/",
    ]);
  });

  it("passes backend 404 and 500 responses through", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(backendResponse("missing", 404));
    const notFound = await call("POST", "https://front.test/api/v1/feedbacks", { body: "{}" });
    expect(notFound.status).toBe(404);
    expect(await notFound.text()).toBe("missing");

    vi.mocked(fetch).mockResolvedValueOnce(backendResponse("broken", 500));
    const broken = await call("GET", "https://front.test/api/v1/projects");
    expect(broken.status).toBe(500);
    expect(await broken.text()).toBe("broken");
  });

  it("skips ID token when PROXY_USE_IAM_AUTH is false", async () => {
    await call("GET", "https://front.test/api/v1/projects");

    const forwarded = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(getGoogleIdToken).not.toHaveBeenCalled();
    expect(forwarded.headers.has("x-serverless-authorization")).toBe(false);
  });

  it("adds X-Serverless-Authorization for IAM without changing Authorization", async () => {
    vi.stubEnv("PROXY_USE_IAM_AUTH", "true");
    getGoogleIdToken.mockResolvedValue("id-token");

    await call("GET", "https://front.test/api/v1/projects", {
      headers: { Authorization: "Bearer app-jwt" },
    });

    const forwarded = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(getGoogleIdToken).toHaveBeenCalledWith("https://core.example");
    expect(forwarded.headers.get("authorization")).toBe("Bearer app-jwt");
    expect(forwarded.headers.get("x-serverless-authorization")).toBe("Bearer id-token");
  });

  it("keeps internal paths on core-api and does not route them to search-service", async () => {
    await call("POST", "https://front.test/api/v1/internal/reload-serving-targets", { body: "{}" });

    const forwarded = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(forwarded.url).toBe("https://core.example/api/v1/internal/reload-serving-targets");
  });

  it("returns 404 outside /api/v1 and does not export unsupported methods", async () => {
    const response = await route.GET(request("https://front.test/api/projects"), context(["projects"]));

    expect(response.status).toBe(404);
    expect("PUT" in route).toBe(false);
  });
});
