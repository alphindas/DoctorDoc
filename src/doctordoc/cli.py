"""Command line: doctordoc parse | engines | triage | extract | ui | api."""

from __future__ import annotations

import logging
import os
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table as RichTable
from rich.text import Text

from doctordoc.config import Config
from doctordoc.models import Document

app = typer.Typer(help="One door for any document.", no_args_is_help=True, add_completion=False)
out = Console()  # normal output
err = Console(stderr=True)  # logs and --explain, so stdout stays clean for the document text

ConfigOption = Annotated[
    Optional[Path], typer.Option("--config", "-c", help="Path to a doctordoc.yaml file.")
]  # noqa: UP007


@app.callback()
def _every_command() -> None:
    # Text printed to stdout (the tables) shows "?" for a character the terminal's encoding
    # lacks, instead of stopping with UnicodeEncodeError. JSON and Markdown do not go this
    # way: they are always written as UTF-8.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:
        reconfigure(errors="replace")


def _load_config(path: Path | None, gpu: bool | None = None) -> Config:
    config = Config.from_yaml(path) if path else Config()
    if gpu is not None:
        config.gpu = "on" if gpu else "off"
    return config


def _write_utf8(text: str, errors: str) -> None:
    """Write to stdout as UTF-8 whatever the locale, with the system's line endings. Not through
    Rich (it wraps lines and turns ":smile:" into an emoji) and not through click.echo (it
    removes terminal codes from piped text)."""
    sys.stdout.flush()  # keep the order if anything was printed as text before
    sys.stdout.buffer.write(text.replace("\n", os.linesep).encode("utf-8", errors))
    sys.stdout.buffer.flush()


def _print_json(text: str) -> None:
    # A model's answer can hold half of a character pair, which UTF-8 cannot carry.
    # "backslashreplace" writes it as \udXXX, which is how JSON itself escapes it.
    _write_utf8(text + "\n", "backslashreplace")


def _print_markdown(text: str) -> None:
    """The same bytes that --out writes."""
    # surrogateescape gives a file name that is not valid UTF-8 its own bytes back
    _write_utf8(text, "surrogateescape")


def _output_names(files: list[Path], target: Path, extension: str) -> list[str]:
    """One output file name for each input: "<input name>.<extension>", changed only where two
    inputs would write to the same file. Then the folders are put in front, and if that name is
    taken too, a number is added."""

    def key(name: str) -> str:  # Windows and macOS see "A.txt" and "a.txt" as one file
        return unicodedata.normalize("NFC", name).casefold()

    def too_long(name: str) -> bool:  # the usual limit for a file name
        return len(name.encode("utf-8", "surrogateescape")) > 255

    names = [f"{path.name}.{extension}" for path in files]
    counts = Counter(map(key, names))
    clashing = [i for i, name in enumerate(names) if counts[key(name)] > 1]
    taken = {key(name) for name in names if counts[key(name)] == 1}
    # files at the top of the folder choose first, so that they can keep their own name
    clashing.sort(key=lambda i: (len(files[i].relative_to(target).parts) > 1, i))
    for i in clashing:
        folders = files[i].relative_to(target).parts[:-1]
        stem, suffix = os.path.splitext(files[i].name)
        base = "_".join([*folders, stem])
        if too_long(f"{base}{suffix}.{extension}"):  # then leave the folders out
            base = stem
        number = 1
        while True:  # ends because every number gives a new name
            tail = f"{'' if number == 1 else f'-{number}'}{suffix}.{extension}"
            fitted = base
            while fitted and too_long(fitted + tail):
                fitted = fitted[:-1]
            if key(fitted + tail) not in taken:
                break
            number += 1
        names[i] = fitted + tail
        taken.add(key(names[i]))
    return names


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
    """Parse a file (or every file in a folder) into Markdown or JSON.

    With --format json and no --out, a folder with several files (or none) gives a JSON list.
    With --out, inputs that would write to the same file get different names.
    """
    from doctordoc.cloud_warning import warn_about_cloud
    from doctordoc.pipeline import parse_many

    if format not in ("md", "json"):
        raise typer.BadParameter("format must be 'md' or 'json'")
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    files = _files_in(target)
    config = _load_config(config_path, gpu)
    warn_about_cloud(config)
    docs = parse_many(files, config)
    output_names = _output_names(files, target, format) if out_dir else []
    renamed = sum(
        name != f"{path.name}.{format}" for path, name in zip(files, output_names, strict=False)
    )
    if renamed:
        err.print(
            f"Some inputs share a file name. Outputs renamed so that no result is lost: {renamed}",
            highlight=False,
            soft_wrap=True,
        )

    # several JSON documents one after another are not valid JSON, so they go in one list
    as_list = format == "json" and not out_dir and len(files) != 1
    listed: list[str] = []
    failures = 0
    for index, (path, doc) in enumerate(zip(files, docs, strict=True)):
        failures += 1 if doc.error else 0
        text = doc.to_markdown() if format == "md" else doc.to_json()
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            destination = out_dir / output_names[index]
            destination.write_text(text, encoding="utf-8")
            # Text, not markup: a path may hold "[red]" or ":smile:"
            line = Text.assemble(("wrote", "green"), f" {path} -> {destination}")
            err.print(line, soft_wrap=True)
        elif format == "json":
            if as_list:
                err.print(f"===== {path} =====", markup=False, highlight=False, soft_wrap=True)
                listed.append(text)
            else:
                _print_json(text)
        else:
            if len(files) > 1:
                _print_markdown(f"\n===== {path} =====\n")
            _print_markdown(text)
        if doc.error:
            err.print(f"[red]error[/red] {path.name}: {doc.error}")
        for warning in doc.warnings:
            err.print(f"[yellow]warning[/yellow] {path.name}: {warning}")
        if explain:
            _print_decisions(doc, path.name)
    if as_list:
        _print_json("[" + ",\n".join(listed) + "]")
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
    from doctordoc.cloud_warning import warn_about_cloud
    from doctordoc.extract import extract as run_extract

    config = _load_config(config_path)
    if model:
        config.llm.model = model
    if base_url:
        config.llm.base_url = base_url
    if provider:
        config.llm.provider = provider  # type: ignore[assignment]
    warn_about_cloud(config)
    result = run_extract(file, schema, config)
    if format == "json":
        _print_json(result.to_report_json())
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
    import subprocess

    if importlib.util.find_spec("streamlit") is None:
        err.print(
            "[red]The UI needs Streamlit.[/red] Install it with: "
            + escape('pip install "doctordoc[ui]"'),
            soft_wrap=True,
        )
        raise typer.Exit(code=1)
    from doctordoc.cloud_warning import UI_NOTE, warn_about_cloud
    from doctordoc.ui import app as ui_app

    config = _load_config(config_path)
    warn_about_cloud(config, UI_NOTE)
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
    no_docs: Annotated[
        bool, typer.Option("--no-docs", help="Turn off /docs, /redoc and /openapi.json.")
    ] = False,
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

    config = _load_config(config_path)
    if no_docs:
        config.api.docs = False
    local = _is_local(host)
    if not local:
        err.print(
            f"[bold red]warning[/bold red] {escape(repr(host))} is not a local address, and the "
            "API has no login: anyone who can reach it can send files to be parsed and use your "
            "model. Use 127.0.0.1 unless something else controls who can connect.",
            soft_wrap=True,
        )
        if not config.api.allowed_hosts:
            err.print(
                "[bold red]warning[/bold red] Any Host header is now accepted. To limit it, "
                "set api.allowed_hosts to the names people use to reach this server.",
                soft_wrap=True,
            )
    uvicorn.run(create_app(config, local_only=local), host=host, port=port)


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
