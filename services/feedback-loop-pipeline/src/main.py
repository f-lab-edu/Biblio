from __future__ import annotations

import argparse
import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from src.bootstrap import create_role_bootstraps
from src.config.settings import Settings, get_settings


RoleBootstrap = Callable[[Settings], Awaitable[None] | None] | Callable[[Settings, bool], Awaitable[None] | None]


@dataclass(slots=True)
class FeedbackLoopApplication:
    settings: Settings
    role_bootstraps: Mapping[str, RoleBootstrap]

    async def run(self, *, run_once: bool = False) -> None:
        bootstrap = self.role_bootstraps.get(self.settings.app_role)
        if bootstrap is None:
            raise ValueError(f"Unsupported APP_ROLE: {self.settings.app_role}")
        result = bootstrap(self.settings, run_once=run_once)
        if inspect.isawaitable(result):
            await result


def build_application(
    *,
    settings: Settings | None = None,
    role_bootstraps: Mapping[str, RoleBootstrap] | None = None,
) -> FeedbackLoopApplication:
    return FeedbackLoopApplication(
        settings=settings or get_settings(),
        role_bootstraps=create_role_bootstraps() if role_bootstraps is None else role_bootstraps,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a feedback-loop-pipeline runtime role.")
    parser.add_argument("--run-once", action="store_true", help="Run the configured APP_ROLE once, then exit.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        asyncio.run(build_application().run(run_once=args.run_once))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
