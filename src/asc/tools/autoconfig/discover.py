"""Discovery of Azure App Service bindings across subscriptions."""

import json
import subprocess
import sys
from collections.abc import Callable

import typer

RunFn = Callable[[list[str]], list[dict[str, str]]]


def _az_json(cmd: list[str]) -> list[dict[str, str]]:
    """Run an az CLI command with JSON output and parse the result."""
    full_cmd = [*cmd, "--output", "json"]
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        typer.echo(f"Error running command: {' '.join(full_cmd)}", err=True)
        typer.echo(f"stderr: {e.stderr}", err=True)
        sys.exit(1)
    return json.loads(result.stdout)


def discover(run: RunFn | None = None) -> dict[str, dict[str, dict[str, str]]]:
    """Return config dict: group (resource group) -> app alias -> binding fields."""
    run = run or _az_json
    subs = run(["az", "account", "list", "--query", "[].{id:id, tenantId:tenantId}"])
    config: dict[str, dict[str, dict[str, str]]] = {}
    for sub in subs:
        apps = run(
            [
                "az",
                "webapp",
                "list",
                "--subscription",
                sub["id"],
                "--query",
                "[].{name:name, rg:resourceGroup}",
            ]
        )
        for app in apps:
            config.setdefault(app["rg"], {})[app["name"]] = {
                "app_name": app["name"],
                "resource_group": app["rg"],
                "subscription_id": sub["id"],
                "tenant_id": sub["tenantId"],
            }
    return config
