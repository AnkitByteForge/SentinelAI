# Structured (JSON) logging setup — call configure_logging() once per process.
# Replaces the print(...) statements scattered across services/routers with
# real logging: leveled, filterable via LOG_LEVEL, and machine-parseable
# (a JSON line per record) so a log aggregator (or just `| jq`) can filter
# on fields like request_id or provider instead of grepping strings.
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_RESERVED_RECORD_KEYS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()) | {
    "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Pass through anything passed via logger.info(..., extra={...}),
        # e.g. request_id, provider — without clobbering the fields above.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_KEYS and key not in payload:
                payload[key] = value

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Idempotent — safe to call more than once (e.g. app reload in dev)."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)

    # These libraries are noisy at INFO/DEBUG and rarely useful here.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
