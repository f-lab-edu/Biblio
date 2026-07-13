from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.e2e.lib.config import default_config_path
from scripts.e2e.lib.report import ReportWriter, StepResult, timestamp_for_path, utc_now


SCRIPT_NAMES = [
    "01_video_upload_to_ready.py",
    "02_search.py",
    "03_feedback_delivery.py",
    "04_dataset_generation.py",
    "05_training_release_legacy_reindex.py",
    "06_rollback_recovery.py",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run backend GCP E2E scripts in order.")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--report-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--use-direct-queue-fallback", action="store_true")
    args = parser.parse_args()
    args.config = _validate_path_arg(args.config, name="--config", must_exist=True)
    report_dir = args.report_dir or Path("artifacts/e2e") / timestamp_for_path()
    report_dir = _validate_path_arg(report_dir, name="--report-dir", must_exist=False)
    report = ReportWriter(run_dir=report_dir)
    failed = False
    for script_name in SCRIPT_NAMES:
        result = _run_script(script_name, args, report_dir)
        report.add_step(result)
        report.write()
        if result.status == "FAIL":
            failed = True
            break
    report_path = report.write()
    print(f"run_all_backend_e2e: {'FAIL' if failed else 'PASS'}")
    print(f"  report: {report_path}")
    return 1 if failed else 0


def _run_script(script_name: str, args: argparse.Namespace, report_dir: Path) -> StepResult:
    started_at = utc_now()
    command = _script_command(script_name, args, report_dir)
    completed = subprocess.run(command, capture_output=True, check=False, encoding="utf-8")
    return StepResult(
        name=script_name.removesuffix(".py"),
        status="PASS" if completed.returncode == 0 else "FAIL",
        started_at=started_at,
        finished_at=utc_now(),
        observations={
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        },
        error=None if completed.returncode == 0 else f"{script_name} exited {completed.returncode}",
    )


def _validate_path_arg(value: Path, *, name: str, must_exist: bool) -> Path:
    raw = str(value)
    if raw.startswith("-"):
        raise SystemExit(f"Invalid value for {name}: must not look like a CLI flag ({raw!r})")
    resolved = value.resolve()
    if must_exist and not resolved.is_file():
        raise SystemExit(f"Invalid value for {name}: file not found at {resolved}")
    return resolved


def _script_command(script_name: str, args: argparse.Namespace, report_dir: Path) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / script_name),
        "--config",
        str(args.config),
        "--report-dir",
        str(report_dir),
    ]
    if args.dry_run:
        command.append("--dry-run")
    if script_name == "06_rollback_recovery.py" and args.use_direct_queue_fallback:
        command.append("--use-direct-queue-fallback")
    return command


if __name__ == "__main__":
    raise SystemExit(main())
