import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const signup = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    currentUser: () => Promise.reject(new Error("unauthenticated")),
    signup: (...a: unknown[]) => signup(...a),
    logout: () => Promise.resolve(),
  },
}));

import SignupPage from "@/app/signup/page";
import { AuthProvider } from "@/lib/auth/AuthContext";

function renderPage() {
  return render(
    <AuthProvider>
      <SignupPage />
    </AuthProvider>
  );
}

describe("SignupPage", () => {
  beforeEach(() => {
    push.mockReset();
    signup.mockReset();
  });

  it("submits and redirects home on success", async () => {
    signup.mockResolvedValue({ userId: "u", email: "a@b.com" });
    renderPage();

    await userEvent.type(screen.getByLabelText("이메일"), "a@b.com");
    await userEvent.type(screen.getByLabelText("비밀번호"), "pw12345");
    await userEvent.click(screen.getByRole("button", { name: "가입" }));

    expect(signup).toHaveBeenCalledWith({ email: "a@b.com", password: "pw12345" });
    expect(push).toHaveBeenCalledWith("/");
  });

  it("shows an error message on failure", async () => {
    signup.mockRejectedValue(new Error("이미 가입된 이메일입니다."));
    renderPage();

    await userEvent.type(screen.getByLabelText("이메일"), "a@b.com");
    await userEvent.type(screen.getByLabelText("비밀번호"), "pw12345");
    await userEvent.click(screen.getByRole("button", { name: "가입" }));

    expect(await screen.findByText("이미 가입된 이메일입니다.")).toBeInTheDocument();
  });
});
