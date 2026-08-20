from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import httpx
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from raspberry.collectors.factory import build_collectors
from raspberry.outbox import Outbox
from shared.models import UploadBatch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("collector")


class CollectorService:
    def __init__(self, config: dict):
        self.config = config
        self.collectors = build_collectors(config["collectors"])
        self.outbox = Outbox(config["outbox_db"])
        self.lock = asyncio.Lock()

    async def run_cycle(self) -> None:
        if self.lock.locked():
            logger.warning("Previous collection cycle is still running; skipping")
            return
        async with self.lock:
            results = await asyncio.gather(
                *(collector.collect() for collector in self.collectors),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception):
                    logger.error("Collector failed: %s",
                                 result,
                                 exc_info=(type(result), result, result.__traceback__),
                                )
                    continue
                records = result if isinstance(result, list) else [result]
                for record in records:
                    self.outbox.add(record)
                    logger.debug("Collected %s record %s",
                                record.system,
                                record.record_id,
                               )
            await self.flush_outbox()

    async def flush_outbox(self) -> None:
        records = self.outbox.pending()
        if not records:
            return
        server = self.config["server"]
        batch = UploadBatch(device=self.config["device"], records=records)
        headers = {"X-API-Key": server["api_key"]}
        try:
            async with httpx.AsyncClient(
                timeout=server.get("timeout_seconds", 30),
                verify=server.get("verify_tls", True),
            ) as client:
                response = await client.post(
                    server["upload_url"],
                    content=batch.model_dump_json(),
                    headers={**headers, "Content-Type": "application/json"},
                )
                response.raise_for_status()
            self.outbox.delete([str(record.record_id) for record in records])
            logger.info("Uploaded %d queued records", len(records))
        except Exception:
            logger.exception("Upload failed; records remain in outbox")


async def async_main(config_path: str, once: bool) -> None:
    with Path(config_path).open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    service = CollectorService(config)
    if once:
        await service.run_cycle()
        return

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
    service.run_cycle,
    "cron",
    minute="2,17,32,47",
    second=0,
    max_instances=1,
    coalesce=True,
    )

    scheduler.start()
    if config.get("run_immediately", True):
        await service.run_cycle()
    await asyncio.Event().wait()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="raspberry/config.yaml")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    asyncio.run(async_main(args.config, args.once))


if __name__ == "__main__":
    main()
