from __future__ import annotations

import json
from typing import Any

import typer
from typer.core import TyperArgument

# Compatibility shim for Click/Typer signature differences.
_original_make_metavar = TyperArgument.make_metavar


def _patched_make_metavar(self, *args, **kwargs):
    return _original_make_metavar(self)


TyperArgument.make_metavar = _patched_make_metavar

from app.services.supabase import supabase_anon, supabase_service

app = typer.Typer(help="CLI tools for CURFD AI backend")


@app.command("supabase-get")
def supabase_get(
    path: str = typer.Argument(..., help="Supabase REST path, e.g. /rest/v1/users"),
    use_service_role: bool = typer.Option(
        False, "--service", help="Use service role key instead of anon key"
    ),
    params: list[str] = typer.Option(
        None,
        "--param",
        help="Query params as key=value (repeatable)",
    ),
) -> None:
    client = supabase_service if use_service_role else supabase_anon
    if not client:
        raise typer.BadParameter("Supabase client not configured; check env vars")

    parsed_params: dict[str, Any] = {}
    for item in params or []:
        if "=" not in item:
            raise typer.BadParameter("Param must be key=value")
        key, value = item.split("=", 1)
        parsed_params[key] = value

    resp = client.request("GET", path, params=parsed_params)
    try:
        payload = resp.json()
    except Exception:
        payload = resp.text

    typer.echo(json.dumps(payload, indent=2) if isinstance(payload, (dict, list)) else payload)


if __name__ == "__main__":
    app()
