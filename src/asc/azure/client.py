"""Thin wrapper around the Azure CLI for App Service operations.

All calls shell out to ``az webapp`` so no Azure SDK dependency
is required. The caller is responsible for ensuring ``az`` is authenticated
(``az login`` or a service principal in the environment).

Raises ``AzureClientError`` on any non-zero exit code.
"""

import json
import subprocess
import tempfile
from pathlib import Path


class AzureClientError(Exception):
    """Raised when an ``az`` CLI call returns a non-zero exit code."""


class AzureClient:
    """Shells out to the Azure CLI to manage App Service settings and slots.

    Args:
        app_name: The App Service name.
        resource_group: The resource group name.
        subscription_id: Azure subscription ID to set as active context.
    """

    def __init__(self, app_name: str, resource_group: str, subscription_id: str) -> None:
        self._app = app_name
        self._rg = resource_group
        self._sub = subscription_id

    def list_slots(self) -> list[str]:
        out = self._run(
            [
                "az",
                "webapp",
                "deployment",
                "slot",
                "list",
                "--name",
                self._app,
                "--resource-group",
                self._rg,
                "--subscription",
                self._sub,
                "--query",
                "[].name",
                "--output",
                "json",
            ]
        )
        return json.loads(out)

    def list_settings(self, slot: str | None = None) -> list[dict]:
        cmd = [
            "az",
            "webapp",
            "config",
            "appsettings",
            "list",
            "--name",
            self._app,
            "--resource-group",
            self._rg,
            "--subscription",
            self._sub,
            "--output",
            "json",
        ]
        return json.loads(self._run(cmd + self._slot_args(slot)))

    def set_settings(self, settings: list[dict], slot: str | None = None) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(settings, f)
            tmp = Path(f.name)
        tmp.chmod(0o600)
        try:
            self._run(
                [
                    "az",
                    "webapp",
                    "config",
                    "appsettings",
                    "set",
                    "--name",
                    self._app,
                    "--resource-group",
                    self._rg,
                    "--subscription",
                    self._sub,
                    "--settings",
                    f"@{tmp}",
                ]
                + self._slot_args(slot)
            )
        finally:
            tmp.unlink(missing_ok=True)

    def delete_settings(self, names: list[str], slot: str | None = None) -> None:
        self._run(
            [
                "az",
                "webapp",
                "config",
                "appsettings",
                "delete",
                "--name",
                self._app,
                "--resource-group",
                self._rg,
                "--subscription",
                self._sub,
                "--setting-names",
                *names,
            ]
            + self._slot_args(slot)
        )

    def resolve_kv_secret(self, vault: str, secret: str) -> str:
        out = self._run(
            [
                "az",
                "keyvault",
                "secret",
                "show",
                "--vault-name",
                vault,
                "--name",
                secret,
                "--query",
                "value",
                "--output",
                "tsv",
            ]
        )
        return out.removesuffix("\n")

    def _slot_args(self, slot: str | None) -> list[str]:
        return ["--slot", slot] if slot else []

    def _run(self, cmd: list[str]) -> str:
        """Run a command, returning stdout. Raises AzureClientError on failure."""
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise AzureClientError(result.stderr)
        return result.stdout
