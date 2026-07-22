import { describe, it, expect, vi, afterEach } from "vitest";
import { createHttpApi, HttpError } from "@/lib/api/http";

afterEach(() => vi.restoreAllMocks());

describe("http auth", () => {
  it("login POSTs with credentials and returns parsed body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ userId: "u", email: "a@b.com" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const api = createHttpApi("https://api.test");
    const res = await api.login({ email: "a@b.com", password: "pw" });

    expect(res.userId).toBe("u");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/api/v1/auth/login");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(init.body)).toEqual({ email: "a@b.com", password: "pw" });
  });

  it("sends csrf header for unsafe requests when the cookie exists", async () => {
    document.cookie = "biblio_csrf_token=csrf-1";
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "p2", title: "회의록", videoCount: 0, createdAt: "", updatedAt: "" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await createHttpApi("https://api.test").createProject({ title: "회의록" });

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-CSRF-Token"]).toBe("csrf-1");
    expect(init.credentials).toBe("include");
  });

  it("preserves a structured API error response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: "UNAUTHENTICATED",
            message: "Authentication credentials are invalid.",
            trace_id: "trace-body",
          }),
          {
            status: 401,
            headers: {
              "Content-Type": "application/json",
              "X-Trace-Id": "trace-header",
            },
          }
        )
      )
    );
    const api = createHttpApi("https://api.test");
    const error = await api.login({ email: "a@b.com", password: "x" }).catch((reason) => reason);

    expect(error).toBeInstanceOf(HttpError);
    expect(error).toMatchObject({
      status: 401,
      code: "UNAUTHENTICATED",
      message: "Authentication credentials are invalid.",
      traceId: "trace-body",
    });
  });

  it.each([
    ["an empty body", ""],
    ["a non-JSON body", "nope"],
    ["an invalid API error body", JSON.stringify({ code: "UNAUTHENTICATED" })],
  ])("falls back to the existing message for %s", async (_label, body) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(body, { status: 401, headers: { "X-Trace-Id": "trace-header" } })
      )
    );
    const api = createHttpApi("https://api.test");
    const error = await api.login({ email: "a@b.com", password: "x" }).catch((reason) => reason);

    expect(error).toBeInstanceOf(HttpError);
    expect(error).toMatchObject({
      status: 401,
      message: "요청 실패 (401)",
      traceId: "trace-header",
    });
    expect(error.code).toBeUndefined();
  });
});
