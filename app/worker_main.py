from __future__ import annotations

import argparse

from app.config import get_settings
from app.jobs.queue import JobStore
from app.jobs.worker import JobQueueWorker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the NexForge AI queue worker")
    parser.add_argument("--once", action="store_true", help="Process a single queued job and exit")
    parser.add_argument("--idle-cycles", type=int, default=None, help="Exit after this many empty polling cycles")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Seconds to sleep between queue polls")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    store = JobStore(settings.database_url)
    worker = JobQueueWorker(store, poll_interval_seconds=args.poll_interval)

    stop_after_idle_cycles = args.idle_cycles
    if args.once:
        stop_after_idle_cycles = 1

    worker.run_forever(stop_after_idle_cycles=stop_after_idle_cycles)


if __name__ == "__main__":
    main()