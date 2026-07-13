import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const METADATA_ENDPOINT =
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity";

function tokenWithExp(exp: number): string {
  const payload = Buffer.from(JSON.stringify({ exp })).toString("base64url");
  return `header.${payload}.signature`;
}

async function loadModule() {
  return import("@/lib/server/gcp-id-token");
}

describe("gcp id token cache", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-02T00:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("skips metadata token issuing when PROXY_USE_IAM_AUTH is false", async () => {
    vi.stubEnv("PROXY_USE_IAM_AUTH", "false");
    vi.stubGlobal("fetch", vi.fn());

    const { getGoogleIdToken } = await loadModule();
    await expect(getGoogleIdToken("https://core.example")).resolves.toBeNull();

    expect(fetch).not.toHaveBeenCalled();
  });

  it("fetches an audience token from metadata server and caches it", async () => {
    vi.stubEnv("PROXY_USE_IAM_AUTH", "true");
    const token = tokenWithExp(Math.floor(Date.now() / 1000) + 600);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(token, { status: 200 })));

    const { getGoogleIdToken } = await loadModule();
    await expect(getGoogleIdToken("https://core.example")).resolves.toBe(token);
    await expect(getGoogleIdToken("https://core.example")).resolves.toBe(token);

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(`${METADATA_ENDPOINT}?audience=https%3A%2F%2Fcore.example&format=full`);
    expect(init?.headers).toEqual({ "Metadata-Flavor": "Google" });
  });

  it("refreshes the token inside the 60 second expiry window", async () => {
    vi.stubEnv("PROXY_USE_IAM_AUTH", "true");
    const first = tokenWithExp(Math.floor(Date.now() / 1000) + 120);
    const second = tokenWithExp(Math.floor(Date.now() / 1000) + 600);
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(new Response(first, { status: 200 }))
        .mockResolvedValueOnce(new Response(second, { status: 200 }))
    );

    const { getGoogleIdToken } = await loadModule();
    await expect(getGoogleIdToken("https://core.example")).resolves.toBe(first);
    vi.setSystemTime(new Date(Date.now() + 61_000));
    await expect(getGoogleIdToken("https://core.example")).resolves.toBe(second);

    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("caches tokens per audience", async () => {
    vi.stubEnv("PROXY_USE_IAM_AUTH", "true");
    const core = tokenWithExp(Math.floor(Date.now() / 1000) + 600);
    const search = tokenWithExp(Math.floor(Date.now() / 1000) + 600);
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(new Response(core, { status: 200 }))
        .mockResolvedValueOnce(new Response(search, { status: 200 }))
    );

    const { getGoogleIdToken } = await loadModule();
    await expect(getGoogleIdToken("https://core.example")).resolves.toBe(core);
    await expect(getGoogleIdToken("https://search.example")).resolves.toBe(search);

    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
