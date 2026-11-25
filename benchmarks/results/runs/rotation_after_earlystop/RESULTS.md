# DoctorDoc benchmark results

Run on 2026-11-25, x86_64, Python 3.12.3, CPU only, 12 synthetic files (seed 1234), 4.6 min. docling 2.152.0, rapidocr 3.10.0, markitdown 0.1.9, pypdfium2 5.15.2, rapidfuzz 3.14.6.

Files each system produced text for: rapidocr 12/12, tesseract 12/12, router 12/12

Broken files: the router returned a clear error (no crash) for 0/0. Good files wrongly rejected: 0.

### Text accuracy (1.00 = perfect; edit distance on letters and digits)

| document type | rapidocr | tesseract | router |
|---|---:|---:|---:|
| rotated | **1.00** | 0.97 | **1.00** |
| photo | **0.99** | 0.87 | **0.99** |
| **ALL files** | **0.99** | 0.92 | **0.99** |

### Table accuracy (share of cells at the right row and column)

| document type | rapidocr | tesseract | router |
|---|---:|---:|---:|
| rotated | **0.00** | **0.00** | **0.00** |
| photo | **0.00** | **0.00** | **0.00** |
| **ALL files** | **0.00** | **0.00** | **0.00** |

### Speed (pages per second, files the system could read; model loading excluded)

| document type | rapidocr | tesseract | router |
|---|---:|---:|---:|
| rotated | 0.1 | **0.2** | 0.1 |
| photo | 0.1 | **0.3** | 0.1 |

### How often the text-quality check is fooled

The check accepts an output that scores at least 0.55. It never sees the truth.
Counted over every single-engine output that produced some text.

| a bad output means text accuracy below | outputs | bad | bad but accepted | share of bad outputs that fool it | share of accepted outputs that are bad |
|---|---:|---:|---:|---:|---:|
| 0.99 | 24 | 10 | 10 | 100% | 42% |
| 0.90 | 24 | 4 | 4 | 100% | 17% |
| 0.50 | 24 | 0 | 0 | - | 0% |

**Router vs single engines.** Overall text_acc: router 0.994 vs best single engine (rapidocr) 0.994. Categories where a single engine beat the router: none.
Overall table_acc: router 0.000 vs best single engine (rapidocr) 0.000. Categories where a single engine beat the router: none.
