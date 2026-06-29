import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const login = vi.fn();
vi.mock("@/lib/api", () => ({ api: { login: (...a: unknown[]) => login(...a) } }));

import LoginPage from "@/app/login/page";
import { AuthProvider } from "@/lib/auth/AuthContext";

function renderPage() {
  return render(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    push.mockReset();
    login.mockReset();
  });

  it("submits email/password and redirects home on success", async () => {
    login.mockResolvedValue({ token: "t", userId: "u" });
    renderPage();

    await userEvent.type(screen.getByLabelText("이메일"), "a@b.com");
    await userEvent.type(screen.getByLabelText("비밀번호"), "pw12345");
    await userEvent.click(screen.getByRole("button", { name: "로그인" }));

    expect(login).toHaveBeenCalledWith({ email: "a@b.com", password: "pw12345" });
    expect(push).toHaveBeenCalledWith("/");
  });

  it("shows an error message on failure", async () => {
    login.mockRejectedValue(new Error("이메일 또는 비밀번호가 올바르지 않습니다."));
    renderPage();

    await userEvent.type(screen.getByLabelText("이메일"), "a@b.com");
    await userEvent.type(screen.getByLabelText("비밀번호"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "로그인" }));

    expect(
      await screen.findByText("이메일 또는 비밀번호가 올바르지 않습니다.")
    ).toBeInTheDocument();
  });
});
