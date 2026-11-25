# Quality score: how often is it fooled? (re-check)

**Recalculated after the fact from output texts kept locally (git-ignored). Not a fresh benchmark run.**

Others cannot rerun this: the output texts and the truth file are in `benchmarks/data/`, which is git-ignored. Rebuilding the sample files does not bring back the old outputs.

Made by `python -m benchmarks.recheck_quality`, using the repo's `doctordoc.quality.quality_report` and `benchmarks.metrics.text_accuracy`. No engine, OCR or LLM was run. A score of at least 0.55 means accepted. Only single-engine outputs that produced text are counted (not the router).

Checks made: for every output used here, the recalculated text accuracy matches the saved CSV (within 0.0005). Where the CSV saved a quality score, that matches too. The full run's CSV saved no quality scores, so those are new calculations.

## Which runs make up each total

| total | run (saved CSV) | outputs used |
|---|---|---:|
| before the rotation fix | `results.csv` | 95 |
| before the rotation fix | `runs/rotation_before/results.csv` | 24 |
| **before, total** | from 44 files | **119** |
| after the rotation fix | `results.csv` | 75 |
| after the rotation fix | `runs/rotation_after_regression/results.csv` | 20 |
| after the rotation fix | `runs/rotation_after_earlystop/results.csv` | 24 |
| **after, total** | from 44 files | **119** |

In the `after` total, the 20 RapidOCR and Tesseract outputs in the full run for the scanned, garbled and mixed files are replaced by the `rotation_after_regression` re-run. The other full-run outputs are kept. No file from the time records how the earlier `after` total was put together; this combination gives the same totals.

## Results

| total | bad means text accuracy below | bad outputs accepted | accepted outputs that were bad |
|---|---:|---:|---:|
| before the rotation fix | 0.90 | 37/41 (90.2%) | 37/115 (32.2%) |
| after the rotation fix | 0.90 | 19/23 (82.6%) | 19/115 (16.5%) |
| before the rotation fix | 0.99 | 54/58 (93.1%) | 54/115 (47.0%) |
| after the rotation fix | 0.99 | 41/45 (91.1%) | 41/115 (35.7%) |

## Bad outputs (below 0.90) that were caught, before the fix: 4

| engine | file | text accuracy | quality score | result |
|---|---|---:|---:|---|
| pdf_text | garbled_1_invoice.pdf | 0.3885 | 0.492 | rejected |
| docling | garbled_1_invoice.pdf | 0.3682 | 0.492 | rejected |
| pdf_text | garbled_2_ddr.pdf | 0.2476 | 0.446 | rejected |
| docling | garbled_2_ddr.pdf | 0.2476 | 0.446 | rejected |

## Bad outputs (below 0.90) that were caught, after the fix: 4

| engine | file | text accuracy | quality score | result |
|---|---|---:|---:|---|
| pdf_text | garbled_1_invoice.pdf | 0.3885 | 0.492 | rejected |
| docling | garbled_1_invoice.pdf | 0.3682 | 0.492 | rejected |
| pdf_text | garbled_2_ddr.pdf | 0.2476 | 0.446 | rejected |
| docling | garbled_2_ddr.pdf | 0.2476 | 0.446 | rejected |

## Junk hidden text layers

These are the `pdf_text` and `docling` outputs on the garbled PDFs, from the full run. The score caught 4 of 6 and missed 2.

| engine | file | text accuracy | quality score | result |
|---|---|---:|---:|---|
| pdf_text | garbled_1_invoice.pdf | 0.3885 | 0.492 | rejected |
| docling | garbled_1_invoice.pdf | 0.3682 | 0.492 | rejected |
| pdf_text | garbled_2_ddr.pdf | 0.2476 | 0.446 | rejected |
| docling | garbled_2_ddr.pdf | 0.2476 | 0.446 | rejected |
| pdf_text | garbled_3_memo.pdf | 0.1509 | 0.600 | accepted |
| docling | garbled_3_memo.pdf | 0.1484 | 0.600 | accepted |

## Byte-for-byte check (rotation early stop)

Saved text files of `rotation_after` (always four reads) against `rotation_after_earlystop`: **36 of 36** are identical byte for byte. The run names are the only record here of each run's rotation setting.

One row per output and total is in [`recheck_quality_from_saved_outputs.csv`](recheck_quality_from_saved_outputs.csv).
