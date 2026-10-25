"""Small Spanish text helpers shared by the panel modules."""

from datetime import datetime, timezone


def count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def seconds_text(secs: float) -> str:
    """Seconds as short Spanish text: "2 s", "2,5 s", "45 s", "2 min 5 s"."""
    if secs < 10 and round(secs, 1) < 10:
        text = f"{secs:.1f}".replace(".", ",")
        return text.removesuffix(",0") + " s"
    whole = round(secs)
    if whole < 60:
        return f"{whole} s"
    minutes, rest = divmod(whole, 60)
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"


MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def parse_ts(value) -> datetime | None:
    """An engine timestamp (ISO 8601, `Z` or offset) as an aware datetime; None when it is not one."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ago(value, now) -> str:
    """"hace 5 min", "hace 3 h", "hace 41 días"; "" when `value` is not a timestamp."""
    dt = parse_ts(value)
    if dt is None:
        return ""
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 60:
        return "hace unos segundos"
    if secs < 3600:
        return f"hace {secs // 60} min"
    if secs < 86400:
        return f"hace {secs // 3600} h"
    days = secs // 86400
    return f"hace {days} {'día' if days == 1 else 'días'}"


def expires_in(value, now) -> str:
    """"vence en 13 días" (rounded up), "vence hoy", or "vencida"."""
    dt = parse_ts(value)
    if dt is None:
        return ""
    secs = (dt - now).total_seconds()
    if secs <= 0:
        return "vencida"
    days = -(-int(secs) // 86400)
    return "vence hoy" if secs < 86400 else f"vence en {days} {'día' if days == 1 else 'días'}"


def short_date(value) -> str:
    """"25 oct" (UTC); "" when `value` is not a timestamp."""
    dt = parse_ts(value)
    return f"{dt.day} {MONTHS[dt.month - 1]}" if dt else ""


def stamp(value) -> str:
    """"25 oct 09:14 UTC"; "" when `value` is not a timestamp."""
    dt = parse_ts(value)
    return f"{dt.day} {MONTHS[dt.month - 1]} {dt:%H:%M} UTC" if dt else ""
