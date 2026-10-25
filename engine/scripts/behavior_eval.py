"""Behavior evaluation against a running engine with its real AI (HANDOFF, Dev B task 7).

Standard library only. Creates a throwaway organization and project through the panel
credential, then plays legitimate-but-unusual uses and attacks, each with a fresh user that
has 14 days of history. Every case runs --runs times. For each run it records the decision,
whether the session or IP ends up actually blocked (blocklist, or 403 on POST /v1/check), and
how long the decision took. Prints a Markdown table and writes the raw results as JSON.

    py -3.12 engine/scripts/behavior_eval.py [--runs 3] [--wait 45] [--only L1,A2] [--out results.json]

Needs VERIDIAN_BASE_URL and VERIDIAN_PANEL_TOKEN (engine/.env or the environment). It calls
the AI of that engine: each run costs a few model calls. The project is deleted at the end.
"""

import argparse
import json
import os
import secrets
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from engine_client import load_dotenv

TIMEOUT_S = 40  # longer than the engine's 10 s judge budget plus its 12 s middleware timeout


def request(base_url, token, method, path, body=None, headers=None):
    """Like engine_client.request, with room for a synchronous AI judgment."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(base_url.rstrip("/") + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")

HOME_IP, HOME_DEVICE = "190.202.14.7", "d_home"
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0"
SEED_ROUTES = [("POST", "/login"), ("GET", "/invoices"), ("GET", "/invoices/{id}")]
BANK = ("POST", "/payouts/bank-account")


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Run:
    """One run of one case: a fresh user, its history, then the probe."""

    case: str
    index: int
    tag: str = field(default_factory=lambda: secrets.token_hex(3))
    events: list = field(default_factory=list)

    @property
    def user(self) -> str:
        return f"u_{self.case.lower()}_{self.tag}"

    def event(self, ts, route=("GET", "/invoices"), ip=HOME_IP, device=HOME_DEVICE, session=None, agent=BROWSER):
        self.events.append(None)
        return {"id": f"ev_{self.tag}_{len(self.events)}", "ts": iso(ts), "user_id": self.user,
                "session_id": session or f"s_{self.tag}", "ip": ip, "user_agent": agent, "device_id": device,
                "method": route[0], "route": route[1], "status": 200, "bytes": 800}

    def history(self, now: datetime, hours: list[int]) -> list[dict]:
        """14 days, one session per day and hour, from home with the home device."""
        out = []
        for day in range(1, 15):
            for hour in hours:
                start = (now - timedelta(days=day)).replace(hour=hour, minute=5, second=0, microsecond=0)
                session = f"s_h_{self.tag}_{day}_{hour}"
                for step, route in enumerate(SEED_ROUTES):
                    out.append(self.event(start + timedelta(minutes=step * 3), route, session=session))
        return out


def usual_hours(now: datetime) -> list[int]:
    return sorted({(now.hour + d) % 24 for d in (-1, 0, 1)})


def other_hours(now: datetime) -> list[int]:
    return sorted({(now.hour + d) % 24 for d in (-8, -7, -6)})


# --- the cases -------------------------------------------------------------------------------
# Each returns (events to ingest, critical requests to check). `expect` is "pass" or "block".

def travel(r, now):  # owner abroad: other network, own device, reads invoices
    s = f"s_trip_{r.tag}"
    return [r.event(now, ("POST", "/login"), ip="181.55.20.14", session=s),
            r.event(now + timedelta(seconds=20), ip="181.55.20.14", session=s),
            r.event(now + timedelta(seconds=40), ("GET", "/invoices/{id}"), ip="181.55.20.14", session=s)], []


def travel_bank(r, now):  # owner abroad changes the payout account (first time)
    s = f"s_tripb_{r.tag}"
    return [r.event(now, ("POST", "/login"), ip="181.55.20.14", session=s)], \
        [r.event(now + timedelta(seconds=30), BANK, ip="181.55.20.14", session=s)]


def new_device(r, now):  # owner at home on a new laptop
    s = f"s_nd_{r.tag}"
    return [r.event(now, ("POST", "/login"), device="d_new_laptop", session=s),
            r.event(now + timedelta(seconds=20), device="d_new_laptop", session=s)], []


def new_device_bank(r, now):  # owner at home, new laptop, changes the payout account
    s = f"s_ndb_{r.tag}"
    return [r.event(now, ("POST", "/login"), device="d_new_laptop", session=s)], \
        [r.event(now + timedelta(seconds=30), BANK, device="d_new_laptop", session=s)]


def night(r, now):  # owner at home with their device, hours never seen before
    s = f"s_night_{r.tag}"
    return [r.event(now, ("POST", "/login"), session=s), r.event(now + timedelta(seconds=20), session=s),
            r.event(now + timedelta(seconds=40), ("GET", "/invoices/{id}"), session=s)], []


def vpn(r, now):  # owner behind a commercial VPN (datacenter IP), own device
    s = f"s_vpn_{r.tag}"
    return [r.event(now, ("POST", "/login"), ip="45.83.91.20", session=s),
            r.event(now + timedelta(seconds=20), ip="45.83.91.20", session=s)], []


def rotating_ip(r, now):  # CGNAT: the owner's IP rotates across three networks in minutes
    s = f"s_rot_{r.tag}"
    ips = ["38.121.211.40", "186.167.82.140", "186.167.53.9"]
    return [r.event(now + timedelta(seconds=40 * i), route, ip=ip, session=s)
            for i, (ip, route) in enumerate(zip(ips, [("POST", "/login"), ("GET", "/invoices"),
                                                       ("GET", "/invoices/{id}")]))], []


ATT_IP, ATT_DEVICE = "203.0.113.45", "d_attacker"


def attacker_bank(r, now):  # someone else, other network and device, straight to the bank account
    s = f"s_att_{r.tag}"
    return [r.event(now, ("POST", "/login"), ip=ATT_IP, device=ATT_DEVICE, session=s)], \
        [r.event(now + timedelta(seconds=10), BANK, ip=ATT_IP, device=ATT_DEVICE, session=s)]


def bulk_download(r, now):  # someone else downloading every invoice in a minute
    s = f"s_bulk_{r.tag}"
    events = [r.event(now, ("POST", "/login"), ip=ATT_IP, device=ATT_DEVICE, session=s)]
    events += [r.event(now + timedelta(seconds=1 + i), ("GET", "/invoices/{id}"), ip=ATT_IP, device=ATT_DEVICE,
                       session=s) for i in range(45)]
    events.append(r.event(now + timedelta(seconds=50), ("GET", "/invoices/export"), ip=ATT_IP, device=ATT_DEVICE,
                          session=s))
    return events, []


def slow_theft(r, now):  # someone else, a few reads spread over minutes, ending in an export
    s = f"s_slow_{r.tag}"
    start = now - timedelta(minutes=8)  # inside the learn delay: nothing of it is learned yet
    events = [r.event(start, ("POST", "/login"), ip=ATT_IP, device=ATT_DEVICE, session=s)]
    events += [r.event(start + timedelta(minutes=1 + i), ("GET", "/invoices/{id}"), ip=ATT_IP, device=ATT_DEVICE,
                       session=s) for i in range(6)]
    events.append(r.event(now, ("GET", "/invoices/export"), ip=ATT_IP, device=ATT_DEVICE, session=s))
    return events, []


def owner_network_script(r, now):  # a script on the owner's own network and laptop, fast
    s = f"s_script_{r.tag}"
    return [r.event(now + timedelta(milliseconds=800 * i), ("GET", "/invoices/{id}"), session=s,
                    agent="python-requests/2.32") for i in range(60)], []


def stolen_session(r, now):  # the owner's live session replayed from another network and device
    s = f"s_live_{r.tag}"
    owner = [r.event(now - timedelta(minutes=2), ("POST", "/login"), session=s),
             r.event(now - timedelta(minutes=1), session=s)]
    thief = [r.event(now, ip=ATT_IP, device=ATT_DEVICE, session=s)]
    return owner + thief, [r.event(now + timedelta(seconds=20), BANK, ip=ATT_IP, device=ATT_DEVICE, session=s)]


CASES = {
    "L1": ("Viaje: otra red, su dispositivo, mira facturas", "pass", travel, usual_hours),
    "L1b": ("Viaje: cambia la cuenta bancaria (primera vez)", "pass", travel_bank, usual_hours),
    "L2": ("Dispositivo nuevo en casa, mira facturas", "pass", new_device, usual_hours),
    "L2b": ("Dispositivo nuevo en casa, cambia la cuenta", "pass", new_device_bank, usual_hours),
    "L3": ("De noche: red y dispositivo de siempre, hora nunca vista", "pass", night, other_hours),
    "L4": ("VPN: IP de centro de datos, su dispositivo", "pass", vpn, usual_hours),
    "L5": ("IP que rota entre 3 redes en minutos", "pass", rotating_ip, usual_hours),
    "A1": ("Atacante: otra red y dispositivo, va a la cuenta bancaria", "block", attacker_bank, usual_hours),
    "A2": ("Atacante: descarga masiva (46 pedidos en 1 min)", "block", bulk_download, usual_hours),
    "A3": ("Atacante: robo lento (8 pedidos en 8 min, termina exportando)", "block", slow_theft, usual_hours),
    "A4": ("Script en la red y laptop del dueño (60 pedidos/min)", "block", owner_network_script, usual_hours),
    "A5": ("Sesión robada: la sesión viva del dueño desde otra red", "block", stolen_session, usual_hours),
}


# --- running -----------------------------------------------------------------------------------

class Engine:
    def __init__(self, base_url: str, panel_token: str):
        self.base_url, self.panel_token = base_url, panel_token
        self.actor = self.project = self.sdk = None

    def panel(self, method, path, body=None, project=False):
        headers = {"X-Veridian-Actor": self.actor} if self.actor else {}
        if project:
            headers["X-Veridian-Project"] = self.project
        return request(self.base_url, self.panel_token, method, path, body, headers)

    def setup(self):
        tag = secrets.token_hex(3)
        status, body = request(self.base_url, self.panel_token, "POST", "/v1/people/login",
                               {"google_sub": f"veridian-eval-{tag}", "email": f"eval-{tag}@veridian.test",
                                "name": "Evaluación"})
        assert status == 200, f"login {status}"
        self.actor = body["person"]["id"]
        status, org = self.panel("POST", "/v1/orgs", {"name": f"Evaluación {tag}"})
        assert status == 201, f"org {status} {org}"
        status, project = self.panel("POST", f"/v1/orgs/{org['id']}/projects", {"name": "eval"})
        assert status == 201, f"project {status}"
        self.project = project["id"]
        self.panel("PATCH", f"/v1/projects/{self.project}", {"enforcement": "enforce"})
        status, key = self.panel("POST", f"/v1/projects/{self.project}/api-keys", {"name": "eval"})
        self.sdk = key["key"]

    def teardown(self):
        self.panel("DELETE", f"/v1/projects/{self.project}/data")
        self.panel("DELETE", f"/v1/projects/{self.project}")

    def ingest(self, events):
        for start in range(0, len(events), 500):
            status, body = request(self.base_url, self.sdk, "POST", "/v1/events", {"events": events[start:start + 500]})
            assert status == 202, f"events {status} {body}"

    def check(self, event):
        t0 = time.monotonic()
        status, body = request(self.base_url, self.sdk, "POST", "/v1/check", {"event": event})
        return (body or {}).get("verdict", f"http {status}"), time.monotonic() - t0

    def decisions(self, user):
        status, body = self.panel("GET", "/v1/decisions?limit=200", project=True)
        return [d for d in (body or {}).get("decisions", []) if d.get("user_id") == user]

    def blocklist(self):
        status, body = request(self.base_url, self.sdk, "GET", "/v1/blocklist")
        return body or {}


def play(engine: Engine, case: str, index: int, wait_s: float) -> dict:
    now = datetime.now(timezone.utc)
    _, _, build, hours = CASES[case]
    run = Run(case, index)
    engine.ingest(run.history(now, hours(now)))
    events, checks = build(run, now)
    t0 = time.monotonic()
    if events:
        engine.ingest(events)
    verdicts = [engine.check(e) for e in checks]
    sessions = {e["session_id"] for e in events + checks}
    ips = {e["ip"] for e in events + checks}
    # Background judgments land a few seconds later: poll until they settle or time runs out.
    first_decision_s, decisions, stable_since = None, [], None
    while time.monotonic() - t0 < wait_s:
        decisions = engine.decisions(run.user)
        if decisions and first_decision_s is None:
            first_decision_s = time.monotonic() - t0
        if first_decision_s is not None:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since > 12:
                break
        time.sleep(3)
    bl = engine.blocklist()
    blocked_listed = bool(sessions & set(bl.get("sessions", []))) or bool(ips & set(bl.get("ips", [])))
    blocked = blocked_listed or any(v == "block" for v, _ in verdicts)
    kinds = [d["decision"] for d in sorted(decisions, key=lambda d: d.get("ts", ""))]
    return {"case": case, "run": index, "user": run.user, "blocked": blocked, "decisions": kinds,
            "strongest": max(kinds, key=["allow", "alert", "review", "step_up", "block"].index) if kinds else "none",
            "check_verdicts": [v for v, _ in verdicts], "check_s": [round(s, 2) for _, s in verdicts],
            "first_decision_s": round(first_decision_s, 1) if first_decision_s is not None else None,
            "rationale": (sorted(decisions, key=lambda d: d.get("ts", ""))[-1].get("rationale", "")[:240]
                          if decisions else "")}


def table(results: list[dict], runs: int) -> str:
    lines = ["| Caso | Esperado | Bloqueado (corridas) | Decisión más fuerte | Consulta síncrona | Primera decisión |",
             "|---|---|---|---|---|---|"]
    for case, (title, expect, _, _) in CASES.items():
        rows = [r for r in results if r["case"] == case]
        if not rows:
            continue
        blocked = sum(r["blocked"] for r in rows)
        strongest = ", ".join(sorted({r["strongest"] for r in rows}))
        checks = [s for r in rows for s in r["check_s"]]
        firsts = [r["first_decision_s"] for r in rows if r["first_decision_s"] is not None]
        ok = (blocked == 0) if expect == "pass" else (blocked == len(rows))
        lines.append(f"| {'✅' if ok else '❌'} {case} {title} | {'no bloquear' if expect == 'pass' else 'bloquear'} "
                     f"| {blocked}/{len(rows)} | {strongest} "
                     f"| {f'{min(checks):.1f}–{max(checks):.1f} s' if checks else '—'} "
                     f"| {f'{min(firsts):.0f}–{max(firsts):.0f} s' if firsts else '—'} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--wait", type=float, default=45.0, help="seconds to wait for background judgments")
    parser.add_argument("--only", default="", help="comma-separated case ids")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    base_url = os.environ.get("VERIDIAN_BASE_URL", "http://localhost:8000")
    panel_token = os.environ.get("VERIDIAN_PANEL_TOKEN", "")
    if not panel_token:
        parser.error("set VERIDIAN_PANEL_TOKEN")
    cases = [c for c in CASES if not args.only or c in args.only.split(",")]
    engine = Engine(base_url, panel_token)
    engine.setup()
    try:
        jobs = [(case, i) for case in cases for i in range(args.runs)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda job: play(engine, job[0], job[1], args.wait), jobs))
    finally:
        engine.teardown()
    print(table(results, args.runs))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(results, handle, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
