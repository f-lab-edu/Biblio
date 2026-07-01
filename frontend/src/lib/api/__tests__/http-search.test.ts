import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { createHttpApi } from "@/lib/api/http";

afterEach(() => vi.restoreAllMocks());
beforeEach(() => localStorage.clear());

describe("http search", () => {
  it("search POSTs query+project_id and maps chunks", async () => {
    document.cookie = "biblio_csrf_token=csrf-1";
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          req_id: "r1",
          answer: "답변",
          chunks: [
            {
              ref: 1,
              chunk_id: "c1",
              video_id: "v1",
              title: "강의1",
              start_ms: 1000,
              end_ms: 2000,
              text: "조각",
              used: true,
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await createHttpApi("https://api.test").search("proj-1", "임베딩");

    expect(res.reqId).toBe("r1");
    expect(res.chunks[0]).toMatchObject({ chunkId: "c1", videoId: "v1", startMs: 1000 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/api/v1/search");
    expect(init.credentials).toBe("include");
    expect(init.headers["X-CSRF-Token"]).toBe("csrf-1");
    expect(JSON.parse(init.body)).toEqual({ query: "임베딩", project_id: "proj-1" });
  });

  it("getPlaybackUrl POSTs and returns the signed url", async () => {
    document.cookie = "biblio_csrf_token=csrf-1";
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ signed_url: "https://gcs/play", expires_at: "" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const url = await createHttpApi("https://api.test").getPlaybackUrl("v1");

    expect(url).toBe("https://gcs/play");
    const [reqUrl, init] = fetchMock.mock.calls[0];
    expect(reqUrl).toBe("https://api.test/api/v1/videos/v1/playback-url");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(init.headers["X-CSRF-Token"]).toBe("csrf-1");
  });
});
