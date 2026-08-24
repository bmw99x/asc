"""Entry point for asc-autoconfig CLI tool."""

import json
import sys
from pathlib import Path

import typer

from asc.tools.autoconfig.discover import discover

app = typer.Typer(
    help="Automatically configure asc by discovering Azure App Services",
    no_args_is_help=True,
)

_OUTPUT_PATH_HELP = (
    "Path to config.json file to write. Existing groups are kept, but a "
    "rediscovered group replaces the same-named group wholesale — apps you "
    "added by hand under that group are dropped."
)
_SERVICE_NAME_MAPPING_HELP = (
    'JSON dict mapping resource group names to group names. Example: \'{"rg-prod": "MyProduct"}\''
)


@app.command()
def main(
    output_path: Path = typer.Argument(  # noqa: B008
        ...,
        help=_OUTPUT_PATH_HELP,
    ),
    service_name_mapping: str = typer.Option(  # noqa: B008
        "{}",
        "--service-name-mapping",
        "-m",
        help=_SERVICE_NAME_MAPPING_HELP,
    ),
) -> None:
    """Discover Azure App Services and write/merge asc config.json.

    The merge is per-group, not per-app: a rediscovered group replaces the
    same-named group in the existing file wholesale.
    """
    try:
        mapping: dict[str, str] = json.loads(service_name_mapping)
    except json.JSONDecodeError as e:
        typer.echo(f"Error parsing service-name-mapping JSON: {e}", err=True)
        sys.exit(1)

    discovered = discover()

    renamed: dict[str, dict[str, dict[str, str]]] = {}
    for group, apps in discovered.items():
        renamed.setdefault(mapping.get(group, group), {}).update(apps)

    existing: dict[str, dict[str, dict[str, str]]] = {}
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text())
        except json.JSONDecodeError as e:
            typer.echo(f"Error parsing existing config at {output_path}: {e}", err=True)
            sys.exit(1)

    merged = {**existing, **renamed}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged, indent=2))

    typer.echo(f"Wrote {len(merged)} group(s) to {output_path}")
    for group_name, apps in renamed.items():
        typer.echo(f"  {group_name}: {', '.join(apps)}")


if __name__ == "__main__":
    app()
