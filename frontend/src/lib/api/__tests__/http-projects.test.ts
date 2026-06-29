import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { createHttpApi } from "@/lib/api/http";
import { setToken } from "@/lib/auth/token";

afterEach(() => vi.restoreAllMocks());
beforeEach(() => localStorage.clear());

describe("http projects", () => {
  it("listProjects GETs with the bearer token", async () => {
    setToken("tok-1");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify([
          { id: "p1", title: "x", videoCount: 0, createdAt: "", updatedAt: "" },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const api = createHttpApi("https://api.test");
    const list = await api.listProjects();

    expect(list).toHaveLength(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/api/v1/projects");
    expect(init.method).toBe("GET");
    expect(init.headers.Authorization).toBe("Bearer tok-1");
  });

  it("createProject POSTs the title", async () => {
    setToken("tok-1");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ id: "p2", title: "회의록", videoCount: 0, createdAt: "", updatedAt: "" }),
        { status: 201, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const api = createHttpApi("https://api.test");
    const created = await api.createProject({ title: "회의록" });

    expect(created.id).toBe("p2");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/api/v1/projects");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ title: "회의록" });
  });
});
