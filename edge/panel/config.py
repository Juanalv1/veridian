"""Panel settings, read from the environment (and edge/.env in local development)."""

import os
from dataclasses import dataclass
from pathlib import Path

# edge/.env holds local credentials and is git-ignored. Tests point this elsewhere (tests/conftest.py).
DOTENV_PATH: Path = Path(__file__).resolve().parents[1] / ".env"


def load_dotenv(path: Path) -> None:
    """Local dev: load KEY=VALUE pairs from `path` without overriding variables already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    google_client_id: str = ""
    google_client_secret: str = ""
    session_secret: str = ""
    public_url: str = ""  # behind Render's proxy: builds the OAuth redirect URI and is the only accepted Origin
    engine_url: str = "http://localhost:8000"
    panel_token: str = ""  # service credential the panel presents to the engine

    @property
    def configured(self) -> bool:
        """Without all of these the panel serves nothing: there is no open mode."""
        return bool(self.google_client_id and self.google_client_secret and self.session_secret and self.panel_token)

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(DOTENV_PATH)
        env = os.environ.get
        return cls(
            google_client_id=env("GOOGLE_CLIENT_ID", ""),
            google_client_secret=env("GOOGLE_CLIENT_SECRET", ""),
            session_secret=env("SESSION_SECRET", ""),
            public_url=env("PUBLIC_URL", "").rstrip("/"),
            engine_url=env("VERIDIAN_URL", "http://localhost:8000").rstrip("/"),
            panel_token=env("VERIDIAN_PANEL_TOKEN", ""),
        )
