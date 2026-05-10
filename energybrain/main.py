"""EnergyBrain entry point.

Usage:
    python -m energybrain.main

Environment variables are loaded from .env in the working directory.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from energybrain.config import ConfigError, load_config
from energybrain.orchestrator.orchestrator import Orchestrator
from energybrain.persistence.database import DatabaseManager
from energybrain.utils.logging_config import get_logger, setup_logging

logger = get_logger(__name__)

_shutdown_event = asyncio.Event()


def _handle_signal(sig: signal.Signals) -> None:
    logger.info("shutdown_signal_received", signal=sig.name)
    _shutdown_event.set()


async def _run() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[energybrain] Configuration error: {exc}", file=sys.stderr)
        return 1

    setup_logging(config.log_level)
    logger.info("energybrain_starting", db=str(config.db_path), log_level=config.log_level)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            # Windows does not support add_signal_handler for all signals
            pass

    db = DatabaseManager(config.db_path)
    try:
        await db.initialize()
    except Exception as exc:
        logger.error("db_init_failed", error=str(exc))
        return 1

    orchestrator = Orchestrator(config, db)

    orchestrator_task = asyncio.create_task(orchestrator.start())
    shutdown_task = asyncio.create_task(_shutdown_event.wait())

    done, pending = await asyncio.wait(
        {orchestrator_task, shutdown_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    if orchestrator_task in done and not orchestrator_task.cancelled():
        exc = orchestrator_task.exception()
        if exc:
            logger.error("orchestrator_crashed", error=str(exc))
            return 1

    logger.info("energybrain_stopped")
    try:
        await db.close()
    except Exception:
        pass

    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(_run())
    except KeyboardInterrupt:
        exit_code = 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
