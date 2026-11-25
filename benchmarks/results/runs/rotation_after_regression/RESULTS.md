# DoctorDoc benchmark results

Run on 2026-11-25, x86_64, Python 3.12.3, CPU only, 10 synthetic files (seed 1234), 4.8 min. docling 2.152.0, rapidocr 3.10.0, markitdown 0.1.9, pypdfium2 5.15.2, rapidfuzz 3.14.6.

Files each system produced text for: rapidocr 10/10, tesseract 10/10, router 10/10

Broken files: the router returned a clear error (no crash) for 0/0. Good files wrongly rejected: 0.

### Text accuracy (1.00 = perfect; edit distance on letters and digits)

| document type | rapidocr | tesseract | router |
|---|---:|---:|---:|
| scanned_pdf | **0.97** | 0.95 | **0.97** |
| garbled_pdf | **1.00** | 0.96 | **1.00** |
| mixed_pdf | **1.00** | **1.00** | **1.00** |
| **ALL files** | **0.99** | 0.97 | **0.99** |

### Table accuracy (share of cells at the right row and column)

| document type | rapidocr | tesseract | router |
|---|---:|---:|---:|
| scanned_pdf | **0.00** | **0.00** | **0.00** |
| garbled_pdf | **0.00** | **0.00** | **0.00** |
| **ALL files** | **0.00** | **0.00** | **0.00** |

### Speed (pages per second, files the system could read; model loading excluded)

| document type | rapidocr | tesseract | router |
|---|---:|---:|---:|
| scanned_pdf | 0.1 | **0.2** | 0.1 |
| garbled_pdf | 0.1 | **0.2** | 0.1 |
| mixed_pdf | 0.1 | **0.4** | 0.1 |

**Router vs single engines.** Overall text_acc: router 0.990 vs best single engine (rapidocr) 0.990. Categories where a single engine beat the router: none.
Overall table_acc: router 0.000 vs best single engine (rapidocr) 0.000. Categories where a single engine beat the router: none.
