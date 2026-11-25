"""Run every installed engine ALONE, and the router, on the same synthetic files.

    python scripts/make_samples.py            # once: builds benchmarks/data
    python -m benchmarks.run                  # text, tables, speed
    python -m benchmarks.run --fields         # also field extraction with the local LLM

Quick mode, to re-check only what you are changing (minutes, not the whole run):
    python -m benchmarks.run --types rotated,photo --systems router --label rotation_after

A partial run (--types, --systems or --limit) writes to benchmarks/results/runs/<label>/ and
never overwrites the full results.

How the comparison is made fair (and where it is generous to the single engines):
  * Every system gets the file's REAL type from doctordoc's detector (so a .pdf that is a
    docx is not a problem for them). Without it they would do worse.
  * A single engine that cannot read a file type, or returns nothing, scores 0 for that file.
    Reading "n/a" in a table means it cannot open that type at all.
  * Models are loaded in a warm-up before timing starts, so speed excludes model loading.
  * Everything runs on CPU (gpu: off) so results do not depend on your graphics card.
Results are written to benchmarks/results/ (CSV + RESULTS.md).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmarks.metrics import field_accuracy, table_accuracy, text_accuracy, word_recall
from doctordoc import parse
from doctordoc.config import Config
from doctordoc.detect import detect
from doctordoc.models import BlockType, Document, Page
from doctordoc.quality import quality_report
from doctordoc.router import Router

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "benchmarks" / "data"
RESULTS = ROOT / "benchmarks" / "results"
ROUTER = "router"
SKIP_ENGINES = {"cloud_vlm", "paddle_vl"}  # cloud is off by design, Paddle needs a GPU
CATEGORY_ORDER = [
    "digital_pdf",
    "scanned_pdf",
    "garbled_pdf",
    "mixed_pdf",
    "table_2page",
    "docx",
    "xlsx",
    "pptx",
    "csv",
    "json",
    "html",
    "email",
    "zip",
    "wrong_extension",
    "rotated",
    "photo",
]


# ======================================================================= running one system


def tables_in(pages: list[Page]) -> list[list[list[str]]]:
    return [b.table.rows for p in pages for b in p.blocks if b.type == BlockType.TABLE and b.table]


def run_engine(router: Router, name: str, item: dict[str, Any], data: bytes) -> dict[str, Any]:
    """One engine alone on the whole file: no triage, no fallback, no merging."""
    info = detect(data, item["id"])
    started = time.perf_counter()
    run = router.run_engine(name, data, info)
    seconds = time.perf_counter() - started
    return {
        "status": run.status,
        "reason": run.reason,
        "seconds": seconds,
        "pages_out": run.pages,
        "text": "\n\n".join(p.text for p in run.pages if p.text),
    }


def run_router(config: Config, router: Router, item: dict[str, Any], data: bytes) -> dict[str, Any]:
    """The full pipeline: detect, triage, route per page, fallback, merge, unpack containers."""
    started = time.perf_counter()
    doc = parse(data, config, filename=item["id"], router=router)
    seconds = time.perf_counter() - started
    everything = list(doc.iter_all())
    pages = [p for d in everything for p in d.pages]
    text = "\n\n".join(d.text for d in everything if d.text)
    status = "error" if doc.error else ("ok" if text.strip() else "empty")
    chosen = sorted({p.engine for p in pages if p.engine})
    return {
        "status": status,
        "reason": doc.error or "",
        "seconds": seconds,
        "pages_out": pages,
        "text": text,
        "engines_used": "+".join(chosen),
        "document": doc,
    }


def score(item: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
    """Compare one system's output with the ground truth."""
    row: dict[str, Any] = {
        "doc": item["id"],
        "category": item["category"],
        "status": out["status"],
        "seconds": round(out["seconds"], 3),
        "pages": item["pages"],
        "reason": out["reason"][:80],
        "text_acc": None,
        "word_recall": None,
        "table_acc": None,
        "field_acc": None,
        "engines_used": out.get("engines_used", ""),
        # what the text-quality check thinks of the same output (it never sees the truth)
        "quality": quality_report(out["text"]).score if out["status"] == "ok" else None,
    }
    if item["expect_error"]:
        return row  # no truth text: judged separately, as "handled gracefully"
    read = out["status"] == "ok"
    row["text_acc"] = text_accuracy(out["text"], item["truth_text"]) if read else 0.0
    row["word_recall"] = word_recall(out["text"], item["truth_text"]) if read else 0.0
    if item["truth_tables"]:
        row["table_acc"] = (
            table_accuracy(tables_in(out["pages_out"]), item["truth_tables"]) if read else 0.0
        )
    return row


# ======================================================================= field extraction


class FieldRunner:
    """Runs the LLM on a system's output, and remembers answers so no text is asked twice."""

    def __init__(self, config: Config, cache_path: Path) -> None:
        self.config = config
        self.path = cache_path
        self.cache: dict[str, dict[str, Any]] = (
            json.loads(cache_path.read_text()) if cache_path.exists() else {}
        )

    def accuracy(self, item: dict[str, Any], out: dict[str, Any]) -> float:
        if out["status"] != "ok":
            return 0.0
        from doctordoc.extract import extract

        pages = out["pages_out"]
        text = "\n\n".join(p.text for p in pages)
        key = hashlib.sha256(
            f"{self.config.llm.model}|{self.config.llm.max_output_tokens}|"
            f"{item['schema']}|{text}".encode()
        ).hexdigest()
        if key not in self.cache:
            result = extract(Document(source=item["id"], pages=pages), item["schema"], self.config)
            if result.error:  # a failed call is not cached: it would poison later runs
                print(f"  fields failed on {item['id']}: {result.error}", file=sys.stderr)
                return 0.0
            self.cache[key] = json.loads(json.dumps(result.data, default=str))
            self.path.write_text(json.dumps(self.cache))
        return field_accuracy(self.cache[key], item["fields"])


# ======================================================================= quick mode


def parse_types(values: list[str] | None) -> list[str]:
    """'rotated,photo', 'rotated photo' or both -> ['rotated', 'photo']."""
    return [name for value in values or [] for name in value.split(",") if name]


def select_types(manifest: list[dict[str, Any]], types: list[str] | None) -> list[dict[str, Any]]:
    if not types:
        return manifest
    present = {m["category"] for m in manifest}
    if unknown := [t for t in types if t not in present]:
        raise SystemExit(
            f"unknown document type(s): {', '.join(unknown)}. In this sample set: "
            f"{', '.join(sorted(present))}. If a type is new, re-run scripts/make_samples.py."
        )
    return [m for m in manifest if m["category"] in types]


def output_dir(args: argparse.Namespace) -> tuple[Path, str]:
    """Where results go, and the run's label. Only a full run may write the main results."""
    partial = bool(args.types or args.systems or args.limit)
    if not partial and not args.label:
        return args.out, ""
    label = args.label or "_".join(args.types or ["partial"])
    return args.out / "runs" / label, label


# ======================================================================= driver


def load_generator() -> Any:
    """Import scripts/make_samples.py (the scripts folder is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "make_samples", ROOT / "scripts" / "make_samples.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["make_samples"] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def load_manifest(data_dir: Path, seed: int) -> list[dict[str, Any]]:
    if not (data_dir / "manifest.json").exists():
        print(f"building the sample set in {data_dir} ...", file=sys.stderr)
        load_generator().write_corpus(data_dir, seed)
    return json.loads((data_dir / "manifest.json").read_text())["files"]  # type: ignore[no-any-return]


def warm_up(
    config: Config,
    router: Router,
    systems: list[str],
    data_dir: Path,
    manifest: list[dict[str, Any]],
) -> None:
    """Load every model once, so timings do not include model loading."""
    samples = {
        m["category"]: m
        for m in manifest
        if m["category"] in ("digital_pdf", "scanned_pdf", "docx")
    }
    for item in samples.values():
        data = (data_dir / "files" / item["id"]).read_bytes()
        for name in systems:
            if name == ROUTER:
                run_router(config, router, item, data)
            else:
                run_engine(router, name, item, data)


def run_benchmark(args: argparse.Namespace) -> list[dict[str, Any]]:
    config = Config(gpu="off")
    router = Router(config)
    everything = load_manifest(args.data, args.seed)
    manifest = select_types(everything, args.types)
    if args.limit:
        manifest = manifest[: args.limit]
    available = [n for n, e in router.engines.items() if e.is_available() and n not in SKIP_ENGINES]
    systems = [s for s in (args.systems or [*available, ROUTER]) if s == ROUTER or s in available]
    print(f"systems: {', '.join(systems)} | files: {len(manifest)}", file=sys.stderr)
    if not args.no_warmup:
        warm_up(config, router, systems, args.data, everything)  # not the filtered list
    fields = FieldRunner(config, RESULTS / "llm_cache.json") if args.fields else None

    rows: list[dict[str, Any]] = []
    outputs = args.data / "outputs" / (args.label or "")
    for number, item in enumerate(manifest, start=1):
        data = (args.data / "files" / item["id"]).read_bytes()
        for system in systems:
            out = (
                run_router(config, router, item, data)
                if system == ROUTER
                else run_engine(router, system, item, data)
            )
            row = {"system": system, **score(item, out)}
            if fields and item["fields"] and not item["expect_error"]:
                row["field_acc"] = fields.accuracy(item, out)
            rows.append(row)
            (outputs / system).mkdir(parents=True, exist_ok=True)
            (outputs / system / f"{item['id']}.txt").write_text(out["text"])
        print(f"[{number}/{len(manifest)}] {item['id']}", file=sys.stderr)
    router.release_all()
    return rows


# ======================================================================= reporting


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    columns = [
        "system",
        "doc",
        "category",
        "status",
        "text_acc",
        "word_recall",
        "table_acc",
        "field_acc",
        "seconds",
        "pages",
        "engines_used",
        "reason",
        "quality",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def cell(rows: list[dict[str, Any]], system: str, category: str | None, metric: str) -> str:
    """Average of a metric for one system in one category (None = all categories)."""
    chosen = [
        r for r in rows if r["system"] == system and (category is None or r["category"] == category)
    ]
    values = [r[metric] for r in chosen if r[metric] is not None]
    if not values:
        return ""
    if all(r["status"] == "skipped" for r in chosen):
        return "n/a"  # this engine cannot open this kind of file at all
    return f"{sum(values) / len(values):.2f}"


def speed_cell(rows: list[dict[str, Any]], system: str, category: str | None) -> str:
    done = [
        r
        for r in rows
        if r["system"] == system
        and r["status"] == "ok"
        and (category is None or r["category"] == category)
    ]
    seconds = sum(r["seconds"] for r in done)
    return f"{sum(r['pages'] for r in done) / seconds:.1f}" if done and seconds > 0 else ""


def markdown_table(rows: list[dict[str, Any]], systems: list[str], metric: str, title: str) -> str:
    categories = (
        [
            c
            for c in CATEGORY_ORDER
            if any(r["category"] == c and r[metric] is not None for r in rows)
        ]
        if metric != "speed"
        else [c for c in CATEGORY_ORDER if any(r["category"] == c for r in rows)]
    )
    lines = [
        f"### {title}",
        "",
        "| document type | " + " | ".join(systems) + " |",
        "|---|" + "---:|" * len(systems),
    ]
    best_note = ""
    # speed has no ALL row: every engine reads a different mix of files, so it is not comparable
    for category in [*categories] if metric == "speed" else [*categories, None]:
        label = category or "**ALL files**"
        values = [
            speed_cell(rows, s, category) if metric == "speed" else cell(rows, s, category, metric)
            for s in systems
        ]
        numeric = [float(v) for v in values if v not in ("", "n/a")]
        top = max(numeric) if numeric else None
        shown = [
            f"**{v}**"
            if top is not None and v not in ("", "n/a") and float(v) == top
            else (v or "-")
            for v in values
        ]
        lines.append(f"| {label} | " + " | ".join(shown) + " |")
    return "\n".join(lines) + best_note


def coverage_line(rows: list[dict[str, Any]], systems: list[str]) -> str:
    graded = [r for r in rows if r["category"] != "corrupt"]
    docs = {r["doc"] for r in graded}
    parts = []
    for s in systems:
        ok = sum(1 for r in graded if r["system"] == s and r["status"] == "ok")
        parts.append(f"{s} {ok}/{len(docs)}")
    return "Files each system produced text for: " + ", ".join(parts)


def verdict(rows: list[dict[str, Any]], systems: list[str], metric: str) -> str:
    """Does the router beat every single engine, overall and per category?"""
    singles = [s for s in systems if s != ROUTER]
    if not singles or ROUTER not in systems:
        return f"No router-vs-single comparison for {metric} in this partial run."

    def avg(system: str, category: str | None) -> float:
        chosen = [
            r[metric]
            for r in rows
            if r["system"] == system
            and r[metric] is not None
            and (category is None or r["category"] == category)
        ]
        return sum(chosen) / len(chosen) if chosen else 0.0

    best = max(singles, key=lambda s: avg(s, None))
    lines = [
        f"Overall {metric}: router {avg(ROUTER, None):.3f} vs best single engine "
        f"({best}) {avg(best, None):.3f}."
    ]
    behind = []
    for category in CATEGORY_ORDER:
        if not any(r["category"] == category and r[metric] is not None for r in rows):
            continue
        top = max(singles, key=lambda s: avg(s, category))
        if avg(ROUTER, category) < avg(top, category) - 0.005:
            behind.append(
                f"{category} ({top} {avg(top, category):.2f} vs router {avg(ROUTER, category):.2f})"
            )
    lines.append(
        "Categories where a single engine beat the router: "
        + (", ".join(behind) if behind else "none")
        + "."
    )
    return " ".join(lines)


BAD_BELOW = (0.99, 0.90, 0.50)  # "bad output" = text accuracy below this


def fooled_counts(rows: list[dict[str, Any]], bad_below: float, threshold: float) -> dict[str, int]:
    """How the text-quality check did against the truth, over outputs that have both numbers."""
    graded = [r for r in rows if r.get("quality") is not None and r["text_acc"] is not None]
    bad = [r for r in graded if r["text_acc"] < bad_below]
    passed = [r for r in graded if r["quality"] >= threshold]
    fooled = [r for r in bad if r["quality"] >= threshold]
    return {"outputs": len(graded), "bad": len(bad), "passed": len(passed), "fooled": len(fooled)}


def quality_table(rows: list[dict[str, Any]], threshold: float) -> str:
    """Every SINGLE-engine output that produced text. The router is left out: it only repeats
    what an engine produced, after already using the quality check."""
    singles = [r for r in rows if r["system"] != ROUTER]
    lines = [
        "### How often the text-quality check is fooled",
        "",
        f"The check accepts an output that scores at least {threshold}. It never sees the truth.",
        "Counted over every single-engine output that produced some text.",
        "",
        "| a bad output means text accuracy below | outputs | bad | bad but accepted | "
        "share of bad outputs that fool it | share of accepted outputs that are bad |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for bar in BAD_BELOW:
        c = fooled_counts(singles, bar, threshold)
        share_bad = f"{c['fooled'] / c['bad']:.0%}" if c["bad"] else "-"
        share_passed = f"{c['fooled'] / c['passed']:.0%}" if c["passed"] else "-"
        lines.append(
            f"| {bar:.2f} | {c['outputs']} | {c['bad']} | {c['fooled']} | "
            f"{share_bad} | {share_passed} |"
        )
    return "\n".join(lines)


def robustness(rows: list[dict[str, Any]], manifest_path: Path) -> str:
    broken = [r for r in rows if r["system"] == ROUTER and r["category"] == "corrupt"]
    handled = sum(1 for r in broken if r["status"] == "error")
    fine = [r for r in rows if r["system"] == ROUTER and r["category"] != "corrupt"]
    false_errors = [r["doc"] for r in fine if r["status"] == "error"]
    return (
        f"Broken files: the router returned a clear error (no crash) for {handled}/{len(broken)}. "
        f"Good files wrongly rejected: {len(false_errors)}"
        + (f" ({', '.join(false_errors)})" if false_errors else "")
        + "."
    )


def versions() -> str:
    from importlib.metadata import PackageNotFoundError, version

    out = []
    for package in ("docling", "rapidocr", "markitdown", "pypdfium2", "rapidfuzz"):
        try:
            out.append(f"{package} {version(package)}")
        except PackageNotFoundError:
            pass
    return ", ".join(out)


def write_report(
    rows: list[dict[str, Any]], systems: list[str], args: argparse.Namespace, elapsed: float
) -> str:
    has_fields = any(r["field_acc"] is not None for r in rows)
    parts = [
        "# DoctorDoc benchmark results",
        "",
        f"Run on {datetime.now():%Y-%m-%d}, {platform.processor() or platform.machine()}, "
        f"Python {platform.python_version()}, CPU only, "
        f"{len({r['doc'] for r in rows})} synthetic files "
        f"(seed {args.seed}), {elapsed / 60:.1f} min. {versions()}.",
        "",
        coverage_line(rows, systems),
        "",
        robustness(rows, args.data / "manifest.json"),
        "",
        markdown_table(
            rows,
            systems,
            "text_acc",
            "Text accuracy (1.00 = perfect; edit distance on letters and digits)",
        ),
        "",
        markdown_table(
            rows,
            systems,
            "table_acc",
            "Table accuracy (share of cells at the right row and column)",
        ),
        "",
    ]
    if has_fields:
        parts += [
            markdown_table(
                rows,
                systems,
                "field_acc",
                "Field accuracy (llama3.2:3b; correct fields / true fields)",
            ),
            "",
        ]
    parts += [
        markdown_table(
            rows,
            systems,
            "speed",
            "Speed (pages per second, files the system could read; model loading excluded)",
        ),
        "",
    ]
    if any(r.get("quality") is not None and r["system"] != ROUTER for r in rows):
        parts += [quality_table(rows, Config().quality_threshold), ""]
    parts += ["**Router vs single engines.** " + verdict(rows, systems, "text_acc")]
    parts += [verdict(rows, systems, "table_acc")]
    if has_fields:
        parts += [verdict(rows, systems, "field_acc")]
    return "\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--out", type=Path, default=RESULTS)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument(
        "--fields", action="store_true", help="also run field extraction (needs Ollama)"
    )
    parser.add_argument(
        "--types", "--only", nargs="*", help="quick mode: only these types, e.g. rotated,photo"
    )
    parser.add_argument("--label", help="name of this partial run (its results folder)")
    parser.add_argument("--systems", nargs="*", help="only these systems (engine names, router)")
    parser.add_argument("--limit", type=int, help="only the first N files")
    parser.add_argument("--no-warmup", action="store_true")
    args = parser.parse_args()
    args.types = parse_types(args.types)
    args.out, args.label = output_dir(args)
    args.out.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    rows = run_benchmark(args)
    systems = list(dict.fromkeys(r["system"] for r in rows))
    write_csv(rows, args.out / "results.csv")
    report = write_report(rows, systems, args, time.perf_counter() - started)
    (args.out / "RESULTS.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
