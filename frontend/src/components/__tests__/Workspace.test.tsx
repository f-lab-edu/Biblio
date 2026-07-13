import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const replace = vi.fn();
const currentUser = vi.fn();
const deleteProject = vi.fn();
const getSearchHistory = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api", () => ({
  api: {
    currentUser: (...a: unknown[]) => currentUser(...a),
    listVideos: () => Promise.resolve([]),
    deleteProject: (projectId: string) => deleteProject(projectId),
    getSearchHistory: (projectId: string) => getSearchHistory(projectId),
  },
}));

import { Workspace } from "@/components/Workspace";
import { AuthProvider } from "@/lib/auth/AuthContext";

function renderWorkspace() {
  return render(
    <AuthProvider>
      <Workspace projectId="p1" />
    </AuthProvider>
  );
}

describe("Workspace", () => {
  beforeEach(() => {
    replace.mockReset();
    currentUser.mockReset();
    deleteProject.mockReset();
    getSearchHistory.mockReset();
    getSearchHistory.mockResolvedValue([]);
    localStorage.clear();
  });

  it("redirects to /login when signed out", async () => {
    currentUser.mockRejectedValue(new Error("unauthenticated"));
    renderWorkspace();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  });

  it("shows the video panel and search placeholder when signed in", async () => {
    currentUser.mockResolvedValue({ userId: "u1" });
    renderWorkspace();
    expect(await screen.findByRole("heading", { name: "영상" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "검색" })).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("confirms project deletion and returns to the project list", async () => {
    currentUser.mockResolvedValue({ userId: "u1" });
    deleteProject.mockResolvedValue(undefined);
    renderWorkspace();

    await userEvent.click(await screen.findByRole("button", { name: "프로젝트 삭제" }));
    expect(screen.getByText("영상과 검색 기록이 모두 삭제됩니다.")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "삭제" }).at(-1)!);

    expect(deleteProject).toHaveBeenCalledWith("p1");
    expect(replace).toHaveBeenCalledWith("/");
  });
});
