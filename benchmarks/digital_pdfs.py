"""Is "Docling first" right for digital PDFs? Docling against the simple reader (pdf_text),
per document type, on more files than the main benchmark has of each type.

    python -m benchmarks.digital_pdfs                       # text, lines, tables, speed
    python -m benchmarks.digital_pdfs --fields --kinds invoice ddr

Extra measure here: "lines kept" = share of the true lines (titles, labelled values,
paragraphs) that come out as a block of their own, not glued to a neighbour or cut in two.
The main text score cannot see this, because it ignores line breaks.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from pathlib import Path
from typing import Any

from benchmarks.metrics import mean, squash, table_accuracy, text_accuracy
from benchmarks.run import RESULTS, FieldRunner, load_generator, run_engine, tables_in
from doctordoc.config import Config
from doctordoc.router import Router

ENGINES = ("pdf_text", "docling")
SEED = 500_000  # far away from the benchmark seeds, so these are different documents


def make_documents(kind: str, count: int) -> list[Any]:
    gen = load_generator()
    makers = {
        "invoice": lambda r: gen.make_invoice(r, 5),
        "ddr": gen.make_ddr,
        "memo": gen.make_memo,
        "rates_2page": lambda r: gen.make_rates(r, 60),
        "invoice_2page": lambda r: gen.make_invoice(r, 45),
    }
    offset = list(makers).index(kind) * 1000
    specs = [makers[kind](random.Random(SEED + offset + i)) for i in range(count)]
    return [(f"{kind}_{i + 1}.pdf", spec, gen.pdf_bytes(spec)) for i, spec in enumerate(specs)]


def lines_kept(blocks: list[str], truth_lines: list[str]) -> float | None:
    if not truth_lines:
        return None
    found = {squash(b) for b in blocks}
    return sum(squash(line) in found for line in truth_lines) / len(truth_lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    kinds = ["invoice", "ddr", "memo", "rates_2page", "invoice_2page"]
    parser.add_argument("--kinds", nargs="*", default=kinds, choices=kinds)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--fields", action="store_true", help="also field extraction (Ollama)")
    args = parser.parse_args()

    config = Config(gpu="off")
    router = Router(config)
    fields = FieldRunner(config, RESULTS / "llm_cache.json") if args.fields else None
    warm = make_documents("memo", 1)[0]
    for name in ENGINES:  # load models before timing
        run_engine(router, name, {"id": warm[0]}, warm[2])

    rows: list[dict[str, Any]] = []
    for kind in args.kinds:
        for filename, spec, data in make_documents(kind, args.count):
            for name in ENGINES:
                started = time.perf_counter()
                out = run_engine(router, name, {"id": filename}, data)
                seconds = time.perf_counter() - started
                pages = out["pages_out"]
                item = {"id": filename, "fields": spec.fields, "schema": spec.schema}
                rows.append(
                    {
                        "kind": kind,
                        "doc": filename,
                        "engine": name,
                        "text_acc": text_accuracy(out["text"], spec.truth_text),
                        "lines_kept": lines_kept(
                            [b.plain_text for p in pages for b in p.blocks],
                            [v for k, v in spec.blocks if k != "table"],
                        ),
                        "table_acc": table_accuracy(tables_in(pages), spec.truth_tables)
                        if spec.truth_tables
                        else None,
                        "field_acc": fields.accuracy(item, out) if fields and spec.fields else None,
                        "seconds": round(seconds, 3),
                    }
                )
            print(filename, file=sys.stderr, flush=True)
    router.release_all()
    write(rows, args)


def write(rows: list[dict[str, Any]], args: argparse.Namespace) -> None:
    folder: Path = RESULTS / "runs" / "digital_pdfs"
    folder.mkdir(parents=True, exist_ok=True)
    name = "_".join(args.kinds) + ("_fields" if args.fields else "")
    with (folder / f"{name}.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"| type (n={args.count}) | engine | text | lines kept | table cells | fields | s/file |")
    print("|---|---|---:|---:|---:|---:|---:|")
    for kind in args.kinds:
        for engine in ENGINES:
            chosen = [r for r in rows if r["kind"] == kind and r["engine"] == engine]
            values = [
                mean(r[k] for r in chosen)
                for k in ("text_acc", "lines_kept", "table_acc", "field_acc", "seconds")
            ]
            shown = ["-" if v is None else f"{v:.2f}" for v in values]
            print(f"| {kind} | {engine} | " + " | ".join(shown) + " |")


if __name__ == "__main__":
    main()
