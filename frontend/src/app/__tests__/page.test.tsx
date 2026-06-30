import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const replace = vi.fn();
const currentUser = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api", () => ({
  api: {
    currentUser: (...a: unknown[]) => currentUser(...a),
    listProjects: () => Promise.resolve([]),
    createProject: vi.fn(),
  },
}));

import Home from "@/app/page";
import { AuthProvider } from "@/lib/auth/AuthContext";

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
    currentUser.mockReset();
    localStorage.clear();
  });

  it("redirects to /login when there is no authenticated user", async () => {
    currentUser.mockRejectedValue(new Error("unauthenticated"));
    renderHome();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  });

  it("shows the project list when signed in", async () => {
    currentUser.mockResolvedValue({ userId: "u1" });
    renderHome();
    expect(await screen.findByText("내 프로젝트")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
});
