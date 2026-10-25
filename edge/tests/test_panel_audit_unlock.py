"""Panel: a self-unlock shows in the audit page as "Desbloqueado por el usuario" (system actor, no person)."""

from fastapi.testclient import TestClient

from tests.panel_support import PANEL_TOKEN
from tests.test_panel_accounts import new_engine, panel_of, project_a, text
from tests.test_panel_audit import SDK_KEY, entries
from tests.test_panel_unlock import ATTACK, SEED


def test_a_self_unlock_reads_as_unlocked_by_the_user():
    engine = new_engine(seed=SEED)
    panel = panel_of(engine)
    panel.post("/marca", data={"brand_name": "Acme", "self_unlock_enabled": "on"})
    sdk = TestClient(engine)
    assert sdk.post("/v1/check", json={"event": {**ATTACK, "user_email": "ana@example.com"}},
                    headers=SDK_KEY).json()["self_unlock"] is True
    token = engine.state.outbox[-1]["token"]
    headers = {"Authorization": f"Bearer {PANEL_TOKEN}", "X-Veridian-Unlock-Token": token}
    assert sdk.post("/v1/self-unlock", headers=headers).status_code == 200
    got = [e for e in entries(panel.get("/auditoria").text) if "Desbloqueado por el usuario" in e]
    assert len(got) == 1 and "d_1" in got[0] and "Alguien" not in got[0]
    assert "El perfil aprende ese acceso." in got[0]
