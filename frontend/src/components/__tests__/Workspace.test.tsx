import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const replace = vi.fn();
const currentUser = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api", () => ({
  api: {
    currentUser: (...a: unknown[]) => currentUser(...a),
    listVideos: () => Promise.resolve([]),
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
});
