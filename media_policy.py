from configparser import ConfigParser
from dataclasses import dataclass


MB = 1_000_000
PUBLIC_UPLOAD_BYTES = 50 * MB
LOCAL_UPLOAD_BYTES = 2000 * MB
PHOTO_UPLOAD_BYTES = 10 * MB


class MediaLimitError(ValueError):
    """A download or upload exceeds the configured media policy."""


@dataclass(frozen=True)
class MediaPolicy:
    local: bool = False
    max_upload_bytes: int | None = None
    max_download_bytes: int | None = None
    max_duration_seconds: int | None = None
    upload_timeout: int | None = None

    @classmethod
    def from_config(cls, config: ConfigParser) -> "MediaPolicy":
        mode = config.get("telegram", "mode", fallback="public").strip().lower()
        if mode not in {"public", "local"}:
            raise ValueError("[telegram] mode must be public or local")

        def optional_limit(section, key, unit=1):
            raw = config.get(section, key, fallback="").strip()
            if not raw:
                return None
            if not raw.isascii() or not raw.isdecimal() or int(raw) <= 0:
                raise ValueError(f"[{section}] {key} must be a positive whole number")
            return int(raw) * unit

        return cls(
            local=mode == "local",
            max_upload_bytes=optional_limit("telegram", "max_upload_mb", MB),
            max_download_bytes=optional_limit("downloads", "max_size_mb", MB),
            max_duration_seconds=optional_limit("downloads", "max_duration_minutes", 60),
            upload_timeout=optional_limit("telegram", "upload_timeout"),
        )

    def upload_limit(self, media_type: str = "video") -> int:
        api_limit = LOCAL_UPLOAD_BYTES if self.local else PUBLIC_UPLOAD_BYTES
        if media_type == "photo":
            api_limit = PHOTO_UPLOAD_BYTES
        return min(api_limit, self.max_upload_bytes or api_limit)

    def download_limit(self, public_default: int = PUBLIC_UPLOAD_BYTES) -> int:
        return self.max_download_bytes or (LOCAL_UPLOAD_BYTES if self.local else public_default)

    def duration_limit(self, default: int | None = None) -> int | None:
        return self.max_duration_seconds or default

    @property
    def timeout(self) -> int:
        return self.upload_timeout or (900 if self.local else 120)
