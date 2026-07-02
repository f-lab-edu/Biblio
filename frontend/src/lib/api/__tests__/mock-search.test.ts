import { describe, it, expect, beforeEach } from "vitest";
import { createMockApi } from "@/lib/api/mock";

const PROJECT = "proj-1";

describe("mock search", () => {
  beforeEach(() => localStorage.clear());

  it("returns no results when the project has no videos", async () => {
    const res = await createMockApi().search(PROJECT, "임베딩");
    expect(res.chunks).toEqual([]);
    expect(res.answer).toContain("검색 결과가 없습니다");
  });

  it("returns chunks pointing at the project's videos", async () => {
    const api = createMockApi();
    const v = await api.uploadVideo(PROJECT, { kind: "url", sourceUrl: "https://x", title: "강의1" });
    const res = await api.search(PROJECT, "임베딩");
    expect(res.chunks.length).toBeGreaterThan(0);
    expect(res.chunks[0].videoId).toBe(v.id);
    expect(res.chunks[0].ref).toBe(1);
  });

  it("stores mock search history for the project", async () => {
    const api = createMockApi();
    await api.uploadVideo(PROJECT, { kind: "url", sourceUrl: "https://x", title: "강의1" });
    await api.search(PROJECT, "임베딩");

    const history = await api.getSearchHistory(PROJECT);

    expect(history).toHaveLength(1);
    expect(history[0].query).toBe("임베딩");
    expect(history[0].result.answer).toContain("임베딩");
  });

  it("returns a playback url", async () => {
    const url = await createMockApi().getPlaybackUrl("v1");
    expect(typeof url).toBe("string");
    expect(url.length).toBeGreaterThan(0);
  });

  it("accepts feedback without a network request", async () => {
    await expect(createMockApi().submitFeedback("req-1", "LIKE")).resolves.toBeUndefined();
  });
});
