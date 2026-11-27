import configparser
import locale
from dataclasses import dataclass
from pathlib import Path


def read_config(path: Path) -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)
    encoded = path.read_bytes()
    try:
        decoded = encoded.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = encoded.decode(locale.getpreferredencoding(False))
    config.read_string(decoded, source=str(path))
    return config


@dataclass(frozen=True)
class RuntimeSettings:
    mode: str
    port: int
    startup_timeout: int
    shutdown_timeout: int

    @property
    def local_endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @classmethod
    def from_config(cls, config):
        mode = config.get("telegram", "mode", fallback="public").strip().lower()
        if mode not in {"public", "local"}:
            raise ValueError("telegram.mode must be public or local")
        port = config.getint("telegram", "local_port", fallback=8081)
        startup = config.getint("telegram", "startup_timeout", fallback=60)
        shutdown = config.getint("telegram", "shutdown_timeout", fallback=45)
        if not (1 <= port <= 65535 and 1 <= startup <= 600 and 1 <= shutdown <= 1800):
            raise ValueError("Invalid local port, startup timeout or shutdown timeout")
        return cls(mode, port, startup, shutdown)
