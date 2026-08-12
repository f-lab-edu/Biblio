from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from generate_enriched_text_fixture import (
    build_manifest,
    generate_boundary_fixture,
    generate_fixture,
    generate_truncation_fixture,
    sha256_path,
    write_json,
)
from validate_enriched_text_fixture import (
    FixtureValidationError,
    validate_boundary,
    validate_fixture,
    validate_manifest,
    validate_truncation_fixture,
)


class FakeTokenCounter:
    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "class": "FakeTokenCounter",
            "name_or_path": "test-tokenizer",
            "model_max_length": 8192,
            "effective_max_length": 512,
        }

    def count(self, text: str) -> int:
        return (len(text) // 2) + 2


def db_profile() -> dict[str, Any]:
    return {
        "measured_at": "2026-08-10T00:00:00+09:00",
        "database": {
            "sample_filter": {"embedding_model_version": "bge-m3-base"}
        },
        "live_embedding_endpoint": {
            "max_text_length_chars": 4096,
            "max_texts_per_request": 32,
            "max_payload_bytes": 262144,
        },
        "runtime_limits": {"application_effective_limit": {"tokens": 512}},
        "sample": {"valid_count": 133, "ready_video_count": 33},
        "actual_token_distribution": {
            "distinct_texts": {
                "sample_count": 117,
                "over_512_count": 64,
                "over_512_rate": 0.547,
            }
        },
        "char_count": {
            "min": 136,
            "p25": 966,
            "p50": 1308,
            "p75": 1430,
            "p95": 2071.8,
            "max": 2757,
        },
        "byte_count": {"min": 284, "avg": 2405.5, "max": 3433},
        "whitespace_word_count": {"min": 34, "avg": 278.3, "max": 455},
    }


def normalize(text: str) -> str:
    return " ".join(text.split())


class TestFixtureGeneration(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = db_profile()
        self.token_counter = FakeTokenCounter()

    def test_generates_balanced_valid_fixture(self) -> None:
        fixture = generate_fixture(
            self.profile,
            self.token_counter,
            normalize,
            items_per_combination=2,
        )

        summary = validate_fixture(
            fixture,
            self.profile,
            self.token_counter,
            expected_items_per_combination=2,
        )

        self.assertEqual(summary["text_count"], 50)
        self.assertEqual(summary["truncated_count"], 0)

    def test_generates_separate_truncation_fixture(self) -> None:
        fixture = generate_truncation_fixture(
            self.profile,
            self.token_counter,
            normalize,
            items_per_combination=2,
        )

        summary = validate_truncation_fixture(
            fixture,
            self.profile,
            self.token_counter,
            expected_items_per_combination=2,
        )

        self.assertEqual(summary["text_count"], 20)
        self.assertEqual(summary["truncated_count"], summary["text_count"])

    def test_same_seed_recreates_same_fixture(self) -> None:
        first = generate_fixture(
            self.profile,
            self.token_counter,
            normalize,
            seed=104,
            items_per_combination=2,
        )
        second = generate_fixture(
            self.profile,
            self.token_counter,
            normalize,
            seed=104,
            items_per_combination=2,
        )
        different = generate_fixture(
            self.profile,
            self.token_counter,
            normalize,
            seed=105,
            items_per_combination=2,
        )

        self.assertEqual(first, second)
        self.assertNotEqual(first, different)

    def test_validator_rejects_modified_token_count(self) -> None:
        fixture = generate_fixture(
            self.profile,
            self.token_counter,
            normalize,
            items_per_combination=1,
        )
        modified = copy.deepcopy(fixture)
        modified["texts"][0]["raw_model_token_count"] += 1

        with self.assertRaisesRegex(
            FixtureValidationError, "raw_model_token_count mismatch"
        ):
            validate_fixture(
                modified,
                self.profile,
                self.token_counter,
                expected_items_per_combination=1,
            )

    def test_boundary_fixture_matches_live_limits(self) -> None:
        boundary = generate_boundary_fixture(self.profile)

        validate_boundary(boundary, self.profile)

        cases = {item["case"]: item for item in boundary["cases"]}
        self.assertEqual(cases["payload_limit_plus"]["expected_status"], 413)
        self.assertNotIn("token_dense", cases)

    def test_manifest_hashes_written_files(self) -> None:
        fixture = generate_fixture(
            self.profile,
            self.token_counter,
            normalize,
            items_per_combination=1,
        )
        truncation = generate_truncation_fixture(
            self.profile,
            self.token_counter,
            normalize,
            items_per_combination=1,
        )
        boundary = generate_boundary_fixture(self.profile)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            profile_path = root / "profile.json"
            fixture_path = root / "fixture.json"
            truncation_path = root / "truncation.json"
            boundary_path = root / "boundary.json"
            write_json(profile_path, self.profile)
            write_json(fixture_path, fixture)
            write_json(truncation_path, truncation)
            write_json(boundary_path, boundary)
            manifest = build_manifest(
                self.profile,
                fixture,
                truncation,
                self.token_counter,
                generated_at="2026-08-10T00:00:00+00:00",
                git_sha="test-sha",
                fixture_hash=sha256_path(fixture_path),
                truncation_hash=sha256_path(truncation_path),
                boundary_hash=sha256_path(boundary_path),
                db_profile_hash=sha256_path(profile_path),
            )

            validate_manifest(
                manifest,
                fixture_path,
                truncation_path,
                boundary_path,
                profile_path,
            )
            self.assertEqual(
                set(manifest["distribution"]["bucket_summaries"]),
                {"short", "medium", "long", "xlong", "boundary"},
            )
            serialized = json.dumps(manifest, ensure_ascii=False)
            self.assertNotIn("사용자 원문", serialized)


if __name__ == "__main__":
    unittest.main()
