from __future__ import annotations

import csv
import shlex
from dataclasses import dataclass
from io import StringIO

from scripts.e2e.lib.config import E2EConfig
from scripts.e2e.lib.gcloud import run_command


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, str]]


class PostgresClient:
    def __init__(self, config: E2EConfig) -> None:
        self._config = config

    def build_command(self, sql: str) -> list[str]:
        postgres = self._config.get("postgres", {})
        instance_name = self._required_postgres_value(postgres, "instance_name")
        zone = self._required_postgres_value(postgres, "zone")
        database = self._required_postgres_value(postgres, "database")
        remote_psql = self._required_postgres_value(postgres, "remote_psql_command")
        remote_command = _remote_psql_command(remote_psql, database, sql)
        return [
            "gcloud",
            "compute",
            "ssh",
            instance_name,
            f"--project={self._config.gcp_project_id}",
            f"--zone={zone}",
            "--tunnel-through-iap",
            "--command",
            remote_command,
        ]

    def fetch_csv(self, sql: str) -> QueryResult:
        result = run_command(self.build_command(sql))
        return parse_csv(result.stdout)

    def execute(self, sql: str) -> None:
        run_command(self.build_command(sql))

    @staticmethod
    def _required_postgres_value(postgres: object, key: str) -> str:
        if not isinstance(postgres, dict) or not isinstance(postgres.get(key), str):
            raise ValueError(f"Missing postgres.{key} config value.")
        return str(postgres[key])


def parse_csv(payload: str) -> QueryResult:
    reader = csv.DictReader(StringIO(payload))
    if reader.fieldnames is None:
        return QueryResult(columns=[], rows=[])
    return QueryResult(columns=list(reader.fieldnames), rows=[dict(row) for row in reader])


def _remote_psql_command(remote_psql: str, database: str, sql: str) -> str:
    quoted_sql = shlex.quote(sql)
    quoted_database = shlex.quote(database)
    return f"{remote_psql} -d {quoted_database} -X --csv -v ON_ERROR_STOP=1 -c {quoted_sql}"
