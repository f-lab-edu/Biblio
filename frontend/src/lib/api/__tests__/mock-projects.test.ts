import { describe, it, expect, beforeEach } from "vitest";
import { createMockApi } from "@/lib/api/mock";

describe("mock projects", () => {
  beforeEach(() => localStorage.clear());

  it("starts with no projects", async () => {
    const api = createMockApi();
    expect(await api.listProjects()).toEqual([]);
  });

  it("creates a project and lists it", async () => {
    const api = createMockApi();
    const created = await api.createProject({ title: "강의영상" });
    expect(created.id).toBeTruthy();
    expect(created.title).toBe("강의영상");
    expect(created.videoCount).toBe(0);

    const list = await api.listProjects();
    expect(list).toHaveLength(1);
    expect(list[0].title).toBe("강의영상");
  });

  it("persists projects across api instances (localStorage)", async () => {
    await createMockApi().createProject({ title: "회의록" });
    const list = await createMockApi().listProjects();
    expect(list.map((p) => p.title)).toContain("회의록");
  });

  it("renames a project and keeps its video count", async () => {
    const api = createMockApi();
    const created = await api.createProject({ title: "예전 제목" });
    await api.uploadVideo(created.id, {
      kind: "url",
      sourceUrl: "https://example.com/video",
      title: "강의",
    });

    const updated = await api.renameProject(created.id, "새 제목");

    expect(updated.title).toBe("새 제목");
    expect(updated.videoCount).toBe(1);
    const [project] = await api.listProjects();
    expect(project.title).toBe("새 제목");
    expect(project.videoCount).toBe(1);
  });

  it("deletes an empty project", async () => {
    const api = createMockApi();
    const created = await api.createProject({ title: "빈 프로젝트" });

    await api.deleteProject(created.id);

    expect(await api.listProjects()).toEqual([]);
  });

  it("deletes a project and its videos", async () => {
    const api = createMockApi();
    const created = await api.createProject({ title: "영상 프로젝트" });
    await api.uploadVideo(created.id, {
      kind: "url",
      sourceUrl: "https://example.com/video",
      title: "강의",
    });

    await api.deleteProject(created.id);

    expect(await api.listProjects()).toEqual([]);
    expect(await api.listVideos(created.id)).toEqual([]);
  });

  it("updates videoCount when a video is uploaded", async () => {
    const api = createMockApi();
    const created = await api.createProject({ title: "카운트 프로젝트" });

    await api.uploadVideo(created.id, {
      kind: "url",
      sourceUrl: "https://example.com/video",
      title: "강의",
    });

    const [project] = await api.listProjects();
    expect(project.videoCount).toBe(1);
  });
});
