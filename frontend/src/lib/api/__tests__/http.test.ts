import { describe, it, expect, vi, afterEach } from "vitest";
import { createHttpApi } from "@/lib/api/http";

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

  it("throws on non-2xx", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("nope", { status: 401 }))
    );
    const api = createHttpApi("https://api.test");
    await expect(
      api.login({ email: "a@b.com", password: "x" })
    ).rejects.toThrow();
  });
});
