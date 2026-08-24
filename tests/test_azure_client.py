import json
import stat
import subprocess
from pathlib import Path

import pytest

from asc.azure.client import AZ_TIMEOUT_SECONDS, AzureClient, AzureClientError


class FakeResult:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


@pytest.fixture
def client():
    return AzureClient("app-api", "rg-prod", "sub-id")


def run_capture(monkeypatch, result, kwargs=None):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if kwargs is not None:
            kwargs.update(kw)
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
    """The payload file is owner-only while it exists and is removed afterwards."""
    seen = {}

    def fake_run(cmd, **kw):
        at_arg = next(a for a in cmd if a.startswith("@"))
        path = Path(at_arg[1:])
        seen["payload"] = json.loads(path.read_text())
        seen["mode"] = stat.S_IMODE(path.stat().st_mode)
        seen["path"] = path
        seen["cmd"] = cmd
        return FakeResult()

    monkeypatch.setattr("asc.azure.client.subprocess.run", fake_run)
    client.set_settings([{"name": "K", "value": "v", "slotSetting": False}])
    assert seen["payload"] == [{"name": "K", "value": "v", "slotSetting": False}]
    assert seen["cmd"][:5] == ["az", "webapp", "config", "appsettings", "set"]
    assert seen["mode"] == 0o600
    assert not seen["path"].exists()


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


def test_run_passes_a_timeout(client, monkeypatch):
    """Every az call is bounded so a hung CLI cannot freeze the TUI forever."""
    kwargs: dict = {}
    run_capture(monkeypatch, FakeResult(stdout="[]"), kwargs)
    client.list_slots()
    assert kwargs["timeout"] == AZ_TIMEOUT_SECONDS


def test_missing_az_binary_becomes_azure_client_error(client, monkeypatch):
    """A missing az CLI is reported, not raised as a bare FileNotFoundError."""

    def fake_run(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory: 'az'")

    monkeypatch.setattr("asc.azure.client.subprocess.run", fake_run)
    with pytest.raises(AzureClientError, match="az CLI not found"):
        client.list_slots()


def test_timeout_becomes_azure_client_error(client, monkeypatch):
    """A hung az call surfaces as AzureClientError so the app can notify."""

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, AZ_TIMEOUT_SECONDS)

    monkeypatch.setattr("asc.azure.client.subprocess.run", fake_run)
    with pytest.raises(AzureClientError, match="timed out"):
        client.list_slots()


def test_unparseable_slot_json_becomes_azure_client_error(client, monkeypatch):
    """Non-JSON stdout from az slot listing is reported, not a JSONDecodeError."""
    run_capture(monkeypatch, FakeResult(stdout="not json"))
    with pytest.raises(AzureClientError, match="unexpected output"):
        client.list_slots()


def test_unparseable_settings_json_becomes_azure_client_error(client, monkeypatch):
    """Non-JSON stdout from az settings listing is reported the same way."""
    run_capture(monkeypatch, FakeResult(stdout="<html>login required</html>"))
    with pytest.raises(AzureClientError, match="unexpected output"):
        client.list_settings()


def test_empty_slot_name_is_not_treated_as_production(client, monkeypatch):
    """An empty slot string is passed through rather than silently dropped."""
    calls = run_capture(monkeypatch, FakeResult(stdout="[]"))
    client.list_settings(slot="")
    assert calls[0][-2:] == ["--slot", ""]
