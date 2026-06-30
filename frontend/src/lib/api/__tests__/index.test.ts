import { describe, it, expect } from "vitest";
import { resolveApi } from "@/lib/api";

describe("resolveApi", () => {
  it("returns a mock api when useMock is true", async () => {
    const api = resolveApi({ useMock: true, baseUrl: "" });
    const res = await api.signup({ email: "a@b.com", password: "pw12345" });
    expect(res.userId).toBeTruthy();
  });

  it("returns an http api when useMock is false", () => {
    const api = resolveApi({ useMock: false, baseUrl: "https://api.test" });
    expect(api.login).toBeTypeOf("function");
  });
});
