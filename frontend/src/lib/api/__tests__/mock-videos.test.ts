import { describe, it, expect, beforeEach } from "vitest";
import { createMockApi } from "@/lib/api/mock";

const PROJECT = "proj-1";

describe("mock videos", () => {
  beforeEach(() => localStorage.clear());

  it("starts empty", async () => {
    expect(await createMockApi().listVideos(PROJECT)).toEqual([]);
  });

  it("uploads a url video as processing", async () => {
    const api = createMockApi();
    const v = await api.uploadVideo(PROJECT, {
      kind: "url",
      sourceUrl: "https://youtu.be/abc",
      title: "강의1",
    });
    expect(v.title).toBe("강의1");
    expect(v.inputType).toBe("EXTERNAL_URL");
    expect(v.status).toBe("PROCESSING");

    const list = await api.listVideos(PROJECT);
    expect(list).toHaveLength(1);
  });

  it("uploads a file video as processing", async () => {
    const api = createMockApi();
    const file = new File(["x"], "clip.mp4", { type: "video/mp4" });
    const v = await api.uploadVideo(PROJECT, { kind: "file", file, title: "강의2" });
    expect(v.inputType).toBe("LOCAL_FILE");
    expect(v.status).toBe("PROCESSING");
  });

  it("turns processing into ready after the processing window", async () => {
    const api = createMockApi();
    await api.uploadVideo(PROJECT, {
      kind: "url",
      sourceUrl: "https://youtu.be/abc",
      title: "강의1",
    });
    const raw = JSON.parse(localStorage.getItem("biblio.mock.videos")!);
    raw[0].createdAt = new Date(Date.now() - 60_000).toISOString();
    localStorage.setItem("biblio.mock.videos", JSON.stringify(raw));

    const list = await api.listVideos(PROJECT);
    expect(list[0].status).toBe("READY");
  });

  it("scopes videos by project", async () => {
    const api = createMockApi();
    await api.uploadVideo(PROJECT, { kind: "url", sourceUrl: "https://x", title: "a" });
    expect(await api.listVideos("other")).toEqual([]);
  });

  it("deletes selected videos", async () => {
    const api = createMockApi();
    const first = await api.uploadVideo(PROJECT, { kind: "url", sourceUrl: "https://x", title: "a" });
    await api.uploadVideo(PROJECT, { kind: "url", sourceUrl: "https://y", title: "b" });

    await api.deleteVideos([first.id]);

    expect(await api.listVideos(PROJECT)).toHaveLength(1);
  });
});
