import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const replace = vi.fn();
const currentUser = vi.fn();
const logout = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api", () => ({
  api: {
    currentUser: (...a: unknown[]) => currentUser(...a),
    logout: () => logout(),
  },
}));

import { AppHeader } from "@/components/AppHeader";
import { AuthProvider } from "@/lib/auth/AuthContext";

function renderHeader(ui: React.ReactNode) {
  return render(<AuthProvider>{ui}</AuthProvider>);
}

describe("AppHeader", () => {
  beforeEach(() => {
    replace.mockReset();
    currentUser.mockReset();
    logout.mockReset();
    currentUser.mockResolvedValue({ userId: "u1" });
    logout.mockResolvedValue(undefined);
  });

  it("always shows a logout button", async () => {
    renderHeader(<AppHeader />);
    expect(await screen.findByRole("button", { name: "로그아웃" })).toBeInTheDocument();
  });

  it("signs out and sends the user to /login", async () => {
    renderHeader(<AppHeader />);
    await userEvent.click(await screen.findByRole("button", { name: "로그아웃" }));

    expect(logout).toHaveBeenCalled();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  });

  it("shows a back link only when backHref is given", async () => {
    const { unmount } = renderHeader(<AppHeader backHref="/" backLabel="프로젝트 목록" />);
    const link = await screen.findByRole("link", { name: "← 프로젝트 목록" });
    expect(link).toHaveAttribute("href", "/");
    unmount();

    renderHeader(<AppHeader />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("renders page specific actions next to logout", async () => {
    renderHeader(
      <AppHeader title="워크스페이스">
        <button type="button">프로젝트 삭제</button>
      </AppHeader>
    );
    expect(await screen.findByText("워크스페이스")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "프로젝트 삭제" })).toBeInTheDocument();
  });
});
