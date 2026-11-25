# DoctorDoc benchmark results

Run on 2026-11-24, x86_64, Python 3.12.3, CPU only, 36 synthetic files (seed 1234), 17.5 min. docling 2.152.0, rapidocr 3.10.0, markitdown 0.1.9, pypdfium2 5.15.2, rapidfuzz 3.14.6.

Files each system produced text for: native 15/34, pdf_text 13/34, docling 20/34, markitdown 13/34, rapidocr 17/34, tesseract 17/34, router 34/34

Broken files: the router returned a clear error (no crash) for 2/2. Good files wrongly rejected: 0.

### Text accuracy (1.00 = perfect; edit distance on letters and digits)

| document type | native | pdf_text | docling | markitdown | rapidocr | tesseract | router |
|---|---:|---:|---:|---:|---:|---:|---:|
| digital_pdf | n/a | **1.00** | **1.00** | n/a | 0.97 | **1.00** | **1.00** |
| scanned_pdf | n/a | 0.00 | 0.00 | n/a | **0.97** | 0.95 | **0.97** |
| garbled_pdf | n/a | 0.26 | 0.25 | n/a | **1.00** | 0.96 | **1.00** |
| mixed_pdf | n/a | 0.84 | 0.84 | n/a | 0.88 | **1.00** | 0.98 |
| table_2page | n/a | **0.97** | **0.97** | n/a | **0.97** | **0.97** | **0.97** |
| docx | **1.00** | n/a | **1.00** | **1.00** | n/a | n/a | **1.00** |
| xlsx | **0.99** | n/a | n/a | 0.98 | n/a | n/a | **0.99** |
| pptx | **1.00** | n/a | **1.00** | **1.00** | n/a | n/a | **1.00** |
| csv | **1.00** | n/a | n/a | **1.00** | n/a | n/a | **1.00** |
| json | **1.00** | n/a | n/a | 0.35 | n/a | n/a | **1.00** |
| html | **1.00** | n/a | **1.00** | **1.00** | n/a | n/a | **1.00** |
| email | 0.30 | n/a | n/a | n/a | n/a | n/a | **0.95** |
| zip | n/a | n/a | n/a | n/a | n/a | n/a | **1.00** |
| wrong_extension | 0.50 | 0.50 | **1.00** | 0.50 | 0.50 | 0.50 | **1.00** |
| **ALL files** | 0.40 | 0.30 | 0.51 | 0.34 | 0.48 | 0.49 | **0.99** |

### Table accuracy (share of cells at the right row and column)

| document type | native | pdf_text | docling | markitdown | rapidocr | tesseract | router |
|---|---:|---:|---:|---:|---:|---:|---:|
| digital_pdf | n/a | 0.00 | **1.00** | n/a | 0.00 | 0.00 | **1.00** |
| scanned_pdf | n/a | **0.00** | **0.00** | n/a | **0.00** | **0.00** | **0.00** |
| garbled_pdf | n/a | **0.00** | **0.00** | n/a | **0.00** | **0.00** | **0.00** |
| table_2page | n/a | 0.00 | **1.00** | n/a | 0.00 | 0.00 | **1.00** |
| docx | **1.00** | n/a | **1.00** | 0.00 | n/a | n/a | **1.00** |
| xlsx | **1.00** | n/a | n/a | 0.98 | n/a | n/a | **1.00** |
| pptx | **1.00** | n/a | **1.00** | **1.00** | n/a | n/a | **1.00** |
| csv | **1.00** | n/a | n/a | **1.00** | n/a | n/a | **1.00** |
| json | **1.00** | n/a | n/a | 0.00 | n/a | n/a | **1.00** |
| html | **1.00** | n/a | **1.00** | **1.00** | n/a | n/a | **1.00** |
| email | 0.00 | n/a | n/a | n/a | n/a | n/a | **1.00** |
| zip | n/a | n/a | n/a | n/a | n/a | n/a | **1.00** |
| wrong_extension | 0.50 | 0.00 | **1.00** | 0.00 | 0.00 | 0.00 | **1.00** |
| **ALL files** | 0.46 | 0.00 | 0.46 | 0.28 | 0.00 | 0.00 | **0.82** |

### Field accuracy (llama3.2:3b; correct fields / true fields)

| document type | native | pdf_text | docling | markitdown | rapidocr | tesseract | router |
|---|---:|---:|---:|---:|---:|---:|---:|
| digital_pdf | n/a | 0.67 | 0.67 | n/a | 0.67 | **1.00** | **1.00** |
| scanned_pdf | n/a | 0.00 | 0.00 | n/a | **1.00** | **1.00** | **1.00** |
| garbled_pdf | n/a | 0.31 | 0.31 | n/a | **1.00** | **1.00** | **1.00** |
| table_2page | n/a | 0.62 | **1.00** | n/a | 0.62 | **1.00** | **1.00** |
| docx | **1.00** | n/a | **1.00** | **1.00** | n/a | n/a | **1.00** |
| pptx | **1.00** | n/a | **1.00** | **1.00** | n/a | n/a | **1.00** |
| html | **1.00** | n/a | **1.00** | **1.00** | n/a | n/a | **1.00** |
| wrong_extension | 0.50 | 0.44 | **0.94** | 0.50 | 0.44 | 0.44 | **0.94** |
| **ALL files** | 0.41 | 0.24 | 0.68 | 0.41 | 0.50 | 0.58 | **0.99** |

### Speed (pages per second, files the system could read; model loading excluded)

| document type | native | pdf_text | docling | markitdown | rapidocr | tesseract | router |
|---|---:|---:|---:|---:|---:|---:|---:|
| digital_pdf | - | **235.3** | 0.5 | - | 0.3 | 1.6 | 0.4 |
| scanned_pdf | - | - | - | - | 0.4 | **0.9** | 0.4 |
| garbled_pdf | - | **333.3** | 0.5 | - | 0.4 | 0.8 | 0.3 |
| mixed_pdf | - | **375.0** | 0.9 | - | 0.4 | 1.7 | 0.3 |
| table_2page | - | **181.8** | 0.2 | - | 0.3 | 1.2 | 0.2 |
| docx | **38.5** | - | 26.7 | 0.8 | - | - | 31.2 |
| xlsx | 142.9 | - | - | 3.9 | - | - | **181.8** |
| pptx | **307.7** | - | 97.6 | 11.5 | - | - | 95.2 |
| csv | **500.0** | - | - | 6.1 | - | - | 250.0 |
| json | 333.3 | - | - | 6.0 | - | - | **500.0** |
| html | **500.0** | - | 95.2 | 6.4 | - | - | 333.3 |
| email | **545.5** | - | - | - | - | - | 1.3 |
| zip | - | - | - | - | - | - | **1.2** |
| wrong_extension | 71.4 | **250.0** | 0.9 | 1.7 | 0.4 | 1.6 | 0.9 |

**Router vs single engines.** Overall text_acc: router 0.990 vs best single engine (docling) 0.507. Categories where a single engine beat the router: mixed_pdf (tesseract 1.00 vs router 0.98).
Overall table_acc: router 0.821 vs best single engine (native) 0.464. Categories where a single engine beat the router: none.
Overall field_acc: router 0.993 vs best single engine (docling) 0.676. Categories where a single engine beat the router: none.
