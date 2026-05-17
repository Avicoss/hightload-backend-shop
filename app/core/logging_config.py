import json
import logging
import sys
from datetime import datetime, timezone

# Стандартные атрибуты LogRecord - исключаем из extra-полей
_BUILTIN_ATTRS = frozenset({
    "args", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "message", "module", "msecs", "msg",
    "name", "pathname", "process", "processName", "relativeCreated",
    "stack_info", "taskName", "thread", "threadName",
})


class JsonFormatter(logging.Formatter):
    """Форматирует записи лога как однострочный JSON - удобно для ELK/Loki."""

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        log: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "file": f"{record.filename}:{record.lineno}",
            "func": record.funcName,
            "message": record.message,
        }
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        # Произвольные поля, переданные через extra={}
        for key, val in record.__dict__.items():
            if key not in _BUILTIN_ATTRS:
                log[key] = val
        return json.dumps(log, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Инициализирует JSON-логирование для всего приложения."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # uvicorn.access шумит при высокой нагрузке - заменяем своим middleware
    logging.getLogger("uvicorn.access").propagate = False
    # sqlalchemy не логирует SQL в INFO - только предупреждения и ошибки
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
