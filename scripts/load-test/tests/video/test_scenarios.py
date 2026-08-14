from __future__ import annotations

import sys
import unittest
from pathlib import Path


LOAD_TEST_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOAD_TEST_DIR))

from infrastructure import LoadTestError
from video_pipeline.models import ScenarioOverrides
from video_pipeline.scenarios import build_scenario_plan


class TestVideoPipelineScenarios(unittest.TestCase):
    def test_s4_baseline_has_delayed_short_phase(self) -> None:
        plan = build_scenario_plan("s4")

        self.assertTrue(plan.is_baseline)
        self.assertEqual(plan.repeat_count, 3)
        self.assertEqual(plan.requests_per_repeat, 8)
        self.assertEqual(plan.total_requests, 24)
        self.assertEqual(plan.phases[0].fixture, "long")
        self.assertEqual(plan.phases[1].fixture, "short")
        self.assertEqual(plan.phases[1].delay_before_seconds, 10.0)

    def test_overrides_create_exploratory_plan(self) -> None:
        plan = build_scenario_plan(
            "S3",
            ScenarioOverrides(
                repeat_count=2,
                request_count=12,
                concurrency=6,
                fixture="long",
            ),
        )

        self.assertFalse(plan.is_baseline)
        self.assertEqual(plan.repeat_count, 2)
        self.assertEqual(plan.total_requests, 24)
        self.assertEqual(plan.phases[0].request_count, 12)
        self.assertEqual(plan.phases[0].concurrency, 6)
        self.assertEqual(plan.phases[0].fixture, "long")

    def test_rejects_invalid_override(self) -> None:
        with self.assertRaisesRegex(LoadTestError, "cannot exceed"):
            build_scenario_plan(
                "S2",
                ScenarioOverrides(request_count=2, concurrency=3),
            )


if __name__ == "__main__":
    unittest.main()
