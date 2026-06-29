import { describe, it, expect } from "vitest";
import { getToken, setToken, clearToken } from "@/lib/auth/token";

describe("token store", () => {
  it("returns null when nothing is stored", () => {
    expect(getToken()).toBeNull();
  });

  it("stores and reads a token", () => {
    setToken("abc");
    expect(getToken()).toBe("abc");
  });

  it("clears a token", () => {
    setToken("abc");
    clearToken();
    expect(getToken()).toBeNull();
  });
});
