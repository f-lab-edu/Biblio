import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const listProjects = vi.fn();
const createProject = vi.fn();
const renameProject = vi.fn();
const deleteProject = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    listProjects: () => listProjects(),
    createProject: (req: unknown) => createProject(req),
    renameProject: (projectId: string, title: string) => renameProject(projectId, title),
    deleteProject: (projectId: string) => deleteProject(projectId),
  },
}));

import { ProjectList } from "@/components/ProjectList";

describe("ProjectList", () => {
  beforeEach(() => {
    listProjects.mockReset();
    createProject.mockReset();
    renameProject.mockReset();
    deleteProject.mockReset();
  });

  it("renders existing projects", async () => {
    listProjects.mockResolvedValue([
      { id: "p1", title: "강의영상", videoCount: 3, createdAt: "", updatedAt: "" },
    ]);
    render(<ProjectList />);
    expect(await screen.findByText("강의영상")).toBeInTheDocument();
    expect(screen.getByText("영상 3개")).toBeInTheDocument();
  });

  it("creates a new project and shows it", async () => {
    listProjects.mockResolvedValue([]);
    createProject.mockResolvedValue({
      id: "p2",
      title: "회의록",
      videoCount: 0,
      createdAt: "",
      updatedAt: "",
    });
    render(<ProjectList />);

    await userEvent.click(await screen.findByRole("button", { name: "＋ 새 프로젝트" }));
    await userEvent.type(screen.getByLabelText("프로젝트 제목"), "회의록");
    await userEvent.click(screen.getByRole("button", { name: "만들기" }));

    expect(createProject).toHaveBeenCalledWith({ title: "회의록" });
    expect(await screen.findByText("회의록")).toBeInTheDocument();
  });

  it("renames a project inline and shows the new title", async () => {
    listProjects.mockResolvedValue([
      { id: "p1", title: "예전 제목", videoCount: 3, createdAt: "", updatedAt: "" },
    ]);
    renameProject.mockResolvedValue({
      id: "p1",
      title: "새 제목",
      videoCount: 3,
      createdAt: "",
      updatedAt: "",
    });
    render(<ProjectList />);

    await userEvent.click(await screen.findByRole("button", { name: "이름 수정" }));
    const input = screen.getByLabelText("프로젝트 이름");
    await userEvent.clear(input);
    await userEvent.type(input, "새 제목");
    await userEvent.click(screen.getByRole("button", { name: "저장" }));

    expect(renameProject).toHaveBeenCalledWith("p1", "새 제목");
    expect(await screen.findByText("새 제목")).toBeInTheDocument();
    expect(screen.queryByText("예전 제목")).not.toBeInTheDocument();
  });

  it("confirms project cascade deletion and removes it from the list", async () => {
    listProjects.mockResolvedValue([
      { id: "p1", title: "삭제할 프로젝트", videoCount: 2, createdAt: "", updatedAt: "" },
    ]);
    deleteProject.mockResolvedValue(undefined);
    render(<ProjectList />);

    await userEvent.click(await screen.findByRole("button", { name: "삭제" }));
    expect(screen.getByText("영상과 검색 기록이 모두 삭제됩니다.")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "삭제" }).at(-1)!);

    expect(deleteProject).toHaveBeenCalledWith("p1");
    expect(screen.queryByText("삭제할 프로젝트")).not.toBeInTheDocument();
  });
});
