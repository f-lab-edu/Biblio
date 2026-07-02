const METADATA_IDENTITY_ENDPOINT =
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity";
const REFRESH_MARGIN_SECONDS = 60;

interface CachedToken {
  token: string;
  expiresAtSeconds: number;
}

const tokenCache = new Map<string, CachedToken>();

function shouldUseIamAuth(): boolean {
  return process.env.PROXY_USE_IAM_AUTH !== "false";
}

function decodeExpirySeconds(token: string): number {
  const [, payload] = token.split(".");
  if (!payload) return 0;
  const decoded = Buffer.from(payload, "base64url").toString("utf8");
  const parsed = JSON.parse(decoded) as { exp?: number };
  return typeof parsed.exp === "number" ? parsed.exp : 0;
}

function isFresh(cached: CachedToken): boolean {
  const nowSeconds = Math.floor(Date.now() / 1000);
  return cached.expiresAtSeconds - REFRESH_MARGIN_SECONDS > nowSeconds;
}

async function fetchMetadataToken(audience: string): Promise<string> {
  const url = `${METADATA_IDENTITY_ENDPOINT}?audience=${encodeURIComponent(audience)}&format=full`;
  const response = await fetch(url, {
    headers: { "Metadata-Flavor": "Google" },
  });
  if (!response.ok) {
    throw new Error(`metadata token request failed (${response.status})`);
  }
  return response.text();
}

export async function getGoogleIdToken(audience: string): Promise<string | null> {
  if (!shouldUseIamAuth()) return null;

  const cached = tokenCache.get(audience);
  if (cached && isFresh(cached)) return cached.token;

  const token = await fetchMetadataToken(audience);
  tokenCache.set(audience, {
    token,
    expiresAtSeconds: decodeExpirySeconds(token),
  });
  return token;
}
