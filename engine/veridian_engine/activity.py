import sqlite3

SEVERITY_VERDICT = {0: "normal", 1: "review", 2: "block"}

# One query: the latest events, every decision that could explain each one, and the most severe
# open one per event. Severity: open block 2, open alert/step_up 1, allow or marked normal 0.
# A session's open block also covers its later events (the middleware rejected them with 403),
# counted from the block's triggering access (the latest event behind it, as in contract 3).
ACTIVITY_SQL = """
WITH recent AS (
    SELECT id, ts, user_id, session_id, ip, device_id, method, route
    FROM events WHERE client_id = :client_id
    ORDER BY ts DESC, id DESC LIMIT :limit
),
open_blocks AS (
    SELECT d.id, d.seq, d.session_id, MAX(e.ts) AS from_ts
    FROM decisions d
    JOIN decision_events de ON de.client_id = d.client_id AND de.decision_id = d.id
    JOIN events e ON e.client_id = de.client_id AND e.id = de.event_id
    WHERE d.client_id = :client_id AND d.decision = 'block' AND d.normal = 0
    GROUP BY d.seq
),
candidates AS (
    SELECT r.id AS event_id, d.id AS decision_id, d.seq,
           CASE WHEN d.normal = 1 THEN 0
                WHEN d.decision = 'block' THEN 2
                WHEN d.decision IN ('alert', 'step_up') THEN 1
                ELSE 0 END AS severity
    FROM recent r
    JOIN decision_events de ON de.client_id = :client_id AND de.event_id = r.id
    JOIN decisions d ON d.client_id = de.client_id AND d.id = de.decision_id
    UNION ALL
    SELECT r.id, b.id, b.seq, 2
    FROM recent r JOIN open_blocks b ON b.session_id = r.session_id AND r.ts >= b.from_ts
),
ranked AS (
    SELECT event_id, decision_id, severity,
           ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY severity DESC, seq DESC) AS rank
    FROM candidates
)
SELECT r.*, k.decision_id, COALESCE(k.severity, 0) AS severity
FROM recent r LEFT JOIN ranked k ON k.event_id = r.id AND k.rank = 1
ORDER BY r.ts DESC, r.id DESC
"""


def list_activity(conn: sqlite3.Connection, client_id: str, limit: int) -> list[dict]:
    """Latest accesses of the client, newest first, with the panel verdict (contract 5)."""
    rows = conn.execute(ACTIVITY_SQL, {"client_id": client_id, "limit": limit}).fetchall()
    return [
        {
            "event_id": row["id"],
            # Stored with microseconds; the panel gets the same shape as a decision's access ts.
            "ts": row["ts"][:19] + "Z",
            "user_id": row["user_id"],
            "session_id": row["session_id"],
            "ip": row["ip"],
            "device_id": row["device_id"],
            "method": row["method"],
            "route": row["route"],
            "verdict": SEVERITY_VERDICT[row["severity"]],
            "decision_id": row["decision_id"],
        }
        for row in rows
    ]
