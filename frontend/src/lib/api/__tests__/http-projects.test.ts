import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { createHttpApi } from "@/lib/api/http";

afterEach(() => vi.restoreAllMocks());
beforeEach(() => localStorage.clear());

describe("http projects", () => {
  it("listProjects GETs with cookies included", async () => {
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
    expect(init.credentials).toBe("include");
  });

  it("createProject POSTs the title", async () => {
    document.cookie = "biblio_csrf_token=csrf-1";
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
    expect(init.credentials).toBe("include");
    expect(init.headers["X-CSRF-Token"]).toBe("csrf-1");
    expect(JSON.parse(init.body)).toEqual({ title: "회의록" });
  });
});
