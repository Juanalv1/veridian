import importlib.util
from pathlib import Path

from conftest import AUTH_A, NOW, post_events

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "send_sample_events.py"


def load_script():
    spec = importlib.util.spec_from_file_location("send_sample_events", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sample_scenario_blocks_only_the_attacker(client):
    script = load_script()
    history = script.owner_history(NOW, run_id="t1")
    assert post_events(client, history).json() == {"accepted": len(history)}
    assert client.get("/v1/decisions", headers=AUTH_A).json()["decisions"] == []

    attack = script.attacker_events(NOW, run_id="t1")
    assert post_events(client, attack).status_code == 202

    [decision] = client.get("/v1/decisions", headers=AUTH_A).json()["decisions"]
    assert decision["decision"] == "block"
    listed = client.get("/v1/blocklist", headers=AUTH_A).json()
    assert listed["sessions"] == ["s_attacker_t1"]
    assert listed["ips"] == [script.ATTACKER_IP]
    assert listed["users"] == []
