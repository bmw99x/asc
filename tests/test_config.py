import json

import pytest

from asc import config as cfg
from asc.config import AppBinding, ConfigError, load_config, save_config

VALID = {
    "MyProduct": {
        "api": {
            "app_name": "app-api",
            "resource_group": "rg-prod",
            "subscription_id": "sub",
            "tenant_id": "ten",
        },
        "_ignored": {
            "app_name": "x",
            "resource_group": "x",
            "subscription_id": "x",
            "tenant_id": "x",
        },
    },
    "_example": {},
}


def test_load_valid(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(VALID))
    monkeypatch.setattr(cfg, "CONFIG_PATH", p)
    c = load_config()
    assert list(c) == ["MyProduct"] and list(c["MyProduct"]) == ["api"]
    assert c["MyProduct"]["api"].app_name == "app-api"


def test_bootstrap_on_missing(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(cfg, "CONFIG_PATH", p)
    monkeypatch.setattr(cfg, "_README_PATH", tmp_path / "README.md")
    assert load_config() == {} and p.exists()


def test_tenant_id_is_optional(tmp_path, monkeypatch):
    """asc scopes every az call by subscription, so tenant_id may be omitted."""
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "G": {
                    "a": {
                        "app_name": "n",
                        "resource_group": "r",
                        "subscription_id": "s",
                    }
                }
            }
        )
    )
    monkeypatch.setattr(cfg, "CONFIG_PATH", p)
    assert load_config()["G"]["a"].tenant_id is None


def test_invalid_binding_raises(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"G": {"a": {"app_name": "only"}}}))
    monkeypatch.setattr(cfg, "CONFIG_PATH", p)
    with pytest.raises(ConfigError):
        load_config()


def test_save_round_trip(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(cfg, "CONFIG_PATH", p)
    c = {
        "G": {
            "a": AppBinding(
                app_name="n",
                resource_group="r",
                subscription_id="s",
                tenant_id="t",
            )
        }
    }
    save_config(c)
    monkeypatch.setattr(cfg, "CONFIG_PATH", p)
    assert load_config() == c
