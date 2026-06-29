import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const listProjects = vi.fn();
const createProject = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    listProjects: () => listProjects(),
    createProject: (req: unknown) => createProject(req),
  },
}));

import { ProjectList } from "@/components/ProjectList";

describe("ProjectList", () => {
  beforeEach(() => {
    listProjects.mockReset();
    createProject.mockReset();
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
});
