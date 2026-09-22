import http from 'k6/http';
import { check, fail } from 'k6';

export const options = {
  discardResponseBodies: true,
  scenarios: {
    smoke: {
      executor: 'constant-vus',
      vus: 1,
      duration: '10s',
    },
  },
  thresholds: {
    checks: ['rate==1'],
    http_req_failed: ['rate==0'],
  },
};

function issueIdentityToken(audience) {
  if (!audience) {
    return null;
  }

  const tokenResponse = http.get(
    `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${encodeURIComponent(audience)}`,
    {
      headers: { 'Metadata-Flavor': 'Google' },
      responseType: 'text',
    },
  );
  if (tokenResponse.status !== 200) {
    fail(`ID token issuance failed with status ${tokenResponse.status}`);
  }
  if (!tokenResponse.body) {
    fail('ID token response body was empty');
  }
  return tokenResponse.body;
}

export function setup() {
  if (!__ENV.TARGET_URL) {
    fail('TARGET_URL is required');
  }
  return { identityToken: issueIdentityToken(__ENV.IAM_AUDIENCE) };
}

export default function ({ identityToken }) {
  const headers = identityToken ? { Authorization: `Bearer ${identityToken}` } : {};
  const expectedStatus = Number(__ENV.EXPECTED_STATUS || '200');
  const response = http.get(__ENV.TARGET_URL, { headers });

  check(response, {
    [`status is ${expectedStatus}`]: (result) => result.status === expectedStatus,
  });
}
