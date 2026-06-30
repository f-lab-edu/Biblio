import { describe, it, expect, beforeEach } from "vitest";
import { createMockApi } from "@/lib/api/mock";

describe("mock auth", () => {
  let api: ReturnType<typeof createMockApi>;
  beforeEach(() => {
    api = createMockApi();
  });

  it("signup returns a userId and marks the mock user current", async () => {
    const res = await api.signup({ email: "a@b.com", password: "pw12345" });
    expect(res.userId).toBeTruthy();
    await expect(api.currentUser()).resolves.toEqual({ userId: res.userId });
  });

  it("login after signup returns a userId", async () => {
    await api.signup({ email: "a@b.com", password: "pw12345" });
    const res = await api.login({ email: "a@b.com", password: "pw12345" });
    expect(res.userId).toBeTruthy();
  });

  it("login with wrong password throws", async () => {
    await api.signup({ email: "a@b.com", password: "pw12345" });
    await expect(
      api.login({ email: "a@b.com", password: "wrong" })
    ).rejects.toThrow();
  });

  it("signup with an existing email throws", async () => {
    await api.signup({ email: "a@b.com", password: "pw12345" });
    await expect(
      api.signup({ email: "a@b.com", password: "pw12345" })
    ).rejects.toThrow();
  });
});
