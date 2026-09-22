const SCHEMA_VERSION = 2;
const EFFECTIVE_TOKEN_LIMIT = 512;
const TRACE_NAMESPACE_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-8[0-9a-f]{3}$/;
const OBSERVED_BUCKETS = [
  'short',
  'medium',
  'long',
  'xlong',
  'boundary',
  'over_limit',
  'observed_tail',
];

export function positiveInteger(value, name) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return parsed;
}

export function nonNegativeInteger(value, name) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(`${name} must be a non-negative integer`);
  }
  return parsed;
}

export function durationValue(value, name) {
  if (!/^\d+(?:\.\d+)?(?:ms|s|m)$/.test(value)) {
    throw new Error(`${name} must use ms, s, or m units`);
  }
  return value;
}

export function booleanValue(value, name) {
  if (value === 'true') return true;
  if (value === 'false') return false;
  throw new Error(`${name} must be true or false`);
}

export function validateFixture(document, expectedSource, shouldTruncate) {
  if (
    !document ||
    document.schema_version !== SCHEMA_VERSION ||
    document.source !== expectedSource ||
    typeof document.model_version !== 'string' ||
    !Array.isArray(document.texts) ||
    document.texts.length === 0
  ) {
    throw new Error(`Invalid enriched text fixture: ${expectedSource}`);
  }
  const ids = new Set();
  const texts = new Set();
  for (const record of document.texts) {
    validateRecord(record, document.model_version, shouldTruncate);
    if (ids.has(record.id) || texts.has(record.text)) {
      throw new Error(`Duplicate enriched text fixture record: ${record.id}`);
    }
    ids.add(record.id);
    texts.add(record.text);
  }
  return { modelVersion: document.model_version, records: document.texts };
}

function validateRecord(record, modelVersion, shouldTruncate) {
  const requiredStrings = ['id', 'text', 'length_bucket', 'content_profile'];
  if (!record || requiredStrings.some((field) => typeof record[field] !== 'string')) {
    throw new Error('Fixture records require string id, text, bucket, and profile fields');
  }
  if (!record.text.trim()) {
    throw new Error(`Fixture text is blank: ${record.id}`);
  }
  const rawTokens = record.raw_model_token_count;
  const effectiveTokens = record.effective_model_token_count;
  if (!Number.isInteger(rawTokens) || rawTokens <= 0) {
    throw new Error(`Invalid raw token count: ${record.id}`);
  }
  if (effectiveTokens !== Math.min(rawTokens, EFFECTIVE_TOKEN_LIMIT)) {
    throw new Error(`Invalid effective token count: ${record.id}`);
  }
  if (record.would_truncate !== shouldTruncate) {
    throw new Error(`Unexpected truncation flag: ${record.id}`);
  }
  if (modelVersion.length === 0) {
    throw new Error('Fixture model version is empty');
  }
}

export function selectRecords(records, inputBucket, contentProfile) {
  const selected = records.filter(
    (record) =>
      (inputBucket === 'balanced' || record.length_bucket === inputBucket) &&
      (contentProfile === 'all' || record.content_profile === contentProfile),
  );
  if (selected.length === 0) {
    throw new Error(
      `No fixture records match bucket=${inputBucket}, profile=${contentProfile}`,
    );
  }
  return interleaveGroups(selected);
}

export function selectObservedMix(capacityRecords, truncationRecords, profile) {
  const observedMix = profile && profile.observed_mix;
  const counts = observedMix && observedMix.raw_token_bucket_counts;
  if (!counts || !Number.isInteger(observedMix.sample_count)) {
    throw new Error('DB profile is missing observed_mix token bucket counts');
  }
  const unknownBuckets = Object.keys(counts).filter(
    (bucket) => !OBSERVED_BUCKETS.includes(bucket),
  );
  if (unknownBuckets.length > 0) {
    throw new Error(`Unknown observed_mix bucket: ${unknownBuckets[0]}`);
  }
  const total = OBSERVED_BUCKETS.reduce((sum, bucket) => {
    const count = counts[bucket];
    if (!Number.isInteger(count) || count < 0) {
      throw new Error(`Invalid observed_mix count for ${bucket}`);
    }
    return sum + count;
  }, 0);
  if (total !== observedMix.sample_count || total <= 0) {
    throw new Error('observed_mix counts do not match sample_count');
  }

  const sourceByBucket = new Map();
  for (const bucket of OBSERVED_BUCKETS) {
    const source = bucket === 'over_limit' || bucket === 'observed_tail'
      ? truncationRecords
      : capacityRecords;
    const candidates = selectRecords(source, bucket, 'all');
    sourceByBucket.set(
      bucket,
      Array.from(
        { length: counts[bucket] },
        (_unused, index) => candidates[index % candidates.length],
      ),
    );
  }
  return smoothWeightedRecords(sourceByBucket, counts, total);
}

function smoothWeightedRecords(sourceByBucket, counts, total) {
  const scores = Object.fromEntries(OBSERVED_BUCKETS.map((bucket) => [bucket, 0]));
  const positions = Object.fromEntries(OBSERVED_BUCKETS.map((bucket) => [bucket, 0]));
  const records = [];
  for (let index = 0; index < total; index += 1) {
    let selectedBucket = null;
    for (const bucket of OBSERVED_BUCKETS) {
      if (positions[bucket] >= counts[bucket]) continue;
      scores[bucket] += counts[bucket];
      if (selectedBucket === null || scores[bucket] > scores[selectedBucket]) {
        selectedBucket = bucket;
      }
    }
    if (selectedBucket === null) {
      throw new Error('Could not build observed_mix sequence');
    }
    records.push(sourceByBucket.get(selectedBucket)[positions[selectedBucket]]);
    positions[selectedBucket] += 1;
    scores[selectedBucket] -= total;
  }
  return records;
}

function interleaveGroups(records) {
  const groups = new Map();
  for (const record of records) {
    const key = `${record.length_bucket}:${record.content_profile}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(record);
  }
  const orderedGroups = Array.from(groups.keys())
    .sort()
    .map((key) => groups.get(key));
  const interleaved = [];
  const largestGroup = Math.max(...orderedGroups.map((group) => group.length));
  for (let index = 0; index < largestGroup; index += 1) {
    for (const group of orderedGroups) {
      if (index < group.length) interleaved.push(group[index]);
    }
  }
  return interleaved;
}

export function batchFor(records, offset, batchSize) {
  const batch = [];
  for (let index = 0; index < batchSize; index += 1) {
    batch.push(records[(offset + index) % records.length]);
  }
  return batch;
}

export function validateRuntimeModel(fixtureModelVersion, runtimeModelVersion) {
  if (fixtureModelVersion !== runtimeModelVersion) {
    throw new Error(
      `Fixture model ${fixtureModelVersion} does not match runtime model ${runtimeModelVersion}`,
    );
  }
}

export function traceId(traceNamespace, sequence) {
  if (!TRACE_NAMESPACE_PATTERN.test(traceNamespace)) {
    throw new Error('TRACE_ID_NAMESPACE is invalid');
  }
  return `${traceNamespace}-${sequence.toString(16).padStart(12, '0')}`;
}
