# DoctorDoc benchmark results

Run on 2026-11-25, x86_64, Python 3.12.3, CPU only, 12 synthetic files (seed 1234), 2.2 min. docling 2.152.0, rapidocr 3.10.0, markitdown 0.1.9, pypdfium2 5.15.2, rapidfuzz 3.14.6.

Files each system produced text for: rapidocr 12/12, tesseract 12/12, router 12/12

Broken files: the router returned a clear error (no crash) for 0/0. Good files wrongly rejected: 0.

### Text accuracy (1.00 = perfect; edit distance on letters and digits)

| document type | rapidocr | tesseract | router |
|---|---:|---:|---:|
| rotated | 0.19 | **0.23** | 0.19 |
| photo | **0.59** | 0.51 | **0.59** |
| **ALL files** | **0.39** | 0.37 | **0.39** |

### Table accuracy (share of cells at the right row and column)

| document type | rapidocr | tesseract | router |
|---|---:|---:|---:|
| rotated | **0.00** | **0.00** | **0.00** |
| photo | **0.00** | **0.00** | **0.00** |
| **ALL files** | **0.00** | **0.00** | **0.00** |

### Speed (pages per second, files the system could read; model loading excluded)

| document type | rapidocr | tesseract | router |
|---|---:|---:|---:|
| rotated | 0.3 | **0.6** | 0.3 |
| photo | 0.3 | **0.7** | 0.3 |

**Router vs single engines.** Overall text_acc: router 0.386 vs best single engine (rapidocr) 0.386. Categories where a single engine beat the router: rotated (tesseract 0.23 vs router 0.19).
Overall table_acc: router 0.000 vs best single engine (rapidocr) 0.000. Categories where a single engine beat the router: none.
