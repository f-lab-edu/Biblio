import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api", () => ({ api: { listVideos: () => Promise.resolve([]) } }));

import { Workspace } from "@/components/Workspace";
import { AuthProvider } from "@/lib/auth/AuthContext";
import { setToken } from "@/lib/auth/token";

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
    localStorage.clear();
  });

  it("redirects to /login when signed out", () => {
    renderWorkspace();
    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("shows the video panel and search placeholder when signed in", () => {
    setToken("t1");
    renderWorkspace();
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "영상" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "검색" })).toBeInTheDocument();
  });
});
