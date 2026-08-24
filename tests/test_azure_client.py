import json

import pytest

from asc.azure.client import AzureClient, AzureClientError


class FakeResult:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


@pytest.fixture
def client():
    return AzureClient("app-api", "rg-prod", "sub-id")


def run_capture(monkeypatch, result):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        return result

    monkeypatch.setattr("asc.azure.client.subprocess.run", fake_run)
    return calls


def test_list_settings_production(client, monkeypatch):
    payload = [{"name": "K", "value": "v", "slotSetting": True}]
    calls = run_capture(monkeypatch, FakeResult(stdout=json.dumps(payload)))
    assert client.list_settings() == payload
    cmd = calls[0]
    assert cmd[:5] == ["az", "webapp", "config", "appsettings", "list"]
    assert "--slot" not in cmd and "--subscription" in cmd


def test_list_settings_slot(client, monkeypatch):
    calls = run_capture(monkeypatch, FakeResult(stdout="[]"))
    client.list_settings(slot="staging")
    assert calls[0][calls[0].index("--slot") + 1] == "staging"


def test_list_slots(client, monkeypatch):
    calls = run_capture(monkeypatch, FakeResult(stdout=json.dumps(["staging", "qa"])))
    assert client.list_slots() == ["staging", "qa"]
    assert calls[0][:4] == ["az", "webapp", "deployment", "slot"]


def test_set_settings_writes_json_file(client, monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, capture_output, text):
        at_arg = next(a for a in cmd if a.startswith("@"))
        with open(at_arg[1:]) as f:
            seen["payload"] = json.loads(f.read())
        seen["cmd"] = cmd
        return FakeResult()

    monkeypatch.setattr("asc.azure.client.subprocess.run", fake_run)
    client.set_settings([{"name": "K", "value": "v", "slotSetting": False}])
    assert seen["payload"] == [{"name": "K", "value": "v", "slotSetting": False}]
    assert seen["cmd"][:5] == ["az", "webapp", "config", "appsettings", "set"]


def test_delete_settings(client, monkeypatch):
    calls = run_capture(monkeypatch, FakeResult())
    client.delete_settings(["A", "B"], slot="staging")
    cmd = calls[0]
    assert cmd[:5] == ["az", "webapp", "config", "appsettings", "delete"]
    idx = cmd.index("--setting-names")
    assert cmd[idx + 1 : idx + 3] == ["A", "B"] and "--slot" in cmd


def test_resolve_kv_secret_strips_newline(client, monkeypatch):
    calls = run_capture(monkeypatch, FakeResult(stdout="s3cret\n"))
    assert client.resolve_kv_secret("kv-prod", "DbPassword") == "s3cret"
    assert "--subscription" in calls[0]


def test_error_raises_with_stderr(client, monkeypatch):
    run_capture(monkeypatch, FakeResult(returncode=1, stderr="boom"))
    with pytest.raises(AzureClientError, match="boom"):
        client.list_slots()
