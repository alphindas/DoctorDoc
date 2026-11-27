"""Command line: doctordoc parse | engines | triage | extract | ui | api."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table as RichTable

from doctordoc.config import Config
from doctordoc.models import Document

app = typer.Typer(help="One door for any document.", no_args_is_help=True, add_completion=False)
out = Console()  # normal output
err = Console(stderr=True)  # logs and --explain, so stdout stays clean for the document text

ConfigOption = Annotated[
    Optional[Path], typer.Option("--config", "-c", help="Path to a doctordoc.yaml file.")
]  # noqa: UP007


def _load_config(path: Path | None, gpu: bool | None = None) -> Config:
    config = Config.from_yaml(path) if path else Config()
    if gpu is not None:
        config.gpu = "on" if gpu else "off"
    return config


def _files_in(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(p for p in target.rglob("*") if p.is_file() and not p.name.startswith("."))
    return [target]


@app.command()
def parse(
    target: Annotated[Path, typer.Argument(exists=True, help="A file or a folder.")],
    format: Annotated[str, typer.Option("--format", "-f", help="md or json.")] = "md",
    out_dir: Annotated[
        Optional[Path], typer.Option("--out", "-o", help="Write files here.")
    ] = None,  # noqa: UP007
    explain: Annotated[
        bool, typer.Option("--explain", help="Show the router's decision log.")
    ] = False,
    config_path: ConfigOption = None,
    gpu: Annotated[Optional[bool], typer.Option("--gpu/--no-gpu", help="Force GPU on/off.")] = None,  # noqa: UP007
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Parse a file (or every file in a folder) into Markdown or JSON."""
    from doctordoc.pipeline import parse_many

    if format not in ("md", "json"):
        raise typer.BadParameter("format must be 'md' or 'json'")
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    files = _files_in(target)
    docs = parse_many(files, _load_config(config_path, gpu))

    failures = 0
    for path, doc in zip(files, docs, strict=True):
        failures += 1 if doc.error else 0
        text = doc.to_markdown() if format == "md" else doc.to_json()
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            destination = out_dir / f"{path.name}.{format}"
            destination.write_text(text, encoding="utf-8")
            err.print(f"[green]wrote[/green] {destination}", soft_wrap=True)
        else:
            if len(files) > 1:
                out.print(f"\n===== {path} =====", markup=False)
            out.print(text, markup=False, highlight=False)
        if doc.error:
            err.print(f"[red]error[/red] {path.name}: {doc.error}")
        for warning in doc.warnings:
            err.print(f"[yellow]warning[/yellow] {path.name}: {warning}")
        if explain:
            _print_decisions(doc, path.name)
    if failures:
        raise typer.Exit(code=1)


def _print_decisions(doc: Document, name: str) -> None:
    for item in doc.iter_all():
        table = RichTable(title=f"Router decisions: {name}" if item is doc else f"  {item.source}")
        for column in ("scope", "engine", "outcome", "quality", "seconds", "reason"):
            table.add_column(column)
        colours = {
            "accepted": "green",
            "rejected": "red",
            "failed": "red",
            "timeout": "red",
            "fallback": "yellow",
            "merged": "cyan",
            "skipped": "dim",
        }
        for d in item.decisions:
            colour = colours.get(d.outcome, "white")
            table.add_row(
                d.scope,
                d.engine,
                f"[{colour}]{d.outcome}[/{colour}]",
                "" if d.quality is None else f"{d.quality:.2f}",
                f"{d.seconds:.2f}",
                escape(d.reason),
            )
        if item.decisions:
            err.print(table)


@app.command()
def engines(config_path: ConfigOption = None) -> None:
    """List every engine: is it installed, and what is its license?"""
    from doctordoc.router import Router

    table = RichTable(title="DoctorDoc engines")
    for column in ("engine", "license", "status", "install", "note"):
        table.add_column(column)
    for row in Router(_load_config(config_path)).engine_report():
        ready = row["status"] == "ready"
        status = "[green]ready[/green]" if ready else f"[yellow]{escape(row['status'])}[/yellow]"
        note = f"[yellow]{escape(row['note'])}[/yellow]" if row["note"] else ""
        table.add_row(row["name"], row["license"], status, escape(row["install"]), note)
    out.print(table)


@app.command()
def triage(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    config_path: ConfigOption = None,
) -> None:
    """Show what kind of page each PDF page is, without parsing it."""
    from doctordoc.detect import detect
    from doctordoc.triage import triage_pdf

    data = file.read_bytes()
    info = detect(data, file.name)
    out.print(f"{file.name}: detected as [bold]{info.file_type}[/bold] ({info.mime})")
    for warning in info.warnings:
        out.print(f"[yellow]warning[/yellow] {warning}")
    if info.problem:
        out.print(f"[red]problem[/red] {info.problem}")
        raise typer.Exit(code=1)
    if info.file_type != "pdf":
        out.print("Not a PDF: only PDFs are triaged page by page.")
        return
    table = RichTable(title="Page triage")
    for column in ("page", "kind", "chars", "image coverage", "text quality", "rotation"):
        table.add_column(column)
    for page in triage_pdf(data, _load_config(config_path)):
        quality = "" if page.quality is None else f"{page.quality:.2f}"
        table.add_row(
            str(page.number),
            page.kind.value,
            str(page.chars),
            f"{page.image_coverage:.0%}",
            quality,
            str(page.rotation),
        )
    out.print(table)


@app.command()
def extract(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    schema: Annotated[
        str,
        typer.Option("--schema", "-s", help="invoice | receipt | drilling | form | module:Class"),
    ] = "invoice",
    format: Annotated[
        str, typer.Option("--format", "-f", help="table, or json (the stable report shape).")
    ] = "table",
    model: Annotated[
        Optional[str], typer.Option(help="LLM model name (default llama3.2:3b).")
    ] = None,  # noqa: UP007
    base_url: Annotated[Optional[str], typer.Option(help="LLM server URL.")] = None,  # noqa: UP007
    provider: Annotated[
        Optional[str], typer.Option(help="ollama or openai (OpenAI-compatible).")
    ] = None,  # noqa: UP007
    config_path: ConfigOption = None,
) -> None:
    """Extract fields (with proof of where each came from) using a local LLM."""
    from doctordoc.extract import extract as run_extract

    config = _load_config(config_path)
    if model:
        config.llm.model = model
    if base_url:
        config.llm.base_url = base_url
    if provider:
        config.llm.provider = provider  # type: ignore[assignment]
    result = run_extract(file, schema, config)
    if format == "json":
        out.print(result.to_report_json(), markup=False, highlight=False)
    else:
        _print_extraction(result)
    for warning in result.warnings:
        err.print(f"[yellow]warning[/yellow] {warning}")
    if result.error:
        err.print(f"[red]error[/red] {result.error}")
        raise typer.Exit(code=1)


def _print_extraction(result) -> None:
    table = RichTable(
        title=(
            f"{result.schema_name}  |  {result.model}  |  "
            f"{result.seconds}s, {result.attempts} LLM call(s)"
        )
    )
    for column in ("field", "value", "conf", "page", "source text", "match"):
        table.add_column(column)
    for item in result.fields:
        colour = "red" if item.needs_review else "green"
        source = item.source
        table.add_row(
            item.path,
            escape(str(item.value)),
            f"[{colour}]{item.confidence:.2f}[/{colour}]",
            str(source.page) if source else "-",
            escape(source.text[:40]) if source else "NOT FOUND",
            source.match if source else "ungrounded",
        )
    out.print(table)
    for issue in result.issues:
        colour = "red" if issue.severity == "error" else "yellow"
        out.print(
            f"[{colour}]{issue.severity}[/{colour}] {escape(issue.path)}: "
            f"{escape(issue.message)} [dim]({issue.rule})[/dim]"
        )
    if result.needs_review:
        out.print(f"[bold red]needs review:[/bold red] {', '.join(result.needs_review)}")
    elif not result.error:
        out.print("[bold green]nothing needs review[/bold green]")


@app.command()
def ui(
    port: Annotated[int, typer.Option(help="Port for the web page.")] = 8501,
    config_path: ConfigOption = None,
) -> None:
    """Open the web UI: upload files, see the router's choices, extract fields, compare engines."""
    import importlib.util
    import os
    import subprocess
    import sys

    if importlib.util.find_spec("streamlit") is None:
        err.print('[red]The UI needs Streamlit.[/red] Install it with: pip install "doctordoc[ui]"')
        raise typer.Exit(code=1)
    from doctordoc.ui import app as ui_app

    config = _load_config(config_path)
    env = dict(os.environ)
    if config_path:
        env["DOCTORDOC_UI_CONFIG"] = str(config_path.resolve())
    # Streamlit reads .streamlit/config.toml (the theme) from the folder it starts in.
    # In a checkout that is the repo root; elsewhere Streamlit's own defaults plus our CSS apply.
    root = Path(ui_app.__file__).resolve().parents[3]
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        ui_app.__file__,
        "--server.port",
        str(port),
        "--server.maxUploadSize",
        str(max(1, int(config.max_file_mb))),
    ]
    cwd = root if (root / ".streamlit" / "config.toml").exists() else None
    raise typer.Exit(code=subprocess.call(command, env=env, cwd=cwd))


@app.command()
def api(
    host: Annotated[str, typer.Option(help="Address to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
    config_path: ConfigOption = None,
) -> None:
    """Start the HTTP API. It has no login: keep it on this computer."""
    import importlib.util

    needed = ("fastapi", "uvicorn", "python_multipart")
    if any(importlib.util.find_spec(name) is None for name in needed):
        err.print(
            "[red]The API needs extra packages.[/red] Install them with: "
            + escape('pip install "doctordoc[api]"'),
            soft_wrap=True,
        )
        raise typer.Exit(code=1)
    import uvicorn

    from doctordoc.api import create_app

    if not _is_local(host):
        err.print(
            f"[bold red]warning[/bold red] {escape(repr(host))} is not a local address, and the "
            "API has no login: anyone who can reach it can send files to be parsed and use your "
            "model. Use 127.0.0.1 unless something else controls who can connect.",
            soft_wrap=True,
        )
    uvicorn.run(create_app(_load_config(config_path)), host=host, port=port)


def _is_local(host: str) -> bool:
    """True only for addresses that cannot be reached from another computer."""
    import ipaddress

    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:  # another name: assume other computers can reach it
        return False


if __name__ == "__main__":
    app()
