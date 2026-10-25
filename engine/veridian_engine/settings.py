import functools
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
    """Parse "client_a=tok1,client_b=tok2" into {token: project_id}."""
    tokens: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        project_id, sep, token = pair.partition("=")
        if not sep or not project_id.strip() or not token.strip():
            raise ValueError(f"Invalid VERIDIAN_TOKENS entry: {pair!r}")
        tokens[token.strip()] = project_id.strip()
    return tokens


def parse_panel_tokens(single: str, many: str) -> tuple[str, ...]:
    """VERIDIAN_PANEL_TOKEN plus the comma list VERIDIAN_PANEL_TOKENS, without blanks or repeats."""
    tokens = [single.strip()] + [token.strip() for token in many.split(",")]
    return tuple(dict.fromkeys(token for token in tokens if token))


def parse_emails(raw: str) -> frozenset[str]:
    """Comma-separated emails, compared lowercase."""
    return frozenset(email.strip().lower() for email in raw.split(",") if email.strip())


@dataclass
class Settings:
    db_path: str
    # token -> project_id. Only imported at startup as legacy API keys; auth reads api_key.
    tokens: dict[str, str]
    routes_file: str = str(DEFAULT_ROUTES_FILE)
    learn_delay_min: int = 10
    # A session spikes above max(rate_factor x the user's usual max per minute, rate_floor).
    # Unmeasured starting points: tune them in rehearsals.
    rate_factor: float = 3.0
    rate_floor: int = 30
    clock: Callable[[], datetime] = field(default=utc_now)
    # judge(signals, profile) -> Judgment. None means rule-only: no AI decisions.
    judge: Callable[[dict, dict], Any] | None = None
    # Judge for POST /v1/check, where the caller waits: a faster model. None falls back to `judge`.
    sync_judge: Callable[[dict, dict], Any] | None = None
    # Seconds POST /v1/check waits for the judge on a gray deviation before the fallback decides.
    sync_judge_timeout_s: float = 10.0
    # Failed authentications per source (IP, key prefix) within the window before 429.
    # Unmeasured starting points: tune them against real traffic.
    auth_max_failures_per_ip: int = 30
    auth_max_failures_per_prefix: int = 10
    auth_failure_window_s: float = 60.0
    # Proxies in front of the engine that append to X-Forwarded-For. 0: use the connection's address.
    trusted_proxies: int = 0
    # Background sender of signed block webhooks (U12). Off unless set: from_env turns it on
    # (VERIDIAN_WEBHOOKS=off disables). Tests inject a fake transport and resolver.
    webhooks: bool = False
    webhook_transport: Any = None  # None: HttpxTransport
    webhook_resolver: Callable[[str, int], list[str]] | None = None  # None: real DNS
    webhook_poll_s: float = 5.0
    # Block alert email (U17), sent by a second worker under the same `webhooks` switch.
    # None: NullMailer, the emails are marked failed ("no email provider configured").
    # from_env picks the Resend mailer when RESEND_API_KEY and VERIDIAN_MAIL_FROM are set.
    mailer: Any = None
    # Base URL of the panel, for the payload's panel_url.
    panel_base_url: str = ""
    # Service credentials of the panel (current and previous, to rotate without a cut). Empty: no panel.
    # The most sensitive secret of the system: never log it, never reuse it as a project key.
    panel_tokens: tuple[str, ...] = ()
    # Verified emails that, on login, become owner of every organization without an owner.
    bootstrap_owners: frozenset[str] = frozenset()
    # Open signup (decision 2, resolved 2026-10-25): any signed-in person creates organizations, up to
    # this many live ones they created. A design default against abuse, not a measured value.
    max_orgs_per_person: int = 3
    # Verified emails with no such limit (the team).
    org_creators: frozenset[str] = frozenset()
    # How long an invitation stays valid. A design default, not a measured value.
    invitation_days: int = 14
    # Automatic route classification (contract 16): the worker that classifies the routes the SDK
    # reports. Off unless set: from_env turns it on (VERIDIAN_AUTO_CLASSIFY=off disables).
    # route_classifier None: openapi.classify (the AI; without ANTHROPIC_API_KEY every route fails).
    auto_classify: bool = False
    route_classifier: Any = None
    classify_poll_s: float = 5.0
    # U13: the retention worker (purge by retention_days, wipe deleted projects) and its interval.
    retention: bool = True
    retention_interval_s: float = 3600.0

    @classmethod
    def from_env(cls) -> "Settings":
        from .resend_mailer import mailer_from_env  # imported here: it needs httpx only when configured

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
            rate_factor=float(os.environ.get("VERIDIAN_RATE_FACTOR", "3")),
            rate_floor=int(os.environ.get("VERIDIAN_RATE_FLOOR", "30")),
            judge=default_judge(),
            sync_judge=default_sync_judge(),
            sync_judge_timeout_s=float(os.environ.get("VERIDIAN_SYNC_JUDGE_TIMEOUT_S", "10")),
            auth_max_failures_per_ip=int(os.environ.get("VERIDIAN_AUTH_MAX_FAILURES_PER_IP", "30")),
            auth_max_failures_per_prefix=int(os.environ.get("VERIDIAN_AUTH_MAX_FAILURES_PER_PREFIX", "10")),
            auth_failure_window_s=float(os.environ.get("VERIDIAN_AUTH_FAILURE_WINDOW_S", "60")),
            trusted_proxies=int(os.environ.get("VERIDIAN_TRUSTED_PROXIES", "0")),
            # Resend when RESEND_API_KEY and VERIDIAN_MAIL_FROM are set, else the NullMailer.
            mailer=mailer_from_env(os.environ),
            webhooks=os.environ.get("VERIDIAN_WEBHOOKS", "on").strip().lower() not in ("off", "0", "false", "no"),
            panel_base_url=os.environ.get("VERIDIAN_PANEL_URL", "").strip(),
            panel_tokens=parse_panel_tokens(
                os.environ.get("VERIDIAN_PANEL_TOKEN", ""), os.environ.get("VERIDIAN_PANEL_TOKENS", "")
            ),
            bootstrap_owners=parse_emails(os.environ.get("VERIDIAN_BOOTSTRAP_OWNERS", "")),
            org_creators=parse_emails(os.environ.get("VERIDIAN_ORG_CREATORS", "")),
            max_orgs_per_person=int(os.environ.get("VERIDIAN_MAX_ORGS_PER_PERSON", "3")),
            invitation_days=int(os.environ.get("VERIDIAN_INVITATION_DAYS", "14")),
            auto_classify=os.environ.get("VERIDIAN_AUTO_CLASSIFY", "on").strip().lower()
            not in ("off", "0", "false", "no"),
            retention=os.environ.get("VERIDIAN_RETENTION", "on").strip().lower() not in ("off", "0", "false", "no"),
            retention_interval_s=float(os.environ.get("VERIDIAN_RETENTION_INTERVAL_S", "3600")),
        )


def default_judge() -> Callable[[dict, dict], Any] | None:
    """The AI judge when ANTHROPIC_API_KEY is set (VERIDIAN_MODEL picks the model), else None."""
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return None
    from .ai.judge import judge

    return judge


def default_sync_judge() -> Callable[[dict, dict], Any] | None:
    """The AI judge on VERIDIAN_SYNC_MODEL (default Haiku) when ANTHROPIC_API_KEY is set, else None.

    Measured on the demo's gray cases (10 runs each): the same decisions as Opus in ~2 s instead of
    up to ~12 s, which crossed the 10 s budget and let the fallback allow the request.
    """
    base = default_judge()
    if base is None:
        return None
    return functools.partial(base, model=os.environ.get("VERIDIAN_SYNC_MODEL", "claude-haiku-5-5"))
