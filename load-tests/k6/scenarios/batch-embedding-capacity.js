import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import { SharedArray } from 'k6/data';
import exec from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';

import {
  batchFor,
  durationValue,
  nonNegativeInteger,
  positiveInteger,
  selectObservedMix,
  selectRecords,
  traceId,
  validateFixture,
  validateRuntimeModel,
} from '../lib/enriched-text-inputs.js';

const CAPACITY_PATH = '../data/batch-embedding-enriched-texts.json';
const TRUNCATION_PATH = '../data/batch-embedding-truncation-inputs.json';
const DB_PROFILE_PATH = '../data/batch-embedding-db-profile.json';
const capacityDocument = JSON.parse(open(CAPACITY_PATH));
const truncationDocument = JSON.parse(open(TRUNCATION_PATH));
const dbProfile = JSON.parse(open(DB_PROFILE_PATH));
const capacityFixture = validateFixture(
  capacityDocument,
  'synthetic-token-bands',
  false,
);
const truncationFixture = validateFixture(
  truncationDocument,
  'synthetic-truncation-token-bands',
  true,
);

const inputSet = __ENV.INPUT_SET || 'capacity';
const inputBucket = __ENV.INPUT_BUCKET || 'balanced';
const contentProfile = __ENV.CONTENT_PROFILE || 'all';
const selectedInputs = new SharedArray(`batch-embedding-${inputSet}`, () => {
  if (inputSet === 'observed-mix') {
    if (inputBucket !== 'observed-mix' || contentProfile !== 'all') {
      throw new Error('observed-mix requires bucket=observed-mix and content=all');
    }
    return selectObservedMix(
      capacityFixture.records,
      truncationFixture.records,
      dbProfile,
    );
  }
  if (inputSet === 'capacity') {
    return selectRecords(capacityFixture.records, inputBucket, contentProfile);
  }
  if (inputSet === 'truncation') {
    return selectRecords(truncationFixture.records, inputBucket, contentProfile);
  }
  throw new Error('INPUT_SET must be capacity, truncation, or observed-mix');
});

const vus = positiveInteger(__ENV.LT_VUS || '1', 'LT_VUS');
const duration = durationValue(__ENV.LT_DURATION || '2m', 'LT_DURATION');
const durationSeconds = secondsFor(duration);
const gracefulStop = durationValue(__ENV.LT_GRACEFUL_STOP || '30s', 'LT_GRACEFUL_STOP');
const batchSize = positiveInteger(__ENV.BATCH_SIZE || '4', 'BATCH_SIZE');
const clientTimeoutSeconds = positiveInteger(
  __ENV.LT_CLIENT_TIMEOUT_SECONDS || '180',
  'LT_CLIENT_TIMEOUT_SECONDS',
);
const retryProfile = __ENV.RETRY_PROFILE || 'raw';
if (retryProfile !== 'raw' && retryProfile !== 'worker-client') {
  throw new Error('RETRY_PROFILE must be raw or worker-client');
}
const responseVerification = __ENV.RESPONSE_VERIFICATION || 'none';
if (!['none', 'sampled', 'all'].includes(responseVerification)) {
  throw new Error('RESPONSE_VERIFICATION must be none, sampled, or all');
}
const retrySeed = nonNegativeInteger(__ENV.RETRY_SEED || '104', 'RETRY_SEED');
const maxRetries = retryProfile === 'worker-client' ? 3 : 0;

const initialRequestCount = new Counter('batch_embedding_initial_requests');
const initialTextCount = new Counter('batch_embedding_initial_texts');
const retryRequestCount = new Counter('batch_embedding_retry_requests');
const initial503Count = new Counter('batch_embedding_initial_503');
const retrySuccessCount = new Counter('batch_embedding_retry_success');
const retryExhaustedCount = new Counter('batch_embedding_retry_exhausted');
const status200Count = new Counter('batch_embedding_status_200');
const status503Count = new Counter('batch_embedding_status_503');
const unexpectedStatusCount = new Counter('batch_embedding_unexpected_status');
const clientErrorCount = new Counter('batch_embedding_client_error');
const invalidResponseCount = new Counter('batch_embedding_invalid_response');
const successfulTextCount = new Counter('batch_embedding_successful_texts');
const successRate = new Rate('batch_embedding_success_rate');
const successDuration = new Trend('batch_embedding_success_duration', true);
const logicalDuration = new Trend('batch_embedding_logical_duration', true);
const payloadBytes = new Trend('batch_embedding_payload_bytes', true);
const attemptCounters = [1, 2, 3, 4].map(
  (attempt) => new Counter(`batch_embedding_attempt_${attempt}`),
);
const inputBucketCounters = Object.fromEntries(
  [
    'short',
    'medium',
    'long',
    'xlong',
    'boundary',
    'over_limit',
    'observed_tail',
  ].map((bucket) => [bucket, new Counter(`batch_embedding_input_${bucket}_texts`)]),
);
const windowMetrics = Object.fromEntries(
  ['first', 'middle', 'last'].map((window) => [
    window,
    {
      successfulTexts: new Counter(`batch_embedding_${window}_window_successful_texts`),
      status503: new Counter(`batch_embedding_${window}_window_status_503`),
      duration: new Trend(`batch_embedding_${window}_window_logical_duration`, true),
    },
  ]),
);

export const options = {
  discardResponseBodies: true,
  scenarios: {
    batch_embedding_capacity: {
      executor: 'constant-vus',
      vus,
      duration,
      gracefulStop,
    },
  },
  thresholds: {
    checks: ['rate==1'],
    batch_embedding_unexpected_status: ['count==0'],
    batch_embedding_client_error: ['count==0'],
    batch_embedding_invalid_response: ['count==0'],
    ...(retryProfile === 'worker-client'
      ? { batch_embedding_retry_exhausted: ['count==0'] }
      : {}),
  },
};

let successfulBatches = 0;

function secondsFor(value) {
  const match = /^(\d+(?:\.\d+)?)(ms|s|m)$/.exec(value);
  const number = Number(match[1]);
  if (match[2] === 'ms') return number / 1000;
  if (match[2] === 'm') return number * 60;
  return number;
}

function requiredEnvironment(name) {
  const value = __ENV[name];
  if (!value) fail(`${name} is required`);
  return value;
}

export function setup() {
  const modelVersion = requiredEnvironment('MODEL_VERSION');
  validateRuntimeModel(capacityFixture.modelVersion, modelVersion);
  validateRuntimeModel(truncationFixture.modelVersion, modelVersion);
  return {
    targetUrl: requiredEnvironment('TARGET_URL'),
    modelVersion,
    traceNamespace: requiredEnvironment('TRACE_ID_NAMESPACE'),
    startedAtMs: Date.now(),
  };
}

function shouldVerifyResponse() {
  if (responseVerification === 'all') return true;
  if (responseVerification === 'none') return false;
  return successfulBatches === 0 || (successfulBatches + 1) % 50 === 0;
}

function validEmbeddingBody(response, expectedCount, verifyThisResponse) {
  if (!verifyThisResponse || response.status !== 200) return true;
  try {
    const body = response.json();
    return (
      Array.isArray(body.embeddings) &&
      body.embeddings.length === expectedCount &&
      body.embeddings.every(
        (embedding) =>
          Array.isArray(embedding) &&
          embedding.length > 0 &&
          embedding.every((value) => Number.isFinite(value)),
      )
    );
  } catch (_error) {
    return false;
  }
}

function deterministicJitter(iteration, attemptIndex) {
  let value = (retrySeed ^ (iteration + 1) ^ ((attemptIndex + 1) * 2654435761)) >>> 0;
  value = (Math.imul(value, 1664525) + 1013904223) >>> 0;
  return value / 4294967296;
}

function retryDelaySeconds(iteration, attemptIndex) {
  return (2 ** attemptIndex) * (1 + deterministicJitter(iteration, attemptIndex) * 0.25);
}

function utf8Bytes(value) {
  let bytes = 0;
  for (const character of value) {
    const codePoint = character.codePointAt(0);
    if (codePoint <= 0x7f) bytes += 1;
    else if (codePoint <= 0x7ff) bytes += 2;
    else if (codePoint <= 0xffff) bytes += 3;
    else bytes += 4;
  }
  return bytes;
}

function windowFor(startedAtMs) {
  const elapsedSeconds = Math.max(0, (Date.now() - startedAtMs) / 1000);
  if (elapsedSeconds < Math.min(300, durationSeconds)) return 'first';
  if (elapsedSeconds >= Math.max(0, durationSeconds - 300)) return 'last';
  const middleStart = Math.max(300, durationSeconds * 0.4);
  if (elapsedSeconds >= middleStart && elapsedSeconds < middleStart + 300) return 'middle';
  return null;
}

function recordInput(batch, requestBody, tags) {
  initialRequestCount.add(1, tags);
  initialTextCount.add(batch.length, tags);
  payloadBytes.add(utf8Bytes(requestBody), tags);
  for (const record of batch) inputBucketCounters[record.length_bucket].add(1, tags);
}

function recordAttempt(response, attemptIndex, tags, window) {
  attemptCounters[attemptIndex].add(1, tags);
  if (attemptIndex > 0) retryRequestCount.add(1, tags);
  if (response.status === 200) {
    status200Count.add(1, tags);
    successDuration.add(response.timings.duration, tags);
    if (attemptIndex > 0) retrySuccessCount.add(1, tags);
  } else if (response.status === 503) {
    status503Count.add(1, tags);
    if (attemptIndex === 0) initial503Count.add(1, tags);
    if (window) windowMetrics[window].status503.add(1, tags);
  } else if (response.status === 0) {
    clientErrorCount.add(1, tags);
  } else {
    unexpectedStatusCount.add(1, tags);
  }
}

function postBatch(targetUrl, modelVersion, requestBody, requestTraceId, tags, verify) {
  return http.post(targetUrl, requestBody, {
    headers: {
      'Content-Type': 'application/json',
      'X-Embedding-Workload': 'video_preprocess',
      'X-Trace-Id': requestTraceId,
    },
    timeout: `${clientTimeoutSeconds}s`,
    responseType: verify ? 'text' : 'none',
    tags,
  });
}

export default function ({ targetUrl, modelVersion, traceNamespace, startedAtMs }) {
  const iteration = exec.scenario.iterationInTest;
  const batch = batchFor(selectedInputs, iteration * batchSize, batchSize);
  const tags = {
    workload: 'video_preprocess',
    input_set: inputSet,
    input_bucket: inputBucket,
    content_profile: contentProfile,
    batch_size: String(batchSize),
    retry_profile: retryProfile,
  };
  const requestBody = JSON.stringify({
    texts: batch.map((record) => record.text),
    model_version: modelVersion,
  });
  const requestTraceId = traceId(traceNamespace, iteration);
  const verifyThisResponse = shouldVerifyResponse();
  const window = windowFor(startedAtMs);
  const logicalStartedAt = Date.now();
  recordInput(batch, requestBody, tags);

  let response;
  let attemptIndex = 0;
  for (; attemptIndex <= maxRetries; attemptIndex += 1) {
    response = postBatch(
      targetUrl,
      modelVersion,
      requestBody,
      requestTraceId,
      tags,
      verifyThisResponse,
    );
    recordAttempt(response, attemptIndex, tags, window);
    if (response.status !== 503 || attemptIndex === maxRetries) break;
    sleep(retryDelaySeconds(iteration, attemptIndex));
  }

  const bodyIsValid = validEmbeddingBody(response, batch.length, verifyThisResponse);
  if (!bodyIsValid) invalidResponseCount.add(1, tags);
  const succeeded = response.status === 200 && bodyIsValid;
  const expectedRaw503 = retryProfile === 'raw' && response.status === 503;
  successRate.add(succeeded, tags);
  check(response, {
    'response matches retry profile': () => succeeded || expectedRaw503,
  });

  if (succeeded) {
    successfulBatches += 1;
    successfulTextCount.add(batch.length, tags);
    const elapsedMs = Date.now() - logicalStartedAt;
    logicalDuration.add(elapsedMs, tags);
    if (window) {
      windowMetrics[window].successfulTexts.add(batch.length, tags);
      windowMetrics[window].duration.add(elapsedMs, tags);
    }
  } else if (response.status === 503 && retryProfile === 'worker-client') {
    retryExhaustedCount.add(1, tags);
    exec.test.abort('Worker retry profile exhausted all 503 attempts.');
  } else if (response.status === 0) {
    exec.test.abort('Batch embedding request reached the client timeout.');
  }
}
