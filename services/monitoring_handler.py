"""
services/monitoring_handler.py

Custom Python logging.Handler that intercepts every logger.info / warning /
error / critical call anywhere in the application and ships the record to the
monitoring API as a structured JSON payload.

Key design decisions
────────────────────
* Runs HTTP in a **background daemon thread** via a queue — the async event
  loop is never blocked, even on slow / unreachable monitoring servers.
* Uses only stdlib (urllib + json + threading + queue) — no extra deps.
* Enriches every record with: module, lineno, funcName, full traceback (for
  exceptions), and an ISO-8601 timestamp.
* Silently swallows all networking errors so the monitoring system can NEVER
  crash the voice agent.
* Filters out its own log records to prevent infinite recursion.
"""

import json
import logging
import queue
import sys
import threading
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Level → string normalisation
# ---------------------------------------------------------------------------
_LEVEL_MAP = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class MonitoringHandler(logging.Handler):
    """
    Logging handler that ships every log record to the monitoring API.

    Usage (install once, covers the entire application):

        from services.monitoring_handler import MonitoringHandler
        logging.getLogger().addHandler(MonitoringHandler())

    Parameters
    ----------
    url : str
        Full URL of the monitoring log endpoint.
        Default: http://192.168.0.230:8570/api/logs
    service_name : str
        Value for the ``service`` field in the payload.
    timeout : int
        Per-request timeout in seconds.
    queue_size : int
        Max pending log records. Oldest records are dropped when full.
    allowed_prefixes : tuple[str, ...] | None
        When set, only records whose ``name`` starts with one of these
        prefixes are forwarded.  Pass ``None`` (default) to forward ALL
        loggers (including livekit / deepgram / aiohttp library logs).
    """

    # Name used internally so we can detect our own records and skip them.
    _HANDLER_LOGGER_NAME = "services.monitoring_handler"

    def __init__(
        self,
        url: str = "http://192.168.0.230:8570/api/logs",
        service_name: str = "AgenticHRVoiceBot",
        timeout: int = 5,
        queue_size: int = 500,
        allowed_prefixes: Optional[tuple] = None,
    ):
        super().__init__()
        self.url = url
        self.service_name = service_name
        self.timeout = timeout
        self.allowed_prefixes = allowed_prefixes

        # Bounded queue; oldest drops if consumer falls behind
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)

        # Background sender thread (daemon = dies when main process exits)
        self._thread = threading.Thread(
            target=self._sender_loop,
            name="MonitoringHandlerSender",
            daemon=True,
        )
        self._thread.start()

    # ------------------------------------------------------------------
    # logging.Handler interface
    # ------------------------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        """Called by the logging framework for every matching log record."""

        # Skip our own log records to prevent infinite recursion
        if record.name == self._HANDLER_LOGGER_NAME:
            return

        # Optional prefix filter
        if self.allowed_prefixes and not record.name.startswith(self.allowed_prefixes):
            return

        # Build payload here (in the calling thread, while record is fresh)
        payload = self._build_payload(record)

        # Non-blocking put; if queue is full just drop (agent > monitoring)
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            pass  # silently drop

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_payload(self, record: logging.LogRecord) -> dict:
        """Convert a LogRecord into the monitoring API payload dict."""

        # Format the message (applies % args)
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()

        # Enrich message with location context
        location = f"[{record.module}.py:{record.lineno} {record.funcName}]"
        full_message = f"{location} {message}"

        # Capture exception traceback if present
        exc_text: Optional[str] = None
        if record.exc_info and record.exc_info[0] is not None:
            exc_text = "".join(traceback.format_exception(*record.exc_info))
            full_message = f"{full_message}\n{exc_text}"

        # ISO-8601 timestamp in UTC
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()

        level_str = _LEVEL_MAP.get(record.levelno, record.levelname)

        return {
            "message": full_message,
            "service": self.service_name,
            "applicationName": self.service_name,
            "environment": "development",
            "level": level_str,
            "module": record.module,
            "lineno": record.lineno,
            "funcName": record.funcName,
            "timestamp": ts,
            "exc_info": exc_text,
        }

    def _sender_loop(self) -> None:
        """
        Runs in daemon thread.  Drains the queue and POSTs each payload.
        Uses urllib (stdlib) — no extra dependencies.
        """
        _local_logger = logging.getLogger(self._HANDLER_LOGGER_NAME)

        while True:
            try:
                payload = self._queue.get()  # blocks until item available

                body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    self.url,
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "AgenticHRVoiceBot/monitoring",
                    },
                    method="POST",
                )

                try:
                    with urllib.request.urlopen(req, timeout=self.timeout):
                        pass  # success — response body not needed
                except urllib.error.URLError:
                    pass  # server unreachable — silently drop
                except Exception:
                    pass  # any other HTTP error — silently drop

                self._queue.task_done()

            except Exception:
                # Last-resort safety net: the sender thread must never crash
                pass


# ---------------------------------------------------------------------------
# Convenience installer
# ---------------------------------------------------------------------------

def install_monitoring_handler(
    url: str = "http://192.168.0.230:8570/api/logs",
    service_name: str = "AgenticHRVoiceBot",
    level: int = logging.DEBUG,
    allowed_prefixes: Optional[tuple] = None,
) -> MonitoringHandler:
    """
    Create a MonitoringHandler and attach it to the **root** logger.

    Call this once at application startup (before any other logging calls)
    and every logger in the process will automatically forward records to
    the monitoring UI.

    Parameters
    ----------
    level : int
        Minimum log level to forward. Default: DEBUG (forwards everything).
    allowed_prefixes : tuple | None
        Filter by logger name prefix.  None = all loggers.
        Example: ``("agent", "services", "main", "controllers", "tools")``
        to skip noisy library loggers (livekit, deepgram, aiohttp, …).

    Returns the installed handler (useful for testing or reconfiguration).
    """
    handler = MonitoringHandler(
        url=url,
        service_name=service_name,
        allowed_prefixes=allowed_prefixes,
    )
    handler.setLevel(level)

    # Plain formatter — the location context is already in the message
    handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.addHandler(handler)

    return handler
