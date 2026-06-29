import { describe, it, expect, vi, afterEach } from "vitest";
import { createHttpApi } from "@/lib/api/http";

afterEach(() => vi.restoreAllMocks());

describe("http auth", () => {
  it("login POSTs to the auth endpoint and returns parsed body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ token: "t", userId: "u" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const api = createHttpApi("https://api.test");
    const res = await api.login({ email: "a@b.com", password: "pw" });

    expect(res.token).toBe("t");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/api/v1/auth/login");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ email: "a@b.com", password: "pw" });
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
