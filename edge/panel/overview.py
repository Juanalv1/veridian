"""Data for the Resumen and Usuarios pages, built from what the engine returns.

Numbers come from `GET /v1/summary` and `GET /v1/users` (contracts 6 and 7). While the engine
does not have them yet, what the panel can know honestly is computed from `/v1/activity`,
`/v1/decisions` and `/v1/blocklist` and labelled with the window it covers; what cannot be known
is left out. Nothing here is a sample value.
"""

from datetime import datetime, timedelta, timezone
from statistics import median

from .fmt import count, seconds_text

USER_ROWS_ON_OVERVIEW = 8
STATES = {"blocked": ("Bloqueado", "block"), "normal": ("Normal", "normal")}


def parse_ts(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_day_window(now: datetime, offset_min: int) -> tuple[datetime, datetime]:
    """The viewer's "today" as a UTC range: local midnight to the next one."""
    local = now.astimezone(timezone.utc) + timedelta(minutes=offset_min)
    since = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(minutes=offset_min)
    return since, since + timedelta(days=1)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _hour(utc_hour: datetime, offset_min: int) -> int:
    return (utc_hour + timedelta(minutes=offset_min)).hour


def headline(stopped: int, engine_error) -> str:
    if engine_error:
        return ""
    if stopped == 0:
        return "Todo normal hoy."
    who = "Un robo detenido" if stopped == 1 else f"{stopped} robos detenidos"
    return f"{who} hoy. El resto, uso normal."


def _tiles(summary, activity, decisions, today, open_blocks, blocklist) -> list[dict]:
    tiles = []
    if summary is not None:
        n_users = summary["users"]
        tiles.append({"label": "Accesos vigilados", "value": summary["accesses"],
                      "sub": f"de {count(n_users, 'usuario', 'usuarios')}"})
    elif activity is not None:
        n = len(activity)
        users = len({a.get("user_id") for a in activity})
        window = "en el último acceso" if n == 1 else f"en los últimos {n} accesos"
        tiles.append({"label": "Accesos vigilados", "value": n,
                      "sub": f"{window}, de {count(users, 'usuario', 'usuarios')}"})
    if summary is not None:
        ba = summary["blocks_active"]
        value, sessions, ips = ba["decisions"], ba["sessions"], ba["ips"]
    else:
        value = len(open_blocks)
        sessions = len(blocklist.get("sessions") or []) if blocklist is not None else None
        ips = len(blocklist.get("ips") or []) if blocklist is not None else None
    sub = f"{count(sessions, 'sesión', 'sesiones')} y {count(ips, 'IP', 'IPs')}" if sessions is not None and value > 0 else ""
    tiles.append({"label": "Bloqueos activos", "value": value, "sub": sub, "tone": "block" if value else ""})
    if summary is not None:
        reviewed, seen = summary["reviewed_by_ai"], summary["accesses"]
        if reviewed:
            sub = f"por {seen} {'acceso vigilado' if seen == 1 else 'accesos vigilados'}"
        else:
            sub = "IA desactivada" if today else ""
        tiles.append({"label": "Consultas a la IA", "value": reviewed, "sub": sub})
    secs = []
    for d in today:
        if d.get("decision") != "block":
            continue
        took = _seconds_between(d.get("ts"), (d.get("access") or {}).get("ts"))
        if took is not None:
            secs.append(took)
    if secs:
        tiles.append({"label": "Detección", "value": seconds_text(median(secs)), "sub": "del acceso al bloqueo"})
    return tiles


def _seconds_between(decision_ts, access_ts) -> float | None:
    a, b = parse_ts(decision_ts), parse_ts(access_ts)
    if a is None or b is None or a < b:
        return None
    return (a - b).total_seconds()


def _hour_rows(summary, activity, today) -> tuple[list[dict], str]:
    """[{hour (UTC datetime), accesses, blocks}] and the label of the window they cover."""
    if summary is not None:
        rows = []
        for r in summary["by_hour"]:
            h = parse_ts(r.get("hour"))
            if h is not None and _is_int(r.get("accesses")):
                rows.append({"hour": h, "accesses": r["accesses"], "blocks": r.get("blocks") if _is_int(r.get("blocks")) else 0})
        return rows, "hoy"
    by_hour: dict[datetime, dict] = {}
    for a in activity or []:
        t = parse_ts(a.get("ts"))
        if t is not None:
            h = t.replace(minute=0, second=0, microsecond=0)
            by_hour.setdefault(h, {"hour": h, "accesses": 0, "blocks": 0})["accesses"] += 1
    for d in today:
        t = parse_ts(d.get("ts"))
        if d.get("decision") == "block" and t is not None:
            h = t.replace(minute=0, second=0, microsecond=0)
            if h in by_hour:
                by_hour[h]["blocks"] += 1
    n = len(activity or [])
    return [by_hour[h] for h in sorted(by_hour)], ("en el último acceso" if n == 1 else f"en los últimos {n} accesos")


def _block_hours(today) -> tuple[dict[datetime, int], int]:
    """Block decisions by the UTC hour of the access that triggered them (a block can be created in
    an hour with no accesses at all). Also how many have no access to place."""
    hours: dict[datetime, int] = {}
    unplaced = 0
    for d in today:
        if d.get("decision") != "block":
            continue
        t = parse_ts((d.get("access") or {}).get("ts"))
        if t is None:
            unplaced += 1
            continue
        h = t.replace(minute=0, second=0, microsecond=0)
        hours[h] = hours.get(h, 0) + 1
    return hours, unplaced


def _chart(summary, activity, today, offset_min) -> dict | None:
    if summary is None and activity is None:
        return None
    rows, window = _hour_rows(summary, activity, today)
    placed, unplaced = _block_hours(today)
    by = {r["hour"]: {"hour": r["hour"], "accesses": r["accesses"], "blocks": placed.get(r["hour"], 0) + (r["blocks"] if unplaced else 0)}
          for r in rows}
    for h, n in placed.items():  # a block hour may have no accesses row
        by.setdefault(h, {"hour": h, "accesses": 0, "blocks": n})
    if not rows:
        return {"empty": True, "window": window}
    first, last = min(by), max(by)
    if last - first <= timedelta(hours=23):
        hours = [first + timedelta(hours=i) for i in range(int((last - first).total_seconds() // 3600) + 1)]
    else:
        hours = sorted(by)
    top = max(r["accesses"] for r in by.values()) or 1
    bars = []
    for h in hours:
        r = by.get(h, {"accesses": 0, "blocks": 0})
        label = f"{_hour(h, offset_min):02d}"
        tip = f"{label} h · {count(r['accesses'], 'acceso', 'accesos')}"
        if r["blocks"]:
            tip += f" · {count(r['blocks'], 'bloqueo', 'bloqueos')}"
        bars.append({"label": label, "accesses": r["accesses"], "blocks": r["blocks"], "tip": tip,
                     "pct": f"{r['accesses'] / top * 100:.1f}"})
    peak = max(bars, key=lambda b: b["accesses"])
    aria = (f"Accesos por hora, de {int(bars[0]['label'])} a {int(bars[-1]['label'])} h, {window}. "
            f"Pico de {count(peak['accesses'], 'acceso', 'accesos')} a las {int(peak['label'])} h.")
    blocked = [str(int(b["label"])) for b in bars if b["blocks"]]
    if blocked:
        aria += f" Bloqueos a las {', '.join(blocked)} h."
    return {"empty": False, "bars": bars, "aria": aria, "window": window,
            "axis": [top, (top + 1) // 2, 0]}


def _state(word) -> tuple[str, str]:
    return STATES.get(word, (str(word or "").capitalize() or "Normal", "normal"))


def _latest_href(decisions: list[dict]) -> dict[str, str]:
    """user_id -> link to that user's newest decision (the list is newest first)."""
    out: dict[str, str] = {}
    for d in decisions:
        if d.get("user_id") and d["user_id"] not in out and d.get("href"):
            out[d["user_id"]] = d["href"]
    return out


def user_rows(users, decisions, activity, open_blocks) -> list[dict] | None:
    links = _latest_href(decisions)
    if users is not None:
        rows = []
        for u in users:
            word, kind = _state(u.get("state"))
            days, nets = u.get("history_days"), u.get("networks")
            parts = []
            if _is_int(days):
                parts.append(count(days, "día", "días"))
            if _is_int(nets):
                parts.append(count(nets, "red", "redes"))
            rows.append({"user_id": u.get("user_id", ""), "detail": " · ".join(parts), "word": word, "kind": kind,
                         "href": links.get(u.get("user_id")), "history": days if _is_int(days) else None,
                         "networks": nets if _is_int(nets) else None, "devices": u.get("devices") if _is_int(u.get("devices")) else None,
                         "last_seen": u.get("last_seen"), "open_blocks": u.get("open_blocks") if _is_int(u.get("open_blocks")) else 0})
        return rows
    ids: list[str] = []
    for source in (decisions, activity or []):
        for item in source:
            uid = item.get("user_id")
            if uid and uid not in ids:
                ids.append(uid)
    blocked = {d.get("user_id") for d in open_blocks}
    ids.sort(key=lambda u: u not in blocked)
    return [{"user_id": u, "detail": "", "word": _state("blocked" if u in blocked else "normal")[0],
             "kind": _state("blocked" if u in blocked else "normal")[1], "href": links.get(u)} for u in ids]


def build_overview(*, summary, users, activity, decisions, blocklist, since, until, offset_min, engine_error) -> dict:
    today = [d for d in decisions if (t := parse_ts(d.get("ts"))) is not None and since <= t < until]
    open_blocks = [d for d in decisions if d.get("decision") == "block" and not d.get("marked_normal")]
    stopped = [d for d in today if d.get("decision") == "block" and not d.get("marked_normal")]
    out = {"headline": headline(len(stopped), engine_error), "tiles": [], "chart": None, "blocks": [], "users": None, "more_users": 0}
    if engine_error:
        return out
    out["tiles"] = _tiles(summary, activity, decisions, today, open_blocks, blocklist)
    out["chart"] = _chart(summary, activity, today, offset_min)
    out["blocks"] = open_blocks
    rows = user_rows(users, decisions, activity, open_blocks)
    if rows is not None:
        out["users"] = rows[:USER_ROWS_ON_OVERVIEW]
        out["more_users"] = max(0, len(rows) - USER_ROWS_ON_OVERVIEW)
    return out
