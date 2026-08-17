from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from urllib.parse import unquote, urlparse
from uuid import UUID

from infrastructure import CommandRunner, Infrastructure, LoadTestError


@dataclass(frozen=True)
class VideoRunEnvironment:
    gcp_project_id: str
    core_api_url: str
    requester_user_id: str
    app_jwt_secret: str


def resolve_video_run_environment(
    commands: CommandRunner,
    infrastructure: Infrastructure,
    *,
    biblio_project_id: str,
    requested_gcp_project_id: str | None,
) -> VideoRunEnvironment:
    gcp_project_id = (
        requested_gcp_project_id
        or _environment_value("GCP_PROJECT_ID")
        or infrastructure.project_id
    )
    core_api_url = _environment_value("CORE_API_URL") or infrastructure.terraform_output(
        "core_api_url"
    )
    app_jwt_secret = _environment_value("APP_JWT_SECRET")
    requester_user_id = _environment_value("REQUESTER_USER_ID")
    required_secret_suffixes = []
    if app_jwt_secret is None:
        required_secret_suffixes.append("-jwt-secret-key")
    if requester_user_id is None:
        required_secret_suffixes.append("-database-url")
    secret_ids = _discover_secret_ids(
        commands,
        gcp_project_id,
        tuple(required_secret_suffixes),
    )
    if app_jwt_secret is None:
        app_jwt_secret = _access_secret(
            commands,
            gcp_project_id,
            secret_ids["-jwt-secret-key"],
        )
    if requester_user_id is None:
        database_url = _access_secret(
            commands,
            gcp_project_id,
            secret_ids["-database-url"],
        )
        requester_user_id = _project_owner_id(
            infrastructure,
            database_url,
            biblio_project_id,
        )
    return VideoRunEnvironment(
        gcp_project_id=gcp_project_id,
        core_api_url=core_api_url.rstrip("/"),
        requester_user_id=requester_user_id,
        app_jwt_secret=app_jwt_secret,
    )


def _environment_value(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _discover_secret_ids(
    commands: CommandRunner,
    project_id: str,
    required_suffixes: tuple[str, ...],
) -> dict[str, str]:
    if not required_suffixes:
        return {}
    secret_names = commands.output(
        [
            "gcloud",
            "secrets",
            "list",
            f"--project={project_id}",
            "--format=value(name)",
        ]
    ).splitlines()
    secret_ids: dict[str, str] = {}
    for suffix in required_suffixes:
        matches = [
            name.rsplit("/", 1)[-1]
            for name in secret_names
            if name.rsplit("/", 1)[-1].endswith(suffix)
        ]
        if len(matches) != 1:
            raise LoadTestError(
                f"Expected one Secret Manager secret ending with {suffix}; "
                f"found {len(matches)}."
            )
        secret_ids[suffix] = matches[0]
    return secret_ids


def _access_secret(
    commands: CommandRunner,
    project_id: str,
    secret_id: str,
) -> str:
    value = commands.output(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={secret_id}",
            f"--project={project_id}",
        ]
    )
    if not value:
        raise LoadTestError(f"Secret Manager secret is empty: {secret_id}")
    return value


def _project_owner_id(
    infrastructure: Infrastructure,
    database_url: str,
    project_id: str,
) -> str:
    database_host, database_name = _database_location(database_url)
    try:
        normalized_project_id = str(UUID(project_id))
    except ValueError as error:
        raise LoadTestError("Biblio project ID is not a UUID.") from error
    instance_name = infrastructure.instance_name_by_private_ip(database_host)
    instance_zone = infrastructure.instance_zone_by_name(instance_name)
    sql = (
        "SELECT user_id::text FROM project "
        f"WHERE id = '{normalized_project_id}' AND lifecycle_state <> 'DELETING'"
    )
    output = infrastructure.ssh_output(
        instance_name,
        instance_zone,
        "sudo -n -u postgres psql "
        f"--dbname {shlex.quote(database_name)} --no-psqlrc --tuples-only --no-align "
        f"--set ON_ERROR_STOP=1 --command {shlex.quote(sql)}",
    )
    owner_ids = [line.strip() for line in output.splitlines() if line.strip()]
    if len(owner_ids) != 1:
        raise LoadTestError(
            "Could not resolve exactly one active owner for Biblio project "
            f"{normalized_project_id}."
        )
    try:
        return str(UUID(owner_ids[0]))
    except ValueError as error:
        raise LoadTestError("Biblio project owner ID is not a UUID.") from error


def _database_location(database_url: str) -> tuple[str, str]:
    parsed = urlparse(database_url)
    database_name = unquote(parsed.path.lstrip("/"))
    if parsed.hostname is None or not database_name:
        raise LoadTestError("Database URL does not contain a host and database name.")
    return parsed.hostname, database_name
