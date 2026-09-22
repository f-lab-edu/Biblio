from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from batch_embedding.fixtures.generate import (
    BATCH_SIZE,
    CAPACITY_TOKEN_BUCKETS,
    CONTENT_PROFILES,
    ITEMS_PER_COMBINATION,
    SCHEMA_VERSION,
    TRUNCATION_ITEMS_PER_COMBINATION,
    TRUNCATION_TOKEN_BUCKETS,
    HuggingFaceTokenCounter,
    TokenCounter,
    read_limits,
    request_payload_size,
    sha256_path,
)


class FixtureValidationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise FixtureValidationError(message)


def _validate_record_counts(record: dict[str, Any], token_counter: TokenCounter) -> None:
    text = record["text"]
    raw_tokens = token_counter.count(text)
    require(record["char_count"] == len(text), f"char_count mismatch: {record['id']}")
    require(
        record["byte_count"] == len(text.encode("utf-8")),
        f"byte_count mismatch: {record['id']}",
    )
    require(
        record["whitespace_word_count"] == len(text.split()),
        f"whitespace_word_count mismatch: {record['id']}",
    )
    require(
        record["raw_model_token_count"] == raw_tokens,
        f"raw_model_token_count mismatch: {record['id']}",
    )


def _validate_record_limits(
    record: dict[str, Any],
    bucket_ranges: dict[str, tuple[int, int]],
    max_text_chars: int,
    effective_token_limit: int,
) -> None:
    text = record["text"]
    raw_tokens = int(record["raw_model_token_count"])
    expected_effective = min(raw_tokens, effective_token_limit)
    require(text and not text.isspace(), f"blank text: {record['id']}")
    require(len(text) <= max_text_chars, f"text exceeds char limit: {record['id']}")
    require(record["content_profile"] in CONTENT_PROFILES, f"unknown profile: {record['id']}")
    require(record["length_bucket"] in bucket_ranges, f"unknown bucket: {record['id']}")
    minimum, maximum = bucket_ranges[record["length_bucket"]]
    require(minimum <= raw_tokens <= maximum, f"bucket range mismatch: {record['id']}")
    require(
        record["effective_model_token_count"] == expected_effective,
        f"effective token mismatch: {record['id']}",
    )
    require(
        record["would_truncate"] is (raw_tokens > effective_token_limit),
        f"truncation flag mismatch: {record['id']}",
    )


def _validate_distribution(
    records: list[dict[str, Any]],
    expected_bucket_count: int,
    expected_items_per_combination: int,
) -> None:
    expected_records_per_bucket = (
        len(CONTENT_PROFILES) * expected_items_per_combination
    )
    expected_records_per_profile = (
        expected_bucket_count * expected_items_per_combination
    )
    bucket_counts = Counter(record["length_bucket"] for record in records)
    profile_counts = Counter(record["content_profile"] for record in records)
    cross_counts = Counter(
        (record["length_bucket"], record["content_profile"]) for record in records
    )
    require(
        set(bucket_counts.values()) == {expected_records_per_bucket},
        "uneven bucket distribution",
    )
    require(
        set(profile_counts.values()) == {expected_records_per_profile},
        "uneven profile distribution",
    )
    require(
        set(cross_counts.values()) == {expected_items_per_combination},
        "uneven bucket/profile distribution",
    )


def _validate_batch_payloads(
    records: list[dict[str, Any]], model_version: str, max_payload_bytes: int
) -> int:
    maximum = 0
    for offset in range(0, len(records), BATCH_SIZE):
        texts = [record["text"] for record in records[offset : offset + BATCH_SIZE]]
        payload_size = request_payload_size(texts, model_version)
        require(payload_size <= max_payload_bytes, f"batch payload exceeds limit at {offset}")
        maximum = max(maximum, payload_size)
    return maximum


def _validate_fixture_document(
    document: dict[str, Any],
    db_profile: dict[str, Any],
    token_counter: TokenCounter,
    bucket_ranges: dict[str, tuple[int, int]],
    expected_source: str,
    *,
    expected_items_per_combination: int,
) -> dict[str, Any]:
    limits = read_limits(db_profile)
    records = document.get("texts", [])
    expected_total = (
        len(bucket_ranges) * len(CONTENT_PROFILES) * expected_items_per_combination
    )
    require(document.get("schema_version") == SCHEMA_VERSION, "invalid schema_version")
    require(document.get("source") == expected_source, "invalid fixture source")
    require(document.get("model_version") == limits.model_version, "model version mismatch")
    require(
        document.get("items_per_combination") == expected_items_per_combination,
        "items_per_combination mismatch",
    )
    require(len(records) == expected_total, f"expected {expected_total} texts")
    require(len({record["id"] for record in records}) == len(records), "duplicate ids")
    require(len({record["text"] for record in records}) == len(records), "duplicate texts")
    for record in records:
        _validate_record_counts(record, token_counter)
        _validate_record_limits(
            record,
            bucket_ranges,
            limits.max_text_chars,
            limits.effective_token_limit,
        )
    _validate_distribution(
        records, len(bucket_ranges), expected_items_per_combination
    )
    max_payload = _validate_batch_payloads(
        records, limits.model_version, limits.max_payload_bytes
    )
    truncated = sum(bool(record["would_truncate"]) for record in records)
    return {
        "text_count": len(records),
        "truncated_count": truncated,
        "non_truncated_count": len(records) - truncated,
        "max_batch_size_4_payload_bytes": max_payload,
    }


def validate_fixture(
    document: dict[str, Any],
    db_profile: dict[str, Any],
    token_counter: TokenCounter,
    *,
    expected_items_per_combination: int = ITEMS_PER_COMBINATION,
) -> dict[str, Any]:
    buckets = {
        bucket.name: (bucket.minimum, bucket.maximum)
        for bucket in CAPACITY_TOKEN_BUCKETS
    }
    summary = _validate_fixture_document(
        document,
        db_profile,
        token_counter,
        buckets,
        "synthetic-token-bands",
        expected_items_per_combination=expected_items_per_combination,
    )
    require(summary["truncated_count"] == 0, "capacity fixture contains truncated text")
    return summary


def validate_truncation_fixture(
    document: dict[str, Any],
    db_profile: dict[str, Any],
    token_counter: TokenCounter,
    *,
    expected_items_per_combination: int = TRUNCATION_ITEMS_PER_COMBINATION,
) -> dict[str, Any]:
    buckets = {
        bucket.name: (bucket.minimum, bucket.maximum)
        for bucket in TRUNCATION_TOKEN_BUCKETS
    }
    summary = _validate_fixture_document(
        document,
        db_profile,
        token_counter,
        buckets,
        "synthetic-truncation-token-bands",
        expected_items_per_combination=expected_items_per_combination,
    )
    require(
        summary["truncated_count"] == summary["text_count"],
        "truncation fixture contains non-truncated text",
    )
    return summary


def _case_map(boundary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = boundary.get("cases", [])
    result = {case["case"]: case for case in cases}
    require(len(result) == len(cases), "duplicate boundary case")
    return result


def validate_boundary(boundary: dict[str, Any], db_profile: dict[str, Any]) -> None:
    limits = read_limits(db_profile)
    require(boundary.get("schema_version") == SCHEMA_VERSION, "invalid boundary schema")
    require(boundary.get("model_version") == limits.model_version, "boundary model mismatch")
    cases = _case_map(boundary)
    require(cases["empty"]["texts"] == [""], "invalid empty case")
    require(cases["whitespace_only"]["texts"][0].isspace(), "invalid whitespace case")
    require(len(cases["one_char"]["texts"][0]) == 1, "invalid one_char case")
    require(
        len(cases["char_limit_minus_1"]["texts"][0])
        == limits.max_text_chars - 1,
        "invalid char_limit_minus_1",
    )
    require(len(cases["char_limit"]["texts"][0]) == limits.max_text_chars, "invalid char_limit")
    require(
        len(cases["char_limit_plus_1"]["texts"][0])
        == limits.max_text_chars + 1,
        "invalid char_limit_plus_1",
    )
    require(len(cases["max_texts"]["texts"]) == limits.max_texts_per_request, "invalid max_texts")
    require(
        len(cases["max_texts_plus_1"]["texts"])
        == limits.max_texts_per_request + 1,
        "invalid max_texts_plus_1",
    )
    below = request_payload_size(cases["payload_limit_minus"]["texts"], limits.model_version)
    above = request_payload_size(cases["payload_limit_plus"]["texts"], limits.model_version)
    require(below <= limits.max_payload_bytes, "payload_limit_minus is too large")
    require(above > limits.max_payload_bytes, "payload_limit_plus is not large enough")
    require(
        cases["payload_limit_minus"]["payload_bytes"] == below,
        "payload_limit_minus byte count mismatch",
    )
    require(
        cases["payload_limit_plus"]["payload_bytes"] == above,
        "payload_limit_plus byte count mismatch",
    )
    require(limits.max_payload_bytes - below < 3, "payload_limit_minus is not near the limit")
    require(above - limits.max_payload_bytes <= 3, "payload_limit_plus is not near the limit")


def validate_manifest(
    manifest: dict[str, Any],
    fixture_path: Path,
    truncation_path: Path,
    boundary_path: Path,
    db_profile_path: Path,
) -> None:
    hashes = manifest.get("hashes", {})
    require(hashes.get("fixture_sha256") == sha256_path(fixture_path), "fixture hash mismatch")
    require(
        hashes.get("truncation_fixture_sha256") == sha256_path(truncation_path),
        "truncation fixture hash mismatch",
    )
    require(
        hashes.get("boundary_fixture_sha256") == sha256_path(boundary_path),
        "boundary hash mismatch",
    )
    require(
        hashes.get("db_profile_sha256") == sha256_path(db_profile_path),
        "DB profile hash mismatch",
    )


def build_parser(repo_root: Path) -> argparse.ArgumentParser:
    data_dir = repo_root / "load-tests/k6/data"
    parser = argparse.ArgumentParser(description="Validate enriched text fixtures")
    parser.add_argument(
        "--db-profile",
        type=Path,
        default=data_dir / "batch-embedding-db-profile.json",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=data_dir / "batch-embedding-enriched-texts.json",
    )
    parser.add_argument(
        "--boundary",
        type=Path,
        default=data_dir / "batch-embedding-boundary-inputs.json",
    )
    parser.add_argument(
        "--truncation",
        type=Path,
        default=data_dir / "batch-embedding-truncation-inputs.json",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=data_dir / "batch-embedding-enriched-texts.manifest.json",
    )
    parser.add_argument("--tokenizer-path", required=True)
    return parser


def main() -> None:
    script_path = Path(__file__).resolve()
    repo_root = script_path.parents[4] if len(script_path.parents) > 4 else Path.cwd()
    arguments = build_parser(repo_root).parse_args()
    db_profile = json.loads(arguments.db_profile.read_text(encoding="utf-8"))
    limits = read_limits(db_profile)
    token_counter = HuggingFaceTokenCounter(
        arguments.tokenizer_path, limits.effective_token_limit
    )
    fixture = json.loads(arguments.fixture.read_text(encoding="utf-8"))
    truncation = json.loads(arguments.truncation.read_text(encoding="utf-8"))
    boundary = json.loads(arguments.boundary.read_text(encoding="utf-8"))
    manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
    summary = validate_fixture(fixture, db_profile, token_counter)
    truncation_summary = validate_truncation_fixture(
        truncation, db_profile, token_counter
    )
    validate_boundary(boundary, db_profile)
    validate_manifest(
        manifest,
        arguments.fixture,
        arguments.truncation,
        arguments.boundary,
        arguments.db_profile,
    )
    print(
        json.dumps(
            {
                "status": "valid",
                "capacity_fixture": summary,
                "truncation_fixture": truncation_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
