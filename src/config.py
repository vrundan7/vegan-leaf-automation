"""Runtime configuration. Secrets come only from environment variables."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONTENT_FILE = ROOT / "content" / "posts.json"
STATE_FILE = ROOT / "state" / "state.json"
FONTS_DIR = ROOT / "assets" / "fonts"
TIMEZONE = ZoneInfo("Europe/Berlin")

ALLOWED_HOSTS = {"graph.facebook.com", "graph.instagram.com"}


class ConfigError(RuntimeError):
    """Configuration is incomplete or invalid. Never contains secret values."""


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader for local development (does not override real env vars)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    ig_user_id: str
    ig_access_token: str
    graph_host: str
    api_version: str
    pages_base_url: str
    publish_enabled: bool
    max_caption_hashtags: int

    @property
    def has_credentials(self) -> bool:
        return bool(self.ig_user_id and self.ig_access_token)

    def __repr__(self) -> str:  # never leak the token via repr/logging
        return (f"Settings(ig_user_id={'set' if self.ig_user_id else 'missing'}, token="
                f"{'set' if self.ig_access_token else 'missing'}, host={self.graph_host}, "
                f"pages_base_url={self.pages_base_url!r}, publish_enabled={self.publish_enabled})")

    def require_live(self) -> None:
        """Raise ConfigError unless everything needed for real publishing is present."""
        problems = []
        if not self.publish_enabled:
            problems.append('PUBLISH_ENABLED is not "true"')
        if not self.ig_user_id:
            problems.append("IG_USER_ID is missing")
        elif not re.fullmatch(r"\d{5,25}", self.ig_user_id):
            problems.append("IG_USER_ID must be the numeric Instagram account ID")
        if not self.ig_access_token:
            problems.append("IG_ACCESS_TOKEN is missing")
        elif len(self.ig_access_token) < 20 or any(c.isspace() for c in self.ig_access_token):
            problems.append("IG_ACCESS_TOKEN looks malformed")
        if self.graph_host not in ALLOWED_HOSTS:
            problems.append(f"IG_GRAPH_HOST must be one of {sorted(ALLOWED_HOSTS)}")
        if not re.fullmatch(r"v\d+\.\d+", self.api_version):
            problems.append("IG_API_VERSION must look like v21.0")
        if not self.pages_base_url.startswith("https://"):
            problems.append("PAGES_BASE_URL must be an https:// URL")
        if problems:
            raise ConfigError("Live publishing refused: " + "; ".join(problems))


def load_settings(use_dotenv: bool = True) -> Settings:
    if use_dotenv:
        _load_dotenv()
    env = os.environ.get
    try:
        max_tags = int(env("MAX_CAPTION_HASHTAGS", "5"))
    except ValueError as exc:
        raise ConfigError("MAX_CAPTION_HASHTAGS must be an integer") from exc
    return Settings(
        ig_user_id=(env("IG_USER_ID") or "").strip(),
        ig_access_token=(env("IG_ACCESS_TOKEN") or "").strip(),
        graph_host=(env("IG_GRAPH_HOST") or "graph.facebook.com").strip(),
        api_version=(env("IG_API_VERSION") or "v21.0").strip(),
        pages_base_url=(env("PAGES_BASE_URL") or "").strip().rstrip("/"),
        publish_enabled=(env("PUBLISH_ENABLED") or "").strip().lower() == "true",
        max_caption_hashtags=max(0, min(max_tags, 30)),
    )
