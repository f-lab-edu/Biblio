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
});
