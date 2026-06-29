import { describe, it, expect } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { AuthProvider, useAuth } from "@/lib/auth/AuthContext";

function Probe() {
  const { token, signIn, signOut } = useAuth();
  return (
    <div>
      <span data-testid="token">{token ?? "none"}</span>
      <button onClick={() => signIn("t1")}>in</button>
      <button onClick={() => signOut()}>out</button>
    </div>
  );
}

describe("AuthContext", () => {
  it("starts signed out, signs in, signs out", () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    expect(screen.getByTestId("token").textContent).toBe("none");

    act(() => screen.getByText("in").click());
    expect(screen.getByTestId("token").textContent).toBe("t1");

    act(() => screen.getByText("out").click());
    expect(screen.getByTestId("token").textContent).toBe("none");
  });
});
