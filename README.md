# DoctorDoc

**One door for any document.** Drop in a PDF (digital, scanned, garbled), DOCX, XLSX, PPTX, CSV,
JSON, HTML, email or ZIP. DoctorDoc works out what it is, picks the best engine for each file
*and each page*, returns clean Markdown/JSON in one format, and can extract the fields you ask
for, with proof of where every value came from.

> Status: early. Phase 1 (parsing + router) and Phase 2 (field extraction) are done.
> Benchmarks, API/UI and the full docs come next. Apache-2.0, fully local by default.

## Quickstart

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[ocr,docling,markitdown]"     # extras are optional: see `doctordoc engines`

doctordoc parse report.pdf --explain              # Markdown + the router's decision log
doctordoc triage report.pdf                       # what kind is each page?
doctordoc engines                                 # which engines are installed, and their licenses

ollama pull llama3.2:3b                           # for field extraction (local, ~2 GB)
doctordoc extract invoice.pdf --schema invoice
```

```python
from doctordoc import parse
from doctordoc.extract import extract

doc = parse("scan.pdf")
print(doc.to_markdown())
result = extract(doc, "invoice")               # or your own Pydantic model
print(result.needs_review)                     # fields a human should check
print(result.field("total").source)            # page + exact text span it came from
```

## How the router decides

1. **Detect** the real type from the bytes (a `.pdf` that is really a DOCX is handled as a DOCX, with a warning).
2. **Triage** every PDF page: digital / scanned / mixed / garbled / blank (text layer, picture coverage, text quality).
3. Each kind has an **engine chain** (editable in `doctordoc.yaml`), e.g. scanned: `paddle_vl -> rapidocr -> tesseract -> cloud_vlm`.
4. An engine's output is **accepted only if its text quality passes**; otherwise the next engine is tried.
   Every attempt is logged (`--explain`). Missing engines are skipped and logged, never a crash.

## Known limitations (so far)

- PaddleOCR-VL needs a GPU build of PaddlePaddle; it is written but not yet run end to end.
- OCR output has no table structure (one line per block); Docling finds tables in digital pages only.
- Pages rotated by 90/270 degrees inside a scan are not auto-corrected.
- Extraction was tested on a handful of synthetic documents, not benchmarked yet.
- `.msg` attachment reading is untested (no sample file); Marker/MinerU plugins are not written yet.
