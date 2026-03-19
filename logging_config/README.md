# logging_config

Shared structured JSON logging configuration for all DanClaw components.

## Usage

```python
from logging_config import setup_logging

setup_logging()  # Call once at startup
```

Every log line is a JSON object written to stderr with fields:

| Field       | Always present | Description                      |
|-------------|:--------------:|----------------------------------|
| `timestamp` | Yes            | ISO-8601 UTC timestamp           |
| `level`     | Yes            | Log level (DEBUG, INFO, …)       |
| `logger`    | Yes            | Logger name                      |
| `message`   | Yes            | Formatted log message            |
| *(extras)*  | No             | Any extra context fields          |

## Why JSON?

Structured JSON logs integrate seamlessly with `docker logs`, `journalctl`,
and log aggregation tools (ELK, Loki, CloudWatch) — each line is independently
parseable without multi-line buffering.
