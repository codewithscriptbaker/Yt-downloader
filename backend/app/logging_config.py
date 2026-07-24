import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level.upper())
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":%(message)s}',
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(level.upper())


def log_event(logger: logging.Logger, event: str, **fields) -> None:
    parts = [f'"event":"{event}"']
    for key, value in fields.items():
        if isinstance(value, str):
            safe = value.replace("\\", "\\\\").replace('"', '\\"')
            parts.append(f'"{key}":"{safe}"')
        elif value is None:
            parts.append(f'"{key}":null')
        else:
            parts.append(f'"{key}":{value}')
    logger.info("{" + ",".join(parts) + "}")
