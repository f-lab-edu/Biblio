import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api", () => ({
  api: { listProjects: () => Promise.resolve([]), createProject: vi.fn() },
}));

import Home from "@/app/page";
import { AuthProvider } from "@/lib/auth/AuthContext";
import { setToken } from "@/lib/auth/token";

function renderHome() {
  return render(
    <AuthProvider>
      <Home />
    </AuthProvider>
  );
}

describe("Home", () => {
  beforeEach(() => {
    replace.mockReset();
    localStorage.clear();
  });

  it("redirects to /login when there is no token", () => {
    renderHome();
    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("shows the project list when signed in", () => {
    setToken("t1");
    renderHome();
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByText("내 프로젝트")).toBeInTheDocument();
  });
});
