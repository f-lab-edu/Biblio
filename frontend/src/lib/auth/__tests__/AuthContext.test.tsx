import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";

const currentUser = vi.fn();
const logout = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    currentUser: (...a: unknown[]) => currentUser(...a),
    logout: (...a: unknown[]) => logout(...a),
  },
}));

import { AuthProvider, useAuth } from "@/lib/auth/AuthContext";

function Probe() {
  const { userId, signIn, signOut } = useAuth();
  return (
    <div>
      <span data-testid="user">{userId ?? "none"}</span>
      <button onClick={() => signIn("u1")}>in</button>
      <button onClick={() => signOut()}>out</button>
    </div>
  );
}

describe("AuthContext", () => {
  beforeEach(() => {
    currentUser.mockReset();
    logout.mockReset();
    localStorage.clear();
  });

  it("updates the user id when signIn is called", async () => {
    currentUser.mockResolvedValue({ userId: "u0" });
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("u0"));

    await act(async () => screen.getByText("in").click());

    expect(screen.getByTestId("user").textContent).toBe("u1");
  });

  it("loads the current cookie-backed user and signs out", async () => {
    currentUser.mockResolvedValue({ userId: "u1" });
    logout.mockResolvedValue(undefined);
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("u1"));

    await act(async () => screen.getByText("out").click());
    expect(logout).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("none"));
  });
});
