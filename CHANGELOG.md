# Changelog

The layout follows [Keep a Changelog](https://keepachangelog.com/). Nothing has been released yet, so everything is
under "Unreleased". "Changed" and "Fixed" compare with earlier states of this repository. The list was made from the git
history and the [README](README.md), which has the details and the limits.

## [Unreleased]

### Added

- Parsing: the real file type is detected from the bytes, each PDF page is triaged, and a router tries engines in order,
  checks the text quality, falls back when needed and keeps a decision log. Engines: native readers, `pdf_text`,
  Docling, MarkItDown, RapidOCR, Tesseract, an optional cloud vision model (off by default) and PaddleOCR-VL
  (experimental).
- Field extraction with an LLM (by default Ollama on your computer): every value is looked for in the document text,
  validation rules run, each field gets a confidence, and doubtful fields go to `needs_review`.
- A web UI (`doctordoc ui`) with engine comparison, example documents, a demo configuration and deploy notes.
- A synthetic benchmark with rotated and photo samples, a quick mode and saved results.
- A local HTTP API (`doctordoc api`) with `/health`, `/engines`, `/parse` and `/extract`. Uploads over `max_file_mb`
  are refused while they arrive, temporary files are deleted after each request, and there is no login.
- A GitHub Actions workflow: ruff, the tests, and a check that an install with no extras can parse a file.
- A warning at startup (`parse`, `extract`, `ui` and the HTTP API) when cloud is enabled: it lists the page kinds and
  file types whose pages could be sent to the cloud server, and how to prevent it. It does not change which engines run.
- The Apache-2.0 license.

### Changed

- **`parse FOLDER -f json` prints one JSON list** when the folder has several files, or none. The `===== path =====`
  lines now go to stderr. A single file still prints one object.
- **The command line's JSON is printed directly to stdout**, no longer through the Rich library: it is not wrapped or
  colored, and text such as `:smile:` stays as it is.
- **The command line's Markdown is printed directly too**: `parse` without `--out` writes the same bytes as `--out`
  (UTF-8, whatever the locale). There is no extra blank line at the end any more. Control characters in a document now
  all reach the terminal.
- **A partial `page_chains`, `file_chains` or `timeouts` is merged over the defaults**, from a config file or an
  environment variable. Before, it replaced the whole table.
  - Leaving a page kind or file type out no longer disables it. Write an empty list, for example `scanned: []`.
  - The default chains for scanned and garbled pages and for images end with `cloud_vlm`. With `cloud.enabled: true`, a
    page kind or file type that your settings leave out can now be sent to the cloud server.
  - A timeout you leave out now has that engine's own default. Before, the router used 60 s.
- **`parse --out` names outputs differently when inputs share a file name**: their folders are put in front, then a
  number if needed. Each `wrote` line now shows the input and the output file.
- On an output encoding that lacks a character, the command line's tables show `?` in its place. JSON is written as
  UTF-8 whatever the locale.
- **NumPy (1.26 or newer) is a core dependency** and is no longer listed in the `api` extra.
- **The HTTP API is stricter.** Requests that it used to accept can now be refused:
  - HTTP 403 when the `Origin` header does not match `Host`, or when `Host` is not `localhost`, `127.0.0.1` or `::1`.
    `api.allowed_hosts` adds names. A non-local `--host` without that list accepts any `Host`. This is not a login.
  - HTTP 503 with `Retry-After` when one request is working and `api.max_waiting` (default 4) others are waiting.
    Before, any number could wait.
- The HTTP API also gained `--no-docs` (or `api.docs: false`) to turn off its docs pages, and a `warnings` list in
  `/extract` that names ignored ZIP contents and email attachments. The command line's JSON has no `warnings`.
- The `api` extra asks for newer minimum versions of its web packages.
- **Length guard for long documents:** a block of text that is too long is now split. With Ollama, a prompt that
  probably does not fit the context window is not sent, and you get an error. If the token counts in a reply look wrong,
  every field goes to review and `needs_review` also lists `document`.
- **Merging the pieces of a long document:** every list item is kept, including repeated ones (before, an item from a
  later piece was dropped if it matched one already collected). When pieces give different values for one single field,
  the first is still used, and the field now gets a `merge_conflict` error and goes to `needs_review`. Its confidence is
  not lowered.
- Field extraction has limits: by default 4096 answer tokens, 300 s per call, 600 s per document and 1 retry (before:
  no answer limit, 600 s and 2 retries).
- A PDF page now counts as mixed when pictures cover 10% of it (before: 35%), so smaller pictures with text get OCR.
- ONNX Runtime's telemetry is off by default (`ORT_DISABLE_TELEMETRY=1`). A value you set yourself, such as `0`, wins.

### Fixed

- The router no longer adds a PDF's hidden text layer on top of OCR output. Only text found by OCR is merged into a
  result that came from the text layer.
- OCR engines now work out which way up a page is, for pages turned by quarter turns.
- `parse -f json` and `extract --format json` could print invalid JSON, and turned `:smile:` into an emoji.
- `parse` without `--out` wrapped long Markdown lines, turned `:smile:` into an emoji, and could stop with
  `UnicodeEncodeError` when the output encoding lacked a character.
- An install with no extras could not parse anything.
- `parse --out` lost results when two inputs had the same file name: one output overwrote the other.
- JSON from `parse` and `extract`, and the command line's tables, could stop with `UnicodeEncodeError`: in some output
  encodings, or when a model's answer held half of a character pair.
- The hint printed by `doctordoc ui` said `doctordoc` instead of `doctordoc[ui]`.
- The web UI said the cloud engine is skipped without an API key. It is not: only keeping the cloud off protects you.
- The web UI and docs said nothing is stored, even with the parse cache on, and called any Extract model "local".
