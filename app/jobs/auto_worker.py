from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger("nexforge.autoworker")


class AutoWorker:
    """Integrated background worker for NexForge AI.

    Polls the SQLite job queue and executes analysis/fix cycles asynchronously,
    reporting stage progress in real time without requiring a second terminal.
    """

    def __init__(self, job_store: Any, poll_interval: float = 1.5, enabled: bool = True) -> None:
        self.job_store = job_store
        self.poll_interval = poll_interval
        self._enabled = enabled
        self._is_busy = False
        self._current_job_id: str | None = None
        self._processed_count = 0
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()

    @property
    def is_enabled(self) -> bool:
        with self._state_lock:
            return self._enabled

    @property
    def is_busy(self) -> bool:
        with self._state_lock:
            return self._is_busy

    @property
    def current_job_id(self) -> str | None:
        with self._state_lock:
            return self._current_job_id

    @property
    def processed_count(self) -> int:
        with self._state_lock:
            return self._processed_count

    def set_enabled(self, enabled: bool) -> bool:
        with self._state_lock:
            self._enabled = bool(enabled)
            logger.info("AutoWorker enabled set to %s", self._enabled)
            return self._enabled

    def toggle(self) -> bool:
        with self._state_lock:
            self._enabled = not self._enabled
            logger.info("AutoWorker toggled to %s", self._enabled)
            return self._enabled

    def get_status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "enabled": self._enabled,
                "is_busy": self._is_busy,
                "current_job_id": self._current_job_id,
                "processed_count": self._processed_count,
            }

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        try:
            from app.config import get_settings

            self.job_store.recover_stale_jobs(get_settings().job_stale_timeout_seconds)
        except Exception as exc:
            logger.warning("Could not recover stale jobs: %s", exc)
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="NexForge-AutoWorker-Thread",
        )
        self._thread.start()
        logger.info("AutoWorker background thread started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            logger.info("AutoWorker background thread stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self.is_enabled:
                time.sleep(self.poll_interval)
                continue

            job = None
            try:
                job = self.job_store.claim_next_job()
            except Exception as exc:
                logger.error("Error claiming next job from queue: %s", exc)
                time.sleep(self.poll_interval)
                continue

            if job is None:
                time.sleep(self.poll_interval)
                continue

            job_id = job["job_id"]
            with self._state_lock:
                self._is_busy = True
                self._current_job_id = job_id

            logger.info("AutoWorker processing job %s", job_id)
            try:
                self.job_store.execute_job_with_progress(job_id, job["payload"])
                with self._state_lock:
                    self._processed_count += 1
                logger.info("AutoWorker successfully completed job %s", job_id)
            except Exception as exc:
                logger.error("AutoWorker error processing job %s: %s", job_id, exc)
            finally:
                with self._state_lock:
                    self._is_busy = False
                    self._current_job_id = None
