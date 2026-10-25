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


def test_dotenv_fills_missing_variables_only(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('# comment\nVERIDIAN_BASE_URL="https://engine.example"\nVERIDIAN_TOKEN=from-file\n')
    nested = tmp_path / "engine"
    nested.mkdir()
    monkeypatch.chdir(nested)
    monkeypatch.delenv("VERIDIAN_BASE_URL", raising=False)
    monkeypatch.setenv("VERIDIAN_TOKEN", "from-env")

    load_script().load_dotenv()

    import os

    assert os.environ["VERIDIAN_BASE_URL"] == "https://engine.example"
    assert os.environ["VERIDIAN_TOKEN"] == "from-env"
    monkeypatch.delenv("VERIDIAN_BASE_URL")
