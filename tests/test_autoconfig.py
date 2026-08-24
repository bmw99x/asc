import json

from typer.testing import CliRunner

from asc.tools.autoconfig.__main__ import app
from asc.tools.autoconfig.discover import discover

SUBS = [{"id": "sub-1", "tenantId": "ten-1"}]
APPS = [
    {"name": "app-api", "rg": "rg-prod"},
    {"name": "app-web", "rg": "rg-prod"},
    {"name": "app-worker", "rg": "rg-staging"},
]


def fake_run(cmd: list[str]) -> list[dict[str, str]]:
    if cmd[:2] == ["az", "account"]:
        return SUBS
    if cmd[:2] == ["az", "webapp"]:
        return APPS
    raise AssertionError(f"unexpected command: {cmd}")


def test_discover_groups_by_resource_group():
    config = discover(run=fake_run)

    assert set(config) == {"rg-prod", "rg-staging"}
    assert set(config["rg-prod"]) == {"app-api", "app-web"}
    assert config["rg-prod"]["app-api"] == {
        "app_name": "app-api",
        "resource_group": "rg-prod",
        "subscription_id": "sub-1",
        "tenant_id": "ten-1",
    }
    assert config["rg-staging"]["app-worker"]["resource_group"] == "rg-staging"


def test_cli_writes_config_with_service_name_mapping(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "asc.tools.autoconfig.__main__.discover",
        lambda run=None: discover(run=fake_run),
    )

    out_path = tmp_path / "config.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [str(out_path), "--service-name-mapping", json.dumps({"rg-prod": "MyProduct"})],
    )

    assert result.exit_code == 0, result.output
    written = json.loads(out_path.read_text())

    assert set(written) == {"MyProduct", "rg-staging"}
    assert set(written["MyProduct"]) == {"app-api", "app-web"}
    assert written["MyProduct"]["app-api"]["app_name"] == "app-api"


def test_cli_merges_with_existing_config(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "asc.tools.autoconfig.__main__.discover",
        lambda run=None: discover(run=fake_run),
    )

    out_path = tmp_path / "config.json"
    out_path.write_text(
        json.dumps(
            {
                "Existing": {
                    "app": {
                        "app_name": "app-existing",
                        "resource_group": "rg-existing",
                        "subscription_id": "sub-x",
                        "tenant_id": "ten-x",
                    }
                }
            }
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, [str(out_path)])

    assert result.exit_code == 0, result.output
    written = json.loads(out_path.read_text())

    assert "Existing" in written
    assert "rg-prod" in written
    assert "rg-staging" in written


def test_cli_malformed_existing_config_exits_nonzero(monkeypatch, tmp_path):
    """A hand-broken config.json is reported, not raised as a traceback."""
    monkeypatch.setattr(
        "asc.tools.autoconfig.__main__.discover",
        lambda run=None: discover(run=fake_run),
    )

    out_path = tmp_path / "config.json"
    out_path.write_text("{ not json")

    runner = CliRunner()
    result = runner.invoke(app, [str(out_path)])

    assert result.exit_code != 0
    assert "Error parsing existing config" in result.output
    assert out_path.read_text() == "{ not json"


def test_cli_invalid_mapping_json_exits_nonzero(tmp_path):
    out_path = tmp_path / "config.json"
    runner = CliRunner()
    result = runner.invoke(app, [str(out_path), "--service-name-mapping", "not-json"])

    assert result.exit_code != 0
    assert not out_path.exists()
