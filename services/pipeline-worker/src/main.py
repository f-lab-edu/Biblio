import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from adapters.queue.broker import BrokerClient
from adapters.queue.consumer import PipelineWorkerConsumer
from config.settings import Settings, get_settings
from utils.logging import configure_logging, get_logger

ConsumerBootstrap = Callable[[Settings], Awaitable[None] | None]


def _default_consumer_bootstrap(settings: Settings) -> None:
    get_logger().bind(
        trace_id="-",
        video_id="-",
        user_id="-",
    ).info(
        "consumer bootstrap ready for broker_type={} worker_concurrency={}",
        settings.broker_type,
        settings.worker_concurrency,
    )


@dataclass(slots=True)
class WorkerApplication:
    settings: Settings
    consumer_bootstrap: ConsumerBootstrap

    async def run(self) -> None:
        get_logger().bind(
            trace_id="-",
            video_id="-",
            user_id="-",
        ).info("pipeline worker starting")
        result = self.consumer_bootstrap(self.settings)
        if inspect.isawaitable(result):
            await result

    async def run_until_complete(self) -> None:
        await self.run()


def build_application(
    *,
    settings: Settings | None = None,
    consumer_bootstrap: ConsumerBootstrap | None = None,
) -> WorkerApplication:
    app_settings = settings or get_settings()
    configure_logging()
    return WorkerApplication(
        settings=app_settings,
        consumer_bootstrap=consumer_bootstrap or _default_consumer_bootstrap,
    )


def build_consumer_bootstrap(
    *,
    broker: BrokerClient,
    consumer: PipelineWorkerConsumer,
    queue_names: list[str],
) -> ConsumerBootstrap:
    async def bootstrap(settings: Settings) -> None:
        await asyncio.gather(*[
            consumer.run_until_empty(broker, queue_names)
            for _ in range(settings.worker_concurrency)
        ])

    return bootstrap


def main() -> None:
    asyncio.run(build_application().run())


if __name__ == "__main__":
    main()
