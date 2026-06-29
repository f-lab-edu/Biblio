import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { createHttpApi } from "@/lib/api/http";
import { setToken } from "@/lib/auth/token";

afterEach(() => vi.restoreAllMocks());
beforeEach(() => localStorage.clear());

describe("http videos", () => {
  it("listVideos GETs with the project filter and maps fields", async () => {
    setToken("tok-1");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            video_id: "v1",
            title: "강의1",
            status: "READY",
            input_type: "EXTERNAL_URL",
            source_url: "https://x",
            created_at: "2026-01-01T00:00:00Z",
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const list = await createHttpApi("https://api.test").listVideos("proj-1");

    expect(list[0]).toMatchObject({
      id: "v1",
      title: "강의1",
      status: "READY",
      inputType: "EXTERNAL_URL",
    });
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/videos?project_id=proj-1");
  });

  it("uploadVideo (url) POSTs an external-url create", async () => {
    setToken("tok-1");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ video_id: "v2", status: "PENDING" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const v = await createHttpApi("https://api.test").uploadVideo("proj-1", {
      kind: "url",
      sourceUrl: "https://youtu.be/abc",
      title: "강의1",
    });

    expect(v.id).toBe("v2");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/projects/proj-1/videos");
    expect(JSON.parse(init.body)).toEqual({
      input_type: "EXTERNAL_URL",
      title: "강의1",
      category: "GENERAL",
      source_url: "https://youtu.be/abc",
    });
  });

  it("uploadVideo (file) creates, uploads to the signed url, then completes", async () => {
    setToken("tok-1");
    const calls: string[] = [];
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      calls.push(url);
      if (url.endsWith("/projects/proj-1/videos")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ video_id: "v3", status: "PENDING", signed_url: "https://gcs/upload" }),
            { status: 201, headers: { "Content-Type": "application/json" } }
          )
        );
      }
      if (url === "https://gcs/upload") {
        return Promise.resolve(new Response(null, { status: 200 }));
      }
      return Promise.resolve(
        new Response(JSON.stringify({ video_id: "v3", status: "UPLOADED" }), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        })
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["x"], "clip.mp4", { type: "video/mp4" });
    const v = await createHttpApi("https://api.test").uploadVideo("proj-1", {
      kind: "file",
      file,
      title: "강의2",
    });

    expect(v.id).toBe("v3");
    expect(calls[0]).toBe("https://api.test/projects/proj-1/videos");
    expect(calls[1]).toBe("https://gcs/upload");
    expect(calls[2]).toBe("https://api.test/videos/v3/complete");
  });
});
