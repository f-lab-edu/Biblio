import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { waitFor } from "@testing-library/react";
import { createHttpApi } from "@/lib/api/http";

afterEach(() => vi.restoreAllMocks());
beforeEach(() => localStorage.clear());

class MockXMLHttpRequest {
  upload: { onprogress?: (event: ProgressEvent) => void } = {};
  status = 200;
  open = vi.fn();
  setRequestHeader = vi.fn();
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  constructor(private readonly options: { completeOnSend: boolean } = { completeOnSend: true }) {}

  send = vi.fn(() => {
    if (!this.options.completeOnSend) return;
    this.upload.onprogress?.({
      lengthComputable: true,
      loaded: 1,
      total: 2,
    } as ProgressEvent);
    this.onload?.();
  });
}

describe("http videos", () => {
  it("listVideos GETs with the project filter and maps fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [
            {
              video_id: "v1",
              title: "강의1",
              status: "FAILED",
              failed_stage: "DOWNLOAD",
              input_type: "EXTERNAL_URL",
              source_url: "https://x",
              created_at: "2026-01-01T00:00:00Z",
            },
          ],
          next_cursor: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const list = await createHttpApi("https://api.test").listVideos("proj-1");

    expect(list[0]).toMatchObject({
      id: "v1",
      title: "강의1",
      status: "FAILED",
      failedStage: "DOWNLOAD",
      inputType: "EXTERNAL_URL",
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/api/v1/projects/proj-1/videos");
    expect(init.credentials).toBe("include");
  });

  it("uploadVideo (url) POSTs an external-url create", async () => {
    document.cookie = "biblio_csrf_token=csrf-1";
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
    expect(url).toBe("https://api.test/api/v1/projects/proj-1/videos");
    expect(init.credentials).toBe("include");
    expect(init.headers["X-CSRF-Token"]).toBe("csrf-1");
    expect(JSON.parse(init.body)).toEqual({
      input_type: "EXTERNAL_URL",
      title: "강의1",
      category: "GENERAL",
      source_url: "https://youtu.be/abc",
    });
  });

  it("uploadVideo (file) creates, uploads to the signed url, then completes", async () => {
    document.cookie = "biblio_csrf_token=csrf-1";
    const calls: string[] = [];
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      calls.push(url);
      if (url.endsWith("/api/v1/projects/proj-1/videos")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              video_id: "v3",
              status: "PENDING",
              signed_url: "https://gcs/upload",
              upload_headers: {
                "content-type": "application/octet-stream",
                "x-goog-content-length-range": "0,2147483648",
              },
            }),
            { status: 201, headers: { "Content-Type": "application/json" } }
          )
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ video_id: "v3", status: "UPLOADED" }), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        })
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const xhrInstances: MockXMLHttpRequest[] = [];
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(function () {
        const xhr = new MockXMLHttpRequest();
        xhrInstances.push(xhr);
        return xhr;
      })
    );

    const file = new File(["x"], "clip.MP4", { type: "video/mp4" });
    const onProgress = vi.fn();
    const v = await createHttpApi("https://api.test").uploadVideo(
      "proj-1",
      {
        kind: "file",
        file,
        title: "강의2",
      },
      { onProgress }
    );

    expect(v.id).toBe("v3");
    expect(onProgress.mock.calls.map(([percent]) => percent)).toEqual([50, 100]);
    expect(calls[0]).toBe("https://api.test/api/v1/projects/proj-1/videos");
    expect(calls[1]).toBe("https://api.test/api/v1/videos/v3/complete");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).extension).toBe(".mp4");
    expect(xhrInstances[0].open).toHaveBeenCalledWith("PUT", "https://gcs/upload");
    expect(xhrInstances[0].setRequestHeader).toHaveBeenCalledWith(
      "content-type",
      "application/octet-stream"
    );
    expect(xhrInstances[0].setRequestHeader).toHaveBeenCalledWith(
      "x-goog-content-length-range",
      "0,2147483648"
    );
  });

  it("warns before leaving while a signed-url file upload is active", async () => {
    let resolveComplete: (() => void) | undefined;
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith("/api/v1/projects/proj-1/videos")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              video_id: "v3",
              status: "PENDING",
              signed_url: "https://gcs/upload",
              upload_headers: {},
            }),
            { status: 201, headers: { "Content-Type": "application/json" } }
          )
        );
      }
      return new Promise<Response>((resolve) => {
        resolveComplete = () =>
          resolve(
            new Response(JSON.stringify({ video_id: "v3", status: "UPLOADED" }), {
              status: 202,
              headers: { "Content-Type": "application/json" },
            })
          );
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const xhrInstances: MockXMLHttpRequest[] = [];
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(function () {
        const xhr = new MockXMLHttpRequest({ completeOnSend: false });
        xhrInstances.push(xhr);
        return xhr;
      })
    );

    const addEventListener = vi.spyOn(window, "addEventListener");
    const removeEventListener = vi.spyOn(window, "removeEventListener");
    const file = new File(["x"], "clip.mp4", { type: "video/mp4" });
    const uploadPromise = createHttpApi("https://api.test").uploadVideo("proj-1", {
      kind: "file",
      file,
      title: "강의2",
    });

    await waitFor(() => expect(addEventListener).toHaveBeenCalledWith("beforeunload", expect.any(Function)));
    const handler = addEventListener.mock.calls.find(([event]) => event === "beforeunload")?.[1] as (
      event: BeforeUnloadEvent
    ) => void;
    const event = new Event("beforeunload", { cancelable: true }) as BeforeUnloadEvent;
    handler(event);

    expect(event.defaultPrevented).toBe(true);
    xhrInstances[0].status = 200;
    xhrInstances[0].onload?.();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "https://api.test/api/v1/videos/v3/complete",
        expect.any(Object)
      )
    );
    expect(removeEventListener).not.toHaveBeenCalledWith("beforeunload", handler);
    resolveComplete?.();
    await uploadPromise;
    expect(removeEventListener).toHaveBeenCalledWith("beforeunload", handler);
  });

  it("deleteVideos POSTs a batch delete request", async () => {
    document.cookie = "biblio_csrf_token=csrf-1";
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);

    await createHttpApi("https://api.test").deleteVideos(["v1", "v2"]);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/api/v1/videos:batch-delete");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(init.headers["X-CSRF-Token"]).toBe("csrf-1");
    expect(JSON.parse(init.body)).toEqual({ video_ids: ["v1", "v2"] });
  });
});
