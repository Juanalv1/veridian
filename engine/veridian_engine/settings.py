import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_ROUTES_FILE = Path(__file__).resolve().parent.parent / "routes.yaml"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_tokens(raw: str) -> dict[str, str]:
    """Parse "client_a=tok1,client_b=tok2" into {token: client_id}."""
    tokens: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        client_id, sep, token = pair.partition("=")
        if not sep or not client_id.strip() or not token.strip():
            raise ValueError(f"Invalid VERIDIAN_TOKENS entry: {pair!r}")
        tokens[token.strip()] = client_id.strip()
    return tokens


@dataclass
class Settings:
    db_path: str
    tokens: dict[str, str]  # token -> client_id
    routes_file: str = str(DEFAULT_ROUTES_FILE)
    learn_delay_min: int = 10
    clock: Callable[[], datetime] = field(default=utc_now)
    # judge(signals, profile) -> Judgment. None means rule-only: no AI decisions.
    judge: Callable[[dict, dict], Any] | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        tokens = parse_tokens(os.environ.get("VERIDIAN_TOKENS", ""))
        single = os.environ.get("VERIDIAN_TOKEN", "").strip()
        if not tokens and single:
            tokens = {single: "default"}
        if not tokens:
            raise ValueError("Set VERIDIAN_TOKENS or VERIDIAN_TOKEN before starting the engine")
        return cls(
            db_path=os.environ.get("VERIDIAN_DB", "veridian.db"),
            tokens=tokens,
            routes_file=os.environ.get("VERIDIAN_ROUTES_FILE", str(DEFAULT_ROUTES_FILE)),
            learn_delay_min=int(os.environ.get("VERIDIAN_LEARN_DELAY_MIN", "10")),
            judge=default_judge(),
        )


def default_judge() -> Callable[[dict, dict], Any] | None:
    """The AI judge when ANTHROPIC_API_KEY is set (VERIDIAN_MODEL picks the model), else None."""
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return None
    from .ai.judge import judge

    return judge
