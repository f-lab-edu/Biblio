import http from 'k6/http';
import { check, fail } from 'k6';
import { SharedArray } from 'k6/data';
import exec from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';

const INPUT_PATH = '../data/search-embedding-inputs.json';
const DEFAULT_RATE = 1;
const DEFAULT_TIME_UNIT = '1s';
const DEFAULT_DURATION = '2m';
const DEFAULT_CLIENT_TIMEOUT_SECONDS = 15;
const MAX_PRODUCTION_QUERY_CHARS = 1000;

// 입력준비 -> vu 계산 -> 테스트 설정(options) -> 실행전 검증(setup) -> 요청 반복(default)->응답 분류

// 요청 결과를 종류별로 나눠서, 테스트 종료 후 병목 원인을 판단할 수 있게 한다.
const status200Count = new Counter('search_embedding_status_200');
const status503Count = new Counter('search_embedding_status_503');
const unexpectedStatusCount = new Counter('search_embedding_unexpected_status');
const clientTimeoutCount = new Counter('search_embedding_client_timeout');
const connectionErrorCount = new Counter('search_embedding_connection_error');
const successRate = new Rate('search_embedding_success_rate');
const unavailableRate = new Rate('search_embedding_503_rate'); //전체 요청 중 503 비율
const successDuration = new Trend('search_embedding_success_duration', true); //200응답의 처리 시간

// LT_RATE, timeout, VU 값이 0보다 큰 숫자인지 확인한다.
function positiveNumber(value, name) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive number`);
  }
  return parsed;
}

// 1s, 2m, 500ms 같은 값을 초 단위 숫자로 바꿈
function durationSeconds(value, name) {
  const match = /^(\d+(?:\.\d+)?)(ms|s|m)$/.exec(value);
  if (!match) {
    throw new Error(`${name} must use ms, s, or m units`);
  }
  const unitMultiplier = { ms: 0.001, s: 1, m: 60 }[match[2]];
  return positiveNumber(match[1], name) * unitMultiplier;
}

// 쿼리문 파일 검사
function validateQueries(document) {
  if (document.schema_version !== 1 || !Array.isArray(document.queries)) {
    throw new Error('Search embedding input schema is invalid');
  }
  const ids = new Set();
  const texts = new Set();
  for (const query of document.queries) {
    if (!query || typeof query.id !== 'string' || typeof query.text !== 'string') {
      throw new Error('Every search embedding input requires string id and text fields');
    }
    if (!query.text.trim() || query.text.length > MAX_PRODUCTION_QUERY_CHARS) {
      throw new Error(`Query ${query.id} is empty or exceeds production query length`);
    }
    if (ids.has(query.id) || texts.has(query.text)) {
      throw new Error(`Duplicate search embedding input: ${query.id}`);
    }
    ids.add(query.id);
    texts.add(query.text);
  }
  if (document.queries.length === 0) {
    throw new Error('Search embedding input set must not be empty');
  }
  return document.queries;
}

// init 단계에서 입력 파일을 한 번만 읽고, 모든 VU가 검증된 질문 목록을 공유한다.
const queries = new SharedArray('search-embedding-inputs', () => {
  const document = JSON.parse(open(INPUT_PATH));
  return validateQueries(document);
});

const rate = positiveNumber(__ENV.LT_RATE || DEFAULT_RATE, 'LT_RATE'); // 목표 도착률
const timeUnit = __ENV.LT_TIME_UNIT || DEFAULT_TIME_UNIT;
const timeUnitSeconds = durationSeconds(timeUnit, 'LT_TIME_UNIT');
const duration = __ENV.LT_DURATION || DEFAULT_DURATION; // run 실행시간
durationSeconds(duration, 'LT_DURATION');
const clientTimeoutSeconds = positiveNumber(
  __ENV.LT_CLIENT_TIMEOUT_SECONDS || DEFAULT_CLIENT_TIMEOUT_SECONDS,
  'LT_CLIENT_TIMEOUT_SECONDS',
);

// 응답이 timeout까지 지연돼도 목표 도착률을 유지할 수 있는 최소 VU 수를 계산한다.
const requiredVUs = Math.ceil((rate / timeUnitSeconds) * clientTimeoutSeconds);
const preAllocatedVUs = positiveNumber(
  __ENV.LT_PRE_ALLOCATED_VUS || requiredVUs,
  'LT_PRE_ALLOCATED_VUS',
);
const maxVUs = positiveNumber(__ENV.LT_MAX_VUS || preAllocatedVUs, 'LT_MAX_VUS');

if (!Number.isInteger(rate) || !Number.isInteger(preAllocatedVUs) || !Number.isInteger(maxVUs)) {
  throw new Error('LT_RATE and VU values must be integers');
}
if (maxVUs < preAllocatedVUs || preAllocatedVUs < requiredVUs) {
  throw new Error(`VU allocation must satisfy the calculated lower bound ${requiredVUs}`);
}

// 일정한 도착률로 요청을 시작하고, 테스트 자체가 유효하지 않은 조건은 threshold로 실패시킨다.
export const options = {
  discardResponseBodies: true, //임베딩 결과는 받지 않음
  scenarios: {
    search_embedding: {
      executor: 'constant-arrival-rate',
      rate,
      timeUnit,
      duration,
      preAllocatedVUs,
      maxVUs,
    },
  },
  thresholds: {
    checks: ['rate==1'],
    dropped_iterations: ['count==0'],
    search_embedding_unexpected_status: ['count==0'],
    search_embedding_client_timeout: ['count==0'],
    search_embedding_connection_error: ['count==0'],
  },
};

function requiredEnvironment(name) {
  const value = __ENV[name];
  if (!value) {
    fail(`${name} is required`);
  }
  return value;
}

export function setup() {
  // 실제 부하가 시작되기 전에 필수 실행값을 한 번 검증해 각 iteration에 전달한다.
  const targetUrl = requiredEnvironment('TARGET_URL');
  const modelVersion = requiredEnvironment('MODEL_VERSION');
  const runId = requiredEnvironment('RUN_ID');
  const traceNamespace = requiredEnvironment('TRACE_ID_NAMESPACE');
  if (!/^[A-Za-z0-9._-]+$/.test(runId)) {
    fail('RUN_ID contains characters that are unsafe for an HTTP header');
  }
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-8[0-9a-f]{3}$/.test(traceNamespace)) {
    fail('TRACE_ID_NAMESPACE is invalid');
  }
  return { targetUrl, modelVersion, traceNamespace };
}

function responseTimedOut(response) {
  return response.status === 0 && String(response.error || '').toLowerCase().includes('timeout');
}

function recordResponse(response, tags) {
  // 503은 이번 스트레스 테스트에서 찾으려는 정상적인 관찰 결과이므로 별도로 집계한다.
  const succeeded = response.status === 200;
  const unavailable = response.status === 503;
  successRate.add(succeeded, tags);
  unavailableRate.add(unavailable, tags);

  if (succeeded) {
    status200Count.add(1, tags);
    successDuration.add(response.timings.duration, tags);
  } else if (unavailable) {
    status503Count.add(1, tags);
  } else if (responseTimedOut(response)) {
    clientTimeoutCount.add(1, tags);
  } else if (response.status === 0) {
    connectionErrorCount.add(1, tags);
  } else {
    unexpectedStatusCount.add(1, tags);
  }
  return succeeded || unavailable;
}

export default function ({ targetUrl, modelVersion, traceNamespace }) {
  // default 함수는 iteration마다 한 번 실행된다. 질문은 30개를 순서대로 반복 사용한다.
  const iteration = exec.scenario.iterationInTest; // 이번 run에서 몇 번째 iteration인지
  const query = queries[iteration % queries.length];
  const iterationHex = iteration.toString(16).padStart(12, '0');
  const traceId = `${traceNamespace}-${iterationHex}`;
  const tags = { query_id: query.id, workload: 'search' };

  // 선택한 질문 하나를 검색용 embedding 요청으로 보내고 설정된 client timeout까지 기다린다.
  const response = http.post(
    targetUrl,
    JSON.stringify({ texts: [query.text], model_version: modelVersion }),
    {
      headers: {
        'Content-Type': 'application/json',
        'X-Embedding-Workload': 'search',
        'X-Trace-Id': traceId,
      },
      timeout: `${clientTimeoutSeconds}s`,
      tags,
    },
  );

  // 200과 503은 기록 가능한 결과로 통과시키고, timeout과 연결 오류 등은 실패로 남긴다.
  check(response, {
    'response is 200 or expected 503': (result) => recordResponse(result, tags),
  });
}
