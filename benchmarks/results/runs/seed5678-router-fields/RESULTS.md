# DoctorDoc benchmark results

Run on 2026-11-25, x86_64, Python 3.12.3, CPU only, 48 synthetic files (seed 5678), 13.1 min. docling 2.152.0, rapidocr 3.10.0, markitdown 0.1.9, pypdfium2 5.15.2, rapidfuzz 3.14.6.

Files each system produced text for: router 46/46

Broken files: the router returned a clear error (no crash) for 2/2. Good files wrongly rejected: 0.

### Text accuracy (1.00 = perfect; edit distance on letters and digits)

| document type | router |
|---|---:|
| digital_pdf | **1.00** |
| scanned_pdf | **0.94** |
| garbled_pdf | **1.00** |
| mixed_pdf | **1.00** |
| table_2page | **0.97** |
| docx | **1.00** |
| xlsx | **0.99** |
| pptx | **1.00** |
| csv | **1.00** |
| json | **1.00** |
| html | **1.00** |
| email | **0.95** |
| zip | **1.00** |
| wrong_extension | **1.00** |
| rotated | **1.00** |
| photo | **0.91** |
| **ALL files** | **0.98** |

### Table accuracy (share of cells at the right row and column)

| document type | router |
|---|---:|
| digital_pdf | **1.00** |
| scanned_pdf | **0.00** |
| garbled_pdf | **0.00** |
| table_2page | **1.00** |
| docx | **1.00** |
| xlsx | **1.00** |
| pptx | **1.00** |
| csv | **1.00** |
| json | **1.00** |
| html | **1.00** |
| email | **1.00** |
| zip | **1.00** |
| wrong_extension | **1.00** |
| rotated | **0.00** |
| photo | **0.00** |
| **ALL files** | **0.62** |

### Field accuracy (llama3.2:3b; correct fields / true fields)

| document type | router |
|---|---:|
| digital_pdf | **1.00** |
| scanned_pdf | **1.00** |
| garbled_pdf | **1.00** |
| table_2page | **1.00** |
| docx | **1.00** |
| pptx | **1.00** |
| html | **1.00** |
| wrong_extension | **1.00** |
| rotated | **1.00** |
| photo | **1.00** |
| **ALL files** | **1.00** |

### Speed (pages per second, files the system could read; model loading excluded)

| document type | router |
|---|---:|
| digital_pdf | **0.3** |
| scanned_pdf | **0.3** |
| garbled_pdf | **0.2** |
| mixed_pdf | **0.2** |
| table_2page | **0.1** |
| docx | **19.0** |
| xlsx | **48.8** |
| pptx | **63.5** |
| csv | **222.2** |
| json | **400.0** |
| html | **250.0** |
| email | **0.8** |
| zip | **0.9** |
| wrong_extension | **0.6** |
| rotated | **0.1** |
| photo | **0.1** |

**Router vs single engines.** No router-vs-single comparison for text_acc in this partial run.
No router-vs-single comparison for table_acc in this partial run.
No router-vs-single comparison for field_acc in this partial run.
