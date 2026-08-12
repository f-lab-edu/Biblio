from __future__ import annotations

import base64
import json
import shlex
from typing import Any

from embedding_target import EmbeddingTarget
from infrastructure import Infrastructure, Settings
from k6_runner import K6Runner


class SearchTarget(EmbeddingTarget):
    SCENARIO = "scenarios/search-embedding.js"

    def __init__(
        self,
        settings: Settings,
        infrastructure: Infrastructure,
        k6_runner: K6Runner,
    ) -> None:
        super().__init__(
            settings,
            infrastructure,
            k6_runner,
            target_name=infrastructure.search_target_name,
            target_zone=infrastructure.search_target_zone,
            config_keys=(
                "MAX_CONCURRENCY",
                "INFERENCE_THREADS",
                "SEARCH_REQUEST_LIMIT",
                "SEARCH_WAIT_TIMEOUT_SEC",
            ),
        )

    def inspect_scenarios(self) -> None:
        self.inspect_scenario(self.SCENARIO)

    def probe(self, session: dict[str, Any], trace_id: str) -> None:
        query_file = self.settings.load_test_root / "data/search-embedding-inputs.json"
        query_data = json.loads(query_file.read_text(encoding="utf-8"))
        payload = json.dumps(
            {
                "texts": [query_data["queries"][0]["text"]],
                "model_version": session["model_version"],
            },
            separators=(",", ":"),
        )
        encoded_payload = base64.b64encode(payload.encode("utf-8")).decode("ascii")
        command = (
            f"printf %s {shlex.quote(encoded_payload)} | base64 -d | "
            "curl -fsS --max-time 15 -H 'Content-Type: application/json' "
            f"-H {shlex.quote(f'X-Trace-Id: {trace_id}')} --data-binary @- "
            f"{shlex.quote(str(session['target']['url']))} | "
            "jq -e '(.embeddings | length == 1) and "
            "(.embeddings[0] | length > 0)' >/dev/null"
        )
        self.infrastructure.ssh(
            self.infrastructure.runner_name,
            self.infrastructure.runner_zone,
            command,
        )
