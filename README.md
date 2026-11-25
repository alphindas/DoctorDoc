# DoctorDoc

**One door for any document: it works out what the file is, picks the best engine for each page, and returns clean Markdown/JSON plus fields you can trace back to the source text.**

> Status: early (v0.1). Parsing + router, field extraction, a web UI and a synthetic benchmark work.
> The API and the Docker image are not built yet. Apache-2.0, fully local by default.

## 30-second quickstart

```bash
git clone <this repo> && cd doctordoc
uv venv && source .venv/bin/activate
uv pip install -e ".[ocr,docling,markitdown]"   # extras are optional: see `doctordoc engines`

doctordoc parse report.pdf --explain            # Markdown + the router's decision log
doctordoc triage report.pdf                     # what kind is each page?
doctordoc engines                               # which engines are installed, and their licenses
doctordoc ui                                    # web page: upload files, try the samples in examples/

ollama pull llama3.2:3b                         # for field extraction (local, about 2 GB)
doctordoc extract invoice.pdf --schema invoice
```

```python
from doctordoc import parse
from doctordoc.extract import extract

doc = parse("scan.pdf")
print(doc.to_markdown())
result = extract(doc, "invoice")  # or your own Pydantic model
print(result.needs_review)  # fields a human should check
print(result.field("total").source)  # page + exact text span it came from
```

## How it works

```mermaid
flowchart LR
    F[File or ZIP or email] --> D[Detect real type<br/>from the bytes]
    D --> T{PDF?}
    T -- yes --> TR[Triage each page:<br/>digital / scanned / mixed / garbled / blank]
    T -- no --> C1[Engine chain for that file type]
    TR --> C2[Engine chain for that page kind]
    C1 --> Q{Text quality<br/>good enough?}
    C2 --> Q
    Q -- no --> N[Try the next engine]
    N --> Q
    Q -- yes --> DOC[One Document:<br/>pages, blocks, tables, decision log]
    DOC --> MD[Markdown / JSON]
    DOC --> EX[Extract fields with a local LLM]
    EX --> GR[Grounding + validation<br/>confidence + needs_review]
```

## Web UI (`doctordoc ui`)

```bash
uv pip install -e ".[ui]"        # Streamlit
doctordoc ui                     # opens http://localhost:8501; pass --config my.yaml to change settings
```

Pure Python (Streamlit), no JavaScript build. Upload one or many files, or a ZIP; every file is
written to a temporary folder, parsed, and deleted: nothing is stored. Four tabs:

- **Parse**: detected type (and a warning if the extension lies), one chip per page (digital,
  scanned, mixed, garbled, blank) with page navigation, the page picture next to the extracted
  blocks (real tables), a tree of the files inside a ZIP or email, and the **Output** panel
  (rendered Markdown, raw Markdown, JSON, tables; copy and download; one ZIP of everything for batches).
- **Router log**: why each engine was used, rejected, skipped or failed, with quality and seconds.
- **Extract**: pick a schema (invoice, receipt, drilling report, generic form); every field with
  confidence, grounded or not, and page; click **Source** to see the exact text it came from.
- **Compare engines**: runs each installed engine alone on the same file and shows them side by
  side, with time and text-quality score, and marks the engine the router chose.

The sidebar has the OCR engine (Auto, or force one), **Local only (private)** (on by default; turning
it off allows the cloud fallback and shows a warning that pages would leave your machine), the
local Ollama model, the mixed-page merge switch and an on/off list of engines. To host a small public
demo see [docs/deploy-demo.md](docs/deploy-demo.md). The look is from
[docs/design/mockup.png](docs/design/mockup.png), a design mockup (made-up data, not a screenshot).

## Extraction output format

`doctordoc extract FILE --format json` prints this, the Extract tab downloads the same thing, and
the future API will return it. One entry per field, then the review list and the problems:

```json
{
  "schema": "Invoice",
  "model": "llama3.2:3b",
  "fields": [
    {"field": "invoice_number", "value": "RI-2025-1047", "confidence": 1.0,
     "grounded": true, "page": 1, "source_text": "RI-2025-1047"},
    {"field": "currency", "value": "USD", "confidence": 0.25,
     "grounded": false, "page": null, "source_text": null},
    {"field": "total", "value": 40000.0, "confidence": 0.95,
     "grounded": true, "page": 1, "source_text": "40,000.00"}
  ],
  "needs_review": ["currency"],
  "validation_issues": [],
  "error": null
}
```

This is a real run (shortened to three fields) on `examples/invoice_digital.pdf` with `llama3.2:3b`.
Note `currency`: the model said `USD`, but the invoice never states a currency, so it is not
grounded, its confidence is low, and it is on the review list. A validation issue looks like
`{"field": "total", "rule": "required", "message": "'total' is required but empty", "severity": "error"}`.

`field` is a path (`line_items[0].amount` for list items). `grounded: false` means the value is
not in the document text (the model made it up): `page` and `source_text` are then `null`.
`validation_issues[].severity` is `error` or `warning`. `error` is `null` unless extraction failed.

## How the router decides

1. **Detect** the real type from the bytes. A `.pdf` that is really a DOCX is handled as a DOCX, with a warning.
2. **Triage** every PDF page: *digital* (good text layer), *scanned* (no text layer), *mixed* (text plus large pictures), *garbled* (text layer exists but is nonsense), *blank*.
3. Each kind has an **engine chain**, which you can edit in `doctordoc.yaml`. The defaults:

   | Page or file kind | Chain (first engine is tried first) |
   |---|---|
   | digital PDF page | `docling` -> `pdf_text` |
   | mixed PDF page | `docling` -> `pdf_text`, plus OCR; the better result is kept and text that only the other engine found is merged in (`merge_mixed_pages`) |
   | scanned PDF page / image | `paddle_vl` -> `rapidocr` -> `tesseract` -> `cloud_vlm` |
   | garbled PDF page | OCR chain first, `pdf_text` last |
   | DOCX / PPTX | `docling` -> `native` -> `markitdown` |
   | HTML | `native` -> `docling` -> `markitdown` |
   | XLSX, CSV, JSON, XML, TXT, MD | `native` -> `markitdown` |
   | email (.eml) | `native` (attachments are parsed again as their own files) |

4. An engine's output is **accepted only if its text quality score passes** (`quality_threshold`, default 0.55). Otherwise the next engine in the chain is tried.
5. Every attempt is written to a **decision log** (`--explain`): which engine, accepted or rejected, why, the quality score, the seconds. A missing engine is skipped and logged, never a crash.
6. RapidOCR and Tesseract first find **which way up** a page is. A page that already reads well (score 0.75 or more,
   `rotation_early_stop`) is read once; otherwise it is read at 0, 90, 180 and 270 degrees and the best angle is kept.
7. Only one heavy model is kept in memory at a time, and it is released after each file.
8. Cloud fallback (`cloud_vlm`) is **off by default**. If you switch it on, page images are sent to the server you configure.

## Engines and licenses

The default engines are all MIT / Apache / BSD, so you can use them commercially.

| Engine | Used for | License | Default? |
|---|---|---|---|
| native (python-docx, openpyxl, python-pptx, stdlib) | DOCX, XLSX, PPTX, CSV, JSON, HTML, email | MIT / BSD / PSF | yes |
| pdf_text (pypdfium2 / PDFium) | digital PDF text, page rendering | Apache-2.0 / BSD-3 | yes |
| [Docling](https://github.com/docling-project/docling) | digital PDF layout and tables, Office files | MIT | yes (extra `docling`) |
| [MarkItDown](https://github.com/microsoft/markitdown) | fallback for Office files | MIT | yes (extra `markitdown`) |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) | scanned pages | Apache-2.0 | yes (extra `ocr`) |
| Tesseract | scanned pages, last local fallback | Apache-2.0 | yes (needs the system package) |
| PaddleOCR-VL | scanned pages (best quality, in theory) | Apache-2.0 | **experimental**, needs 8+ GB RAM or a GPU |
| cloud_vlm | optional cloud fallback you configure | depends on the service | off |
| Marker | optional plugin, **not written yet** | code GPL-3.0; model weights have revenue/funding limits for commercial use | no |
| MinerU | optional plugin, **not written yet** | custom license with conditions (check it before commercial use) | no |

PyMuPDF is **deliberately not used**: it is AGPL, which would force every user of DoctorDoc to open-source their own code.
Marker and MinerU will only ever be optional plugins that you install yourself, after reading their licenses. Check the current terms of any engine before you ship it in a product.

## Benchmarks

Everything here is measured on **small sets of synthetic files made by this project**. Read the numbers as "does it
work as designed", not as accuracy on your documents.

### Router against single engines

These tables are from the full run on the first 36 files, **before** rotation correction was added.

36 synthetic files (made-up companies, wells and numbers; fixed seed; ground truth for every file), CPU only,
every installed engine run **alone** and then the **router**. Text = 1 minus edit distance on letters and
digits; fields = `llama3.2:3b` through the same pipeline. Full tables, per-file CSV and speed:
[`benchmarks/results/RESULTS.md`](benchmarks/results/RESULTS.md).

| Document type | best single engine | router | what a single engine gets wrong |
|---|---|---|---|
| digital PDF | 1.00 (pdf_text / docling) | 1.00 | nothing: ties |
| scanned PDF (noise, blur, tilt) | 0.97 (rapidocr) | 0.97 | text-layer engines read 0.00 |
| garbled PDF (junk hidden text layer) | 1.00 (rapidocr) | 1.00 | text-layer engines read 0.25 |
| mixed PDF (text + picture of text) | **1.00 (tesseract)** | 0.98 | text-layer engines miss the picture (0.84) |
| table over two pages (cells correct) | 1.00 (docling) | 1.00 | others find no table at all (0.00) |
| docx / pptx / html / csv / xlsx | 0.99-1.00 | 0.99-1.00 | ties; engines cannot open every type |
| email with attachments, zip | n/a | 0.95 / 1.00 | single engines cannot open the attachments |
| **all 34 readable files: text** | 0.51 (docling) | **0.99** | |
| **invoice + drilling fields** | 0.68 (docling) | **0.99** | |

**How to read this honestly**

- The router's big lead over "all files" is mostly *breadth*: no single engine opens every format or page kind.
  The fair test of routing quality is the per-type rows, and there the router **ties** the best engine in most
  rows, **wins** where the page kind decides everything (garbled, scanned, tables, fields), and **loses one**:
  on mixed pages Tesseract beat the router (1.00 vs 0.98).
- Every system is given the file's real type by DoctorDoc's detector; without it the single engines would do worse.
- The set is small, synthetic and made by the same project that is being tested. It shows the router works as
  designed, not how it does on your documents. Run `python scripts/make_samples.py && python -m benchmarks.run
  [--fields]` to reproduce it, and add your own files to compare.
- Nobody reads tables from scans (0.00 for every system): OCR output has no table structure yet.
- `table_2page` text caps at 0.97 for everyone: page 2 repeats the table header, which is real text that the
  ground truth does not list.
- Speed is one run on one machine and excludes model loading. Text-layer reading is about 250 pages/s;
  Docling about 0.5 pages/s; OCR about 0.4-1.6 pages/s on CPU.

Since that run, the mixed-page row has changed. RapidOCR's built-in "flip upside-down lines" step was turning a good
line into junk; with it off (part of the rotation fix), a re-run of the 3 mixed files scored 1.00 for the router,
level with Tesseract.

Two real router bugs were found by this benchmark and fixed: a junk text layer was being merged back on top of good
OCR text, and a picture covering under 35% of a page (e.g. a stamp) never triggered OCR (the limit is now 10%).

### Rotated scans and phone photos

12 more files: 6 scans turned 90, 180 or 270 degrees, and 6 **imitation** phone photos made by a script (tilted view,
uneven light, JPEG), 3 upright and 3 turned. They are not real photos. Router only:

| files | text, no correction | text, with correction | fields, no correction | fields, with correction | seconds per page, none / always 4 reads / with early stop |
|---|---:|---:|---:|---:|---|
| turned scans (6) | 0.19 | 1.00 | 0.54 | 1.00 | 3.2 / 10.4 / 9.7 |
| turned photos (3) | 0.19 | 0.99 | 0.22 | 1.00 | 3.2 / 10.7 / 10.6 |
| upright photos (3) | 0.99 | 0.99 | 1.00 | 1.00 | 3.2 / 10.6 / **3.0** |

- **The speed cost:** a turned page takes about **3 times longer** (about 10 s instead of 3 s on one CPU). An upright
  page that passes the early stop costs about what it did before. A poor but upright page that scores under 0.75 also
  pays for all four reads.
- With the early stop, the text of all 36 outputs (12 files; router, RapidOCR and Tesseract) was byte-for-byte the same
  as with four reads. Fields were measured on the four-read run; memos have no fields.
- **Picking the angle with the highest OCR confidence does not work.** It chose the right angle on 10 of 16 test pages
  with RapidOCR and 5 of 16 with Tesseract, because both quietly straighten sideways lines and read them confidently,
  in the wrong order. Counting confidence only for text that lies horizontally chose right on 16 of 16.
- **These numbers are optimistic.** The angle rule and the 0.75 cutoff were designed on pages from this same set.
  They have **not** yet been tested on a new set.

### How often the quality score is fooled

The router accepts an engine's output when its text-quality score is at least 0.55. The score never sees the true
text. Checked against the truth on **119 single-engine outputs** from 46 readable files, calling an output bad when
text accuracy is under 0.90:

| | bad outputs | bad ones the score **accepted** | accepted outputs that were bad |
|---|---:|---:|---:|
| before the rotation fix | 41 | **37 (90%)** | 32% |
| after it | 23 | **19 (83%)** | 17% |

- Before the fix, an output that was only 0.12 accurate scored a perfect 1.00. The only bad outputs it caught (4) came
  from junk hidden text layers, which is what it was built for.
- The "after" row is better because there were fewer bad outputs, **not** because the check improved.
- With a stricter bar (bad = under 0.99), it accepted 93% of bad outputs before the fix.
- These shares depend on which files are in the set. **A high quality score does not mean the text is right.**

### Is Docling better than the simple reader on digital PDFs?

**For tables, yes. For text, no.** New digital PDFs, 5 of each type (`python -m benchmarks.digital_pdfs [--fields]`).
Each cell is simple reader (`pdf_text`) / Docling. "Lines kept" is the share of titles, labelled values and paragraphs
that stay a block of their own.

| document type (files) | text | lines kept | table cells | fields | seconds per file |
|---|---|---|---|---|---|
| invoice (5) | 1.00 / 1.00 | 1.00 / 0.23 | 0.00 / 1.00 | 0.97 / 1.00 | 0.01 / 2.7 |
| drilling report (5) | 1.00 / 1.00 | 1.00 / 0.10 | 0.00 / 1.00 | 1.00 / 1.00 | under 0.01 / 2.1 |
| memo, no table (5) | 1.00 / 1.00 | 1.00 / 1.00 | - | - | under 0.01 / 1.1 |
| rate table, 2 pages (5) | 0.97 / 0.97 | - | 0.00 / 1.00 | - | 0.01 / 17.4 |
| invoice, 2 pages (5; fields on 3) | 0.98 / 0.98 | 1.00 / 0.62 | 0.00 / 1.00 | 1.00 / 1.00 | 0.01 / 11.4 |

- Docling often glues several labelled lines into one paragraph. Fields did not suffer on these files, but the
  Markdown is harder to read.
- The 0.97 against 1.00 on invoice fields is **one** field; it is too small a difference to mean anything.
- Docling stays first because the simple reader finds no tables at all. On pages without tables it adds only time.
- All of these are simple one-column pages. **Multi-column and complex layouts were not tested.**

## Roadmap

Ideas that are **not built**:

- **Text from the simple reader plus tables from Docling** on digital PDFs: keep the simple reader's lines and put
  Docling's tables in their places.
- **A second check to catch wrong text**, because the quality score only catches junk. For example, comparing two
  engines on the same page. None has been tried or measured.
- An API, a Docker image and a published CI setup.

## Screenshots

_Coming with the Streamlit UI (`doctordoc ui`): Parse tab, Router log, Extract tab, Compare engines._

## Known limitations

Honest list, from testing on synthetic documents so far:

- **OCR has no table structure.** RapidOCR and Tesseract return one line per text block. Only Docling finds real tables, and only on digital pages. A scanned invoice table comes out as lines of text, not as a table.
- **Rotation correction has limits and a cost.** A turned page, or a poor one scoring under 0.75, is read four times: about 10 s instead of 3 s per page on one CPU. `fix_rotation: false` turns it off. It handles quarter turns of left-to-right text only: no correction of other angles or of a curved or strongly tilted page, and it was designed and measured on synthetic pages, including imitations of phone photos, not real ones. PaddleOCR-VL and the cloud fallback do not use it.
- **PaddleOCR-VL is experimental.** It is written against the library's source but is not proven here. On a machine with about 6 GB of RAM and no GPU build of PaddlePaddle, the model did not fit in memory (the process was killed after loading the weights). It is off by default on CPU. It needs 8+ GB of RAM or a GPU build of PaddlePaddle.
- **Grounding is not proof of correctness.** Every extracted value is checked against the document text, so the model cannot invent a number that is not there. But a value that is copied from the text can still sit in the wrong field (for example the due date put into "invoice date"). `needs_review` lowers that risk; it does not remove it.
- **Docling is first for digital PDFs only because of tables.** It reads text no better than the simple `pdf_text` engine and often merges separate lines into one paragraph ([numbers above](#is-docling-better-than-the-simple-reader-on-digital-pdfs)). It also splits inline bold into separate paragraphs, which is why HTML defaults to `native`.
- **The "text quality" score catches junk text, not wrong text.** It accepted 37 of 41 bad outputs in one measurement ([details above](#how-often-the-quality-score-is-fooled)). Tesseract dropped a whole line from a clean PDF and still scored 1.00. Do not read a high score as "correct". The Compare tab exists to see this with your own eyes.
- OCR reads a phone photo mostly well but can swap look-alike characters (`RI-2025-1047` came out as `R1-2025-1047`) and mix up the order of address and table lines.
- Docling puts a PDF's title block after the body in one of our samples (the vendor name appeared at the end). Reading order is not always right.
- Excel formulas without a saved result (a file written by a script, not saved by Excel) read as empty cells.
- The UI "Copy" button opens a small box with Streamlit's own copy icon (two clicks), not a one-click button. The UI was checked in Chromium only, and not on a phone.
- Engine comparison and the page picture work for uploads and for files directly inside a ZIP or email, not for deeper nesting.
- **Field extraction has time and length limits.** A model reading junk text can keep writing for many minutes, so by default an answer stops at 4096 tokens (`llm.max_output_tokens`), a call at 300 s (`llm.timeout`) and one document at 600 s in total (`llm.max_seconds_per_document`), with one retry. A document that hits a limit returns an error and no fields, not a partial result. Real long documents can hit them too: on our 4 GB laptop GPU a 45-line invoice took about 2 minutes, so lists of about 100 lines or more, or a CPU-only machine, will need higher limits.
- OCR engines sometimes glue words together across wide gaps (for example `08:30-24:00Drilled`). Grounding tolerates this, but the Markdown shows it.
- Field extraction uses a small local model (`llama3.2:3b` by default) and has only been measured on small synthetic sets (invoices and drilling reports).
- `.msg` (Outlook) attachment reading is untested because there was no sample file.
- English OCR only for now (`ocr_languages: eng`).
- No handwriting, no formulas, and no figure understanding.

## License

Apache-2.0.
