from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

SCHEMA_VERSION = 2
DEFAULT_SEED = 104
ITEMS_PER_COMBINATION = 40
TRUNCATION_ITEMS_PER_COMBINATION = 10
EFFECTIVE_TOKEN_LIMIT = 512
BATCH_SIZE = 4
CONTENT_PROFILES = (
    "narration",
    "mixed_terms",
    "ocr_numeric",
    "scene_tags",
    "fallback_text",
)

TOPICS = (
    "검색 결과 정렬",
    "영상 처리 순서",
    "데이터베이스 인덱스",
    "분산 작업 대기열",
    "모델 입력 정규화",
    "서비스 장애 복구",
    "사용자 요청 흐름",
    "배치 처리 효율",
)
SCENES = (
    "발표자가 화면의 처리 순서를 설명하는 회의실",
    "여러 그래프가 표시된 운영 대시보드",
    "코드와 실행 결과가 함께 보이는 개발 화면",
    "작업 대기열의 변화를 나타내는 도표",
    "영상 장면과 자막이 나란히 놓인 편집 화면",
)
TECH_TERMS = (
    "Worker concurrency",
    "embedding batch",
    "queue timeout",
    "PostgreSQL index",
    "Cloud Run revision",
    "API payload",
)
UNITS = ("ms", "초", "%", "MB", "건/초", "token")
TAG_GROUPS = (
    "발표, 모니터, 회의실, 도표, 설명",
    "개발자, 코드, 터미널, 로그, 분석",
    "영상, 자막, 장면, 검색, 데이터",
    "서버, 대기열, 지표, 경고, 복구",
    "그래프, 표, 숫자, 비교, 결과",
)


class TokenCounter(Protocol):
    @property
    def metadata(self) -> dict[str, Any]: ...

    def count(self, text: str) -> int: ...


@dataclass(frozen=True)
class BucketRange:
    name: str
    minimum: int
    maximum: int


CAPACITY_TOKEN_BUCKETS = (
    BucketRange("short", 64, 128),
    BucketRange("medium", 129, 256),
    BucketRange("long", 257, 384),
    BucketRange("xlong", 385, 480),
    BucketRange("boundary", 481, 512),
)
TRUNCATION_TOKEN_BUCKETS = (
    BucketRange("over_limit", 513, 768),
    BucketRange("observed_tail", 769, 896),
)


@dataclass(frozen=True)
class Limits:
    model_version: str
    max_text_chars: int
    max_texts_per_request: int
    max_payload_bytes: int
    effective_token_limit: int


class HuggingFaceTokenCounter:
    def __init__(self, tokenizer_path: str, effective_limit: int) -> None:
        from transformers import AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
        self._effective_limit = effective_limit

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "class": type(self._tokenizer).__name__,
            "name_or_path": self._tokenizer.name_or_path,
            "model_max_length": self._tokenizer.model_max_length,
            "effective_max_length": self._effective_limit,
        }

    def count(self, text: str) -> int:
        encoded = self._tokenizer(text, truncation=False)
        return len(encoded["input_ids"])


def load_normalizer(source_path: Path) -> Callable[[str], str]:
    spec = importlib.util.spec_from_file_location("pipeline_text_normalizer", source_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load normalizer: {source_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    normalizer = getattr(module, "normalize_enriched_text", None)
    if not callable(normalizer):
        raise RuntimeError(f"normalize_enriched_text is missing: {source_path}")
    return normalizer


def read_limits(db_profile: dict[str, Any]) -> Limits:
    endpoint = db_profile["live_embedding_endpoint"]
    runtime = db_profile["runtime_limits"]["application_effective_limit"]
    return Limits(
        model_version=db_profile["database"]["sample_filter"]["embedding_model_version"],
        max_text_chars=int(endpoint["max_text_length_chars"]),
        max_texts_per_request=int(endpoint["max_texts_per_request"]),
        max_payload_bytes=int(endpoint["max_payload_bytes"]),
        effective_token_limit=int(runtime["tokens"]),
    )


def _narration_sentence(profile: str, rng: random.Random, sequence: int) -> str:
    topic = rng.choice(TOPICS)
    action = rng.choice(("확인합니다", "비교합니다", "기록합니다", "설명합니다"))
    if profile == "mixed_terms":
        term = rng.choice(TECH_TERMS)
        return f"{topic}의 {sequence + 1}번째 단계에서는 {term} 값을 함께 {action}"
    if profile == "fallback_text":
        return f"영상 대본의 {sequence + 1}번째 설명은 {topic}의 원인과 결과를 차례로 {action}"
    return f"이 구간에서는 {topic}의 {sequence + 1}번째 변화를 차례로 {action}"


def _vision_suffix(profile: str, rng: random.Random, sequence: int) -> list[str]:
    if profile == "fallback_text":
        return []
    caption = f"화면에는 {rng.choice(SCENES)}이 보입니다."
    tags = rng.choice(TAG_GROUPS)
    if profile == "ocr_numeric":
        value = 10 + ((sequence * 17) % 983)
        ocr = f"표에는 처리량 {value}{rng.choice(UNITS)}와 측정 시각 14:{sequence % 60:02d}가 표시됩니다."
        return [caption, ocr, tags]
    if profile == "scene_tags":
        return [caption, f"장면 태그는 {tags} 순서로 정리되어 있습니다.", tags]
    if profile == "mixed_terms":
        return [caption, f"화면 표시는 {rng.choice(TECH_TERMS)} 상태를 나타냅니다.", tags]
    return [caption, tags]


def _target_token_count(bucket: BucketRange, position: int, total: int) -> int:
    if total == 1:
        return bucket.minimum
    fraction = position / (total - 1)
    return bucket.minimum + round((bucket.maximum - bucket.minimum) * fraction)


def _build_text_for_token_bucket(
    profile: str,
    target_tokens: int,
    bucket: BucketRange,
    rng: random.Random,
    sequence: int,
    normalize: Callable[[str], str],
    token_counter: TokenCounter,
    max_text_chars: int,
) -> str:
    suffix = _vision_suffix(profile, rng, sequence)
    narration = [_narration_sentence(profile, rng, sequence)]
    filler_words = ("추가", "설명", "확인", "기록", "비교")
    filler_index = 0
    while True:
        current = normalize(" ".join([*narration, *suffix]))
        current_tokens = token_counter.count(current)
        if current_tokens >= target_tokens:
            break
        next_sentence = _narration_sentence(
            profile, rng, sequence + len(narration)
        )
        candidate = normalize(" ".join([*narration, next_sentence, *suffix]))
        candidate_tokens = token_counter.count(candidate)
        if candidate_tokens <= target_tokens:
            narration.append(next_sentence)
            continue
        filler = filler_words[filler_index % len(filler_words)]
        candidate = normalize(" ".join([*narration, filler, *suffix]))
        candidate_tokens = token_counter.count(candidate)
        if candidate_tokens > target_tokens and current_tokens >= bucket.minimum:
            break
        if candidate_tokens > bucket.maximum:
            raise ValueError(
                f"Could not fill {profile} text inside "
                f"{bucket.minimum}..{bucket.maximum} tokens"
            )
        narration.append(filler)
        filler_index += 1
    text = normalize(" ".join([*narration, *suffix]))
    raw_tokens = token_counter.count(text)
    if not bucket.minimum <= raw_tokens <= bucket.maximum:
        raise ValueError(
            f"Generated {profile} token count {raw_tokens} outside "
            f"{bucket.minimum}..{bucket.maximum}"
        )
    if len(text) > max_text_chars:
        raise ValueError(
            f"Generated {profile} text length {len(text)} exceeds {max_text_chars}"
        )
    return text


def _text_record(
    text_id: str,
    text: str,
    bucket: str,
    profile: str,
    token_counter: TokenCounter,
    effective_token_limit: int,
) -> dict[str, Any]:
    raw_tokens = token_counter.count(text)
    effective_tokens = min(raw_tokens, effective_token_limit)
    return {
        "id": text_id,
        "text": text,
        "length_bucket": bucket,
        "content_profile": profile,
        "char_count": len(text),
        "byte_count": len(text.encode("utf-8")),
        "whitespace_word_count": len(text.split()),
        "raw_model_token_count": raw_tokens,
        "effective_model_token_count": effective_tokens,
        "would_truncate": raw_tokens > effective_token_limit,
    }


def _generate_records(
    db_profile: dict[str, Any],
    token_counter: TokenCounter,
    normalize: Callable[[str], str],
    buckets: tuple[BucketRange, ...],
    *,
    seed: int = DEFAULT_SEED,
    items_per_combination: int = ITEMS_PER_COMBINATION,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    limits = read_limits(db_profile)
    records: list[dict[str, Any]] = []
    bucket_total = len(CONTENT_PROFILES) * items_per_combination
    for bucket in buckets:
        for profile_index, profile in enumerate(CONTENT_PROFILES):
            for item_index in range(items_per_combination):
                position = item_index * len(CONTENT_PROFILES) + profile_index
                target = _target_token_count(bucket, position, bucket_total)
                sequence = len(records)
                text = _build_text_for_token_bucket(
                    profile,
                    target,
                    bucket,
                    rng,
                    sequence,
                    normalize,
                    token_counter,
                    limits.max_text_chars,
                )
                text_id = f"et-{bucket.name}-{profile}-{item_index + 1:03d}"
                records.append(
                    _text_record(
                        text_id,
                        text,
                        bucket.name,
                        profile,
                        token_counter,
                        limits.effective_token_limit,
                    )
                )
    return records


def generate_fixture(
    db_profile: dict[str, Any],
    token_counter: TokenCounter,
    normalize: Callable[[str], str],
    *,
    seed: int = DEFAULT_SEED,
    items_per_combination: int = ITEMS_PER_COMBINATION,
) -> dict[str, Any]:
    limits = read_limits(db_profile)
    if CAPACITY_TOKEN_BUCKETS[-1].maximum != limits.effective_token_limit:
        raise ValueError("Capacity token buckets do not match the live effective limit")
    records = _generate_records(
        db_profile,
        token_counter,
        normalize,
        CAPACITY_TOKEN_BUCKETS,
        seed=seed,
        items_per_combination=items_per_combination,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "synthetic-token-bands",
        "generator_seed": seed,
        "model_version": limits.model_version,
        "items_per_combination": items_per_combination,
        "texts": records,
    }


def generate_truncation_fixture(
    db_profile: dict[str, Any],
    token_counter: TokenCounter,
    normalize: Callable[[str], str],
    *,
    seed: int = DEFAULT_SEED,
    items_per_combination: int = TRUNCATION_ITEMS_PER_COMBINATION,
) -> dict[str, Any]:
    limits = read_limits(db_profile)
    records = _generate_records(
        db_profile,
        token_counter,
        normalize,
        TRUNCATION_TOKEN_BUCKETS,
        seed=seed + 1,
        items_per_combination=items_per_combination,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "synthetic-truncation-token-bands",
        "generator_seed": seed + 1,
        "model_version": limits.model_version,
        "items_per_combination": items_per_combination,
        "texts": records,
    }


def request_payload_size(texts: list[str], model_version: str) -> int:
    body = json.dumps(
        {"texts": texts, "model_version": model_version},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return len(body.encode("utf-8"))


def _payload_boundary_texts(limits: Limits) -> tuple[list[str], list[str]]:
    texts: list[str] = []
    unit = "가" * limits.max_text_chars
    while len(texts) < limits.max_texts_per_request:
        candidate = [*texts, unit]
        if request_payload_size(candidate, limits.model_version) > limits.max_payload_bytes:
            break
        texts = candidate
    else:
        raise ValueError("Could not produce a payload above the configured byte limit")
    lower = 1
    upper = limits.max_text_chars
    while lower <= upper:
        middle = (lower + upper) // 2
        candidate = [*texts, "가" * middle]
        if request_payload_size(candidate, limits.model_version) <= limits.max_payload_bytes:
            lower = middle + 1
        else:
            upper = middle - 1
    below = [*texts, "가" * upper]
    above = [*texts, "가" * (upper + 1)]
    return below, above


def generate_boundary_fixture(db_profile: dict[str, Any]) -> dict[str, Any]:
    limits = read_limits(db_profile)
    payload_below, payload_above = _payload_boundary_texts(limits)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "synthetic-contract-cases",
        "model_version": limits.model_version,
        "cases": [
            {
                "case": "empty",
                "texts": [""],
                "expected_status": 400,
                "expected_code": "INVALID_ARGUMENT",
            },
            {"case": "whitespace_only", "texts": ["   "], "expected_status": 200},
            {"case": "one_char", "texts": ["가"], "expected_status": 200},
            {
                "case": "char_limit_minus_1",
                "texts": ["가" * (limits.max_text_chars - 1)],
                "expected_status": 200,
            },
            {"case": "char_limit", "texts": ["가" * limits.max_text_chars], "expected_status": 200},
            {
                "case": "char_limit_plus_1",
                "texts": ["가" * (limits.max_text_chars + 1)],
                "expected_status": 400,
                "expected_code": "INVALID_ARGUMENT",
            },
            {
                "case": "max_texts",
                "texts": [
                    f"합성 입력 {index + 1}"
                    for index in range(limits.max_texts_per_request)
                ],
                "expected_status": 200,
            },
            {
                "case": "max_texts_plus_1",
                "texts": [
                    f"합성 입력 {index + 1}"
                    for index in range(limits.max_texts_per_request + 1)
                ],
                "expected_status": 400,
                "expected_code": "INVALID_ARGUMENT",
            },
            {
                "case": "payload_limit_minus",
                "texts": payload_below,
                "payload_bytes": request_payload_size(
                    payload_below, limits.model_version
                ),
                "expected_status": 200,
            },
            {
                "case": "payload_limit_plus",
                "texts": payload_above,
                "payload_bytes": request_payload_size(
                    payload_above, limits.model_version
                ),
                "expected_status": 413,
                "expected_code": "PAYLOAD_TOO_LARGE",
            },
        ],
    }


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _percentile(values: list[int], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _value_summary(values: list[int]) -> dict[str, float | int]:
    return {
        "min": min(values),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": max(values),
    }


def _group_summaries(
    records: list[dict[str, Any]], group_field: str
) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for group_name in sorted({str(record[group_field]) for record in records}):
        group = [record for record in records if record[group_field] == group_name]
        truncated = sum(bool(record["would_truncate"]) for record in group)
        summaries[group_name] = {
            "count": len(group),
            "char_count": _value_summary([int(record["char_count"]) for record in group]),
            "raw_token_count": _value_summary(
                [int(record["raw_model_token_count"]) for record in group]
            ),
            "truncation": {"count": truncated, "rate": truncated / len(group)},
        }
    return summaries


def _distribution_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    bucket_counts = Counter(record["length_bucket"] for record in records)
    profile_counts = Counter(record["content_profile"] for record in records)
    cross_counts = Counter(
        f"{record['length_bucket']}:{record['content_profile']}" for record in records
    )
    raw_tokens = [int(record["raw_model_token_count"]) for record in records]
    truncated = sum(bool(record["would_truncate"]) for record in records)
    return {
        "total_count": len(records),
        "bucket_counts": dict(sorted(bucket_counts.items())),
        "content_profile_counts": dict(sorted(profile_counts.items())),
        "cross_distribution": dict(sorted(cross_counts.items())),
        "bucket_summaries": _group_summaries(records, "length_bucket"),
        "content_profile_summaries": _group_summaries(records, "content_profile"),
        "raw_token_count": _value_summary(raw_tokens),
        "truncation": {
            "count": truncated,
            "rate": truncated / len(records),
        },
    }


def build_manifest(
    db_profile: dict[str, Any],
    fixture: dict[str, Any],
    truncation_fixture: dict[str, Any],
    token_counter: TokenCounter,
    *,
    generated_at: str,
    git_sha: str,
    fixture_hash: str,
    truncation_hash: str,
    boundary_hash: str,
    db_profile_hash: str,
) -> dict[str, Any]:
    limits = read_limits(db_profile)
    records = fixture["texts"]
    batch_payloads = [
        request_payload_size(
            [record["text"] for record in records[offset : offset + BATCH_SIZE]],
            limits.model_version,
        )
        for offset in range(0, len(records), BATCH_SIZE)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "generator_git_sha": git_sha,
        "generator_seed": fixture["generator_seed"],
        "target_model_version": limits.model_version,
        "db_profile": {
            "measured_at": db_profile["measured_at"],
            "valid_chunk_count": db_profile["sample"]["valid_count"],
            "ready_video_count": db_profile["sample"]["ready_video_count"],
            "char_count": db_profile["char_count"],
            "byte_count": db_profile["byte_count"],
            "whitespace_word_count": db_profile["whitespace_word_count"],
            "actual_token_distribution": db_profile["actual_token_distribution"],
        },
        "tokenizer": token_counter.metadata,
        "endpoint_limits": {
            "max_text_length_chars": limits.max_text_chars,
            "max_texts_per_request": limits.max_texts_per_request,
            "max_payload_bytes": limits.max_payload_bytes,
            "effective_token_limit": limits.effective_token_limit,
        },
        "distribution": _distribution_summary(records),
        "truncation_fixture_distribution": _distribution_summary(
            truncation_fixture["texts"]
        ),
        "batch_size_4_payload_bytes": {
            "max": max(batch_payloads),
            "limit": limits.max_payload_bytes,
        },
        "hashes": {
            "fixture_sha256": fixture_hash,
            "truncation_fixture_sha256": truncation_hash,
            "boundary_fixture_sha256": boundary_hash,
            "db_profile_sha256": db_profile_hash,
        },
    }


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(f"{serialized}\n", encoding="utf-8")


def _git_sha(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def build_parser(repo_root: Path) -> argparse.ArgumentParser:
    data_dir = repo_root / "load-tests/k6/data"
    parser = argparse.ArgumentParser(description="Generate synthetic enriched text fixtures")
    parser.add_argument(
        "--db-profile",
        type=Path,
        default=data_dir / "batch-embedding-db-profile.json",
    )
    parser.add_argument(
        "--fixture-output",
        type=Path,
        default=data_dir / "batch-embedding-enriched-texts.json",
    )
    parser.add_argument(
        "--boundary-output",
        type=Path,
        default=data_dir / "batch-embedding-boundary-inputs.json",
    )
    parser.add_argument(
        "--truncation-output",
        type=Path,
        default=data_dir / "batch-embedding-truncation-inputs.json",
    )
    parser.add_argument(
        "--manifest-output",
        type=Path,
        default=data_dir / "batch-embedding-enriched-texts.manifest.json",
    )
    parser.add_argument("--tokenizer-path", required=True)
    parser.add_argument(
        "--normalizer-source",
        type=Path,
        default=repo_root / "services/pipeline-worker/src/services/text_normalizer.py",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--git-sha")
    return parser


def main() -> None:
    script_path = Path(__file__).resolve()
    repo_root = script_path.parents[2] if len(script_path.parents) > 2 else Path.cwd()
    arguments = build_parser(repo_root).parse_args()
    db_profile = json.loads(arguments.db_profile.read_text(encoding="utf-8"))
    limits = read_limits(db_profile)
    token_counter = HuggingFaceTokenCounter(
        arguments.tokenizer_path, limits.effective_token_limit
    )
    normalizer = load_normalizer(arguments.normalizer_source)
    fixture = generate_fixture(db_profile, token_counter, normalizer, seed=arguments.seed)
    truncation_fixture = generate_truncation_fixture(
        db_profile, token_counter, normalizer, seed=arguments.seed
    )
    boundary = generate_boundary_fixture(db_profile)
    write_json(arguments.fixture_output, fixture)
    write_json(arguments.truncation_output, truncation_fixture)
    write_json(arguments.boundary_output, boundary)
    manifest = build_manifest(
        db_profile,
        fixture,
        truncation_fixture,
        token_counter,
        generated_at=datetime.now(timezone.utc).isoformat(),
        git_sha=arguments.git_sha or _git_sha(repo_root),
        fixture_hash=sha256_path(arguments.fixture_output),
        truncation_hash=sha256_path(arguments.truncation_output),
        boundary_hash=sha256_path(arguments.boundary_output),
        db_profile_hash=sha256_path(arguments.db_profile),
    )
    write_json(arguments.manifest_output, manifest)
    print(json.dumps(manifest["distribution"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
