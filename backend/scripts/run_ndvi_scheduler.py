#!/usr/bin/env python
"""
Standalone CLI runner for the AgroSat NDVI scheduler.

Run from the backend directory:
    python scripts/run_ndvi_scheduler.py

This process starts only APScheduler jobs. It does not start uvicorn
and does not import the FastAPI app.
"""

import logging
import signal
import sys
import time
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ndvi-scheduler")


def main():
    from scheduler import start_scheduler, stop_scheduler

    shutdown_requested = False

    def handle_signal(signum, frame):
        nonlocal shutdown_requested
        if shutdown_requested:
            logger.warning("Second shutdown signal received; forcing exit.")
            sys.exit(1)

        shutdown_requested = True
        logger.info("Shutdown signal received; stopping scheduler...")

    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)

    logger.info("AgroSat NDVI Scheduler starting as standalone process.")
    logger.info("FastAPI web process is not started by this script.")

    try:
        start_scheduler(immediate_run=True)
        logger.info("NDVI Scheduler is running. Press Ctrl+C to stop.")

        while not shutdown_requested:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received; stopping scheduler.")

    finally:
        stop_scheduler()
        logger.info("NDVI Scheduler stopped cleanly.")


if __name__ == "__main__":
    main()
