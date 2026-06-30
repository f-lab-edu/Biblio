import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

async function loadGetCsrfToken() {
  vi.resetModules();
  const { getCsrfToken } = await import("@/lib/auth/token");
  return getCsrfToken;
}

function clearCookies() {
  document.cookie.split(";").forEach((cookie) => {
    const name = cookie.split("=")[0]?.trim();
    if (name) {
      document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
    }
  });
}

describe("csrf token helper", () => {
  beforeEach(() => {
    delete process.env.NEXT_PUBLIC_CSRF_COOKIE_NAME;
    clearCookies();
    localStorage.clear();
  });

  afterEach(() => {
    delete process.env.NEXT_PUBLIC_CSRF_COOKIE_NAME;
    clearCookies();
  });

  it("returns null when the csrf cookie is missing", async () => {
    const getCsrfToken = await loadGetCsrfToken();

    expect(getCsrfToken()).toBeNull();
  });

  it("reads the default csrf cookie without using localStorage", async () => {
    const getCsrfToken = await loadGetCsrfToken();

    document.cookie = "biblio_csrf_token=csrf-1";

    expect(getCsrfToken()).toBe("csrf-1");
  });

  it("reads the csrf cookie name from NEXT_PUBLIC_CSRF_COOKIE_NAME", async () => {
    process.env.NEXT_PUBLIC_CSRF_COOKIE_NAME = "custom_csrf_token";
    const getCsrfToken = await loadGetCsrfToken();

    document.cookie = "biblio_csrf_token=csrf-1";
    document.cookie = "custom_csrf_token=csrf-2";

    expect(getCsrfToken()).toBe("csrf-2");
  });
});
