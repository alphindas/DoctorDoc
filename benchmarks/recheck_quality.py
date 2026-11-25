"""Re-score saved output texts, to give the "how often is the quality score fooled" numbers
a result file.

    python -m benchmarks.recheck_quality

It runs no engine, OCR or LLM. It reads saved result CSVs, the output texts and the truth in
benchmarks/data (git-ignored, so only a machine that still has them can run this), and scores
the texts with the existing functions. It writes two NEW files and refuses to overwrite them.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from benchmarks.metrics import text_accuracy
from doctordoc.config import Config
from doctordoc.quality import quality_report

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
DATA = ROOT / "benchmarks" / "data"
OUTPUTS = DATA / "outputs"
REPORT = RESULTS / "recheck_quality_from_saved_outputs.md"
TABLE = RESULTS / "recheck_quality_from_saved_outputs.csv"

# run name -> (saved result CSV, folder of saved texts under benchmarks/data/outputs)
RUNS = {
    "full": ("results.csv", ""),
    "rotation_before": ("runs/rotation_before/results.csv", "rotation_before"),
    "rotation_after_regression": (
        "runs/rotation_after_regression/results.csv",
        "rotation_after_regression",
    ),
    "rotation_after_earlystop": (
        "runs/rotation_after_earlystop/results.csv",
        "rotation_after_earlystop",
    ),
}
BYTE_CHECK = ("rotation_after", "rotation_after_earlystop")
Row = dict[str, Any]


def score_run(run: str, truth: dict[str, str]) -> list[Row]:
    """Every single-engine output of one run that produced text, re-scored."""
    csv_name, folder = RUNS[run]
    with (RESULTS / csv_name).open(newline="") as handle:
        saved = list(csv.DictReader(handle))
    rows = []
    for old in saved:
        if old["system"] == "router" or old["status"] != "ok" or old["category"] == "corrupt":
            continue
        text = (OUTPUTS / folder / old["system"] / f"{old['doc']}.txt").read_text()
        accuracy = text_accuracy(text, truth[old["doc"]])
        quality = quality_report(text).score
        # stop if a saved text is no longer the one that was scored at the time
        if abs(accuracy - float(old["text_acc"])) > 0.0005:
            raise SystemExit(f"{run} {old['system']} {old['doc']}: text accuracy differs")
        if old.get("quality") and abs(quality - float(old["quality"])) > 0.0005:
            raise SystemExit(f"{run} {old['system']} {old['doc']}: quality score differs")
        rows.append(
            {
                "source_run": run,
                "system": old["system"],
                "doc": old["doc"],
                "category": old["category"],
                "text_acc_saved": float(old["text_acc"]),
                "text_acc_recalculated": round(accuracy, 4),
                "quality_saved": old.get("quality") or "",
                "quality_recalculated": quality,
            }
        )
    return rows


def fraction(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.1%})" if whole else f"{part}/{whole}"


def counts(rows: list[Row], bad_below: float, threshold: float) -> dict[str, int]:
    bad = [r for r in rows if r["text_acc_saved"] < bad_below]
    accepted = [r for r in rows if r["quality_recalculated"] >= threshold]
    fooled = [r for r in bad if r["quality_recalculated"] >= threshold]
    return {"bad": len(bad), "accepted": len(accepted), "fooled": len(fooled)}


def output_line(row: Row, threshold: float) -> str:
    verdict = "accepted" if row["quality_recalculated"] >= threshold else "rejected"
    return (
        f"| {row['system']} | {row['doc']} | {row['text_acc_saved']:.4f} | "
        f"{row['quality_recalculated']:.3f} | {verdict} |"
    )


def byte_check() -> tuple[int, int]:
    """(identical text files, text files compared) between the two rotation runs."""
    first, second = (OUTPUTS / name for name in BYTE_CHECK)
    names = sorted(p.relative_to(first) for p in first.rglob("*.txt"))
    if names != sorted(p.relative_to(second) for p in second.rglob("*.txt")):
        raise SystemExit("the two rotation runs do not hold the same files")
    same = sum((first / n).read_bytes() == (second / n).read_bytes() for n in names)
    return same, len(names)


def main() -> None:
    if existing := [str(p) for p in (REPORT, TABLE) if p.exists()]:
        raise SystemExit(f"will not overwrite: {', '.join(existing)}")
    manifest = json.loads((DATA / "manifest.json").read_text())["files"]
    truth = {m["id"]: m["truth_text"] for m in manifest}
    threshold = Config().quality_threshold

    scored = {run: score_run(run, truth) for run in RUNS}
    replaced = {(r["system"], r["doc"]) for r in scored["rotation_after_regression"]}
    kept = [r for r in scored["full"] if (r["system"], r["doc"]) not in replaced]
    totals = {
        "before": {
            "full": scored["full"],
            "rotation_before": scored["rotation_before"],
        },
        "after": {
            "full": kept,
            "rotation_after_regression": scored["rotation_after_regression"],
            "rotation_after_earlystop": scored["rotation_after_earlystop"],
        },
    }
    joined = {name: [r for part in parts.values() for r in part] for name, parts in totals.items()}

    lines = [
        "# Quality score: how often is it fooled? (re-check)",
        "",
        "**Recalculated after the fact from output texts kept locally (git-ignored). "
        "Not a fresh benchmark run.**",
        "",
        "Others cannot rerun this: the output texts and the truth file are in `benchmarks/data/`, "
        "which is git-ignored. Rebuilding the sample files does not bring back the old outputs.",
        "",
        "Made by `python -m benchmarks.recheck_quality`, using the repo's "
        "`doctordoc.quality.quality_report` and `benchmarks.metrics.text_accuracy`. No engine, "
        f"OCR or LLM was run. A score of at least {threshold} means accepted. Only single-engine "
        "outputs that produced text are counted (not the router).",
        "",
        "Checks made: for every output used here, the recalculated text accuracy matches the saved "
        "CSV (within 0.0005). Where the CSV saved a quality score, that matches too. "
        "The full run's CSV saved no quality scores, so those are new calculations.",
        "",
        "## Which runs make up each total",
        "",
        "| total | run (saved CSV) | outputs used |",
        "|---|---|---:|",
    ]
    for name, parts in totals.items():
        for run, rows in parts.items():
            lines.append(f"| {name} the rotation fix | `{RUNS[run][0]}` | {len(rows)} |")
        files = len({r["doc"] for r in joined[name]})
        lines.append(f"| **{name}, total** | from {files} files | **{len(joined[name])}** |")
    lines += [
        "",
        f"In the `after` total, the {len(replaced)} RapidOCR and Tesseract outputs in the full run "
        "for the scanned, garbled and mixed files are replaced by the "
        "`rotation_after_regression` re-run. The other full-run outputs are kept. No file from "
        "the time records how the earlier `after` total was put together; this combination "
        "gives the same totals.",
        "",
        "## Results",
        "",
        "| total | bad means text accuracy below | bad outputs accepted | "
        "accepted outputs that were bad |",
        "|---|---:|---:|---:|",
    ]
    for bar in (0.90, 0.99):
        for name, rows in joined.items():
            c = counts(rows, bar, threshold)
            lines.append(
                f"| {name} the rotation fix | {bar:.2f} | {fraction(c['fooled'], c['bad'])} | "
                f"{fraction(c['fooled'], c['accepted'])} |"
            )

    header = [
        "| engine | file | text accuracy | quality score | result |",
        "|---|---|---:|---:|---|",
    ]
    for name, rows in joined.items():
        caught = [
            r for r in rows if r["text_acc_saved"] < 0.90 and r["quality_recalculated"] < threshold
        ]
        lines += [
            "",
            f"## Bad outputs (below 0.90) that were caught, {name} the fix: {len(caught)}",
        ]
        lines += ["", *header, *[output_line(r, threshold) for r in caught]]

    junk = [
        r
        for r in scored["full"]
        if r["category"] == "garbled_pdf" and r["system"] in ("pdf_text", "docling")
    ]
    missed = [r for r in junk if r["quality_recalculated"] >= threshold]
    junk_caught = len(junk) - len(missed)
    lines += [
        "",
        "## Junk hidden text layers",
        "",
        "These are the `pdf_text` and `docling` outputs on the garbled PDFs, from the full run. "
        f"The score caught {junk_caught} of {len(junk)} and missed {len(missed)}.",
        "",
        *header,
        *[output_line(r, threshold) for r in junk],
    ]
    same, compared = byte_check()
    lines += [
        "",
        "## Byte-for-byte check (rotation early stop)",
        "",
        f"Saved text files of `{BYTE_CHECK[0]}` (always four reads) against `{BYTE_CHECK[1]}`: "
        f"**{same} of {compared}** are identical byte for byte. The run names are the only record "
        "here of each run's rotation setting.",
        "",
        f"One row per output and total is in [`{TABLE.name}`]({TABLE.name}).",
    ]

    columns = ["total", *scored["full"][0], "accepted", "bad_below_0.90", "bad_below_0.99"]
    with TABLE.open("x", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for name, rows in joined.items():
            for r in rows:
                writer.writerow(
                    {
                        "total": name,
                        **r,
                        "accepted": r["quality_recalculated"] >= threshold,
                        "bad_below_0.90": r["text_acc_saved"] < 0.90,
                        "bad_below_0.99": r["text_acc_saved"] < 0.99,
                    }
                )
    with REPORT.open("x") as handle:
        handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
