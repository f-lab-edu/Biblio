import { describe, it, expect, beforeEach } from "vitest";
import { createMockApi } from "@/lib/api/mock";

describe("mock auth", () => {
  let api: ReturnType<typeof createMockApi>;
  beforeEach(() => {
    api = createMockApi();
  });

  it("signup returns a token and userId", async () => {
    const res = await api.signup({ email: "a@b.com", password: "pw12345" });
    expect(res.token).toBeTruthy();
    expect(res.userId).toBeTruthy();
  });

  it("login after signup returns a token", async () => {
    await api.signup({ email: "a@b.com", password: "pw12345" });
    const res = await api.login({ email: "a@b.com", password: "pw12345" });
    expect(res.token).toBeTruthy();
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
