# Hosting a demo of the UI (CPU only)

Goal: a public page where people upload a file and see DoctorDoc parse it, using only light
engines (native readers, pdf_text, **RapidOCR**) on a small CPU machine. No Ollama, no GPU.

> **Not tested on the real platforms.** I checked the official docs, and the demo config was
> tested locally (`tests/test_demo_config.py` parses every example with it). I have not
> deployed it. If a step fails, the error log will say which package is missing.

## What must be switched off

| Thing | Why | How |
|---|---|---|
| Docling | pulls in PyTorch (GBs of RAM and disk); a free host will run out | not installed (`requirements.txt` has only `ui` and `ocr`), and not in the demo chains |
| PaddleOCR-VL | did not fit on our CPU-only test machine; it may need more RAM or a GPU build of PaddlePaddle (seen during development, no result file saved) | not installed, not in the chains |
| Cloud fallback | would send visitors' pages to a third party | only keeping it off protects them, and `cloud.enabled: false` is **not enough** in the web UI: see [Keeping the cloud off](#keeping-the-cloud-off) |
| Ollama / Extract tab | there is no model server on a free host | the Extract tab shows "Cannot reach the LLM server". Leave it, or point `llm` at a hosted OpenAI-compatible server and keep the key in the host's secrets (then page text leaves the machine) |
| Big uploads | memory | `max_file_mb: 10` in the demo config |
| Private documents | strangers use it | say on the page that only non-sensitive files should be uploaded (each upload goes to a temporary folder and that copy is deleted after parsing; parsed results stay in the app's memory. With the parse cache off, DoctorDoc keeps no copy on disk. The demo config has no `cache_dir` line, so **check that `DOCTORDOC_CACHE_DIR` is not set on the host**, or add `cache_dir: null`. See [what is kept on disk](../README.md#what-is-kept-on-disk)) |

The demo config is `doctordoc.demo.yaml`; the app reads it from the environment variable
`DOCTORDOC_UI_CONFIG`.

## Keeping the cloud off

**Only keeping the cloud off protects your visitors' pages. A missing API key does not:** the page image is sent
without one.

- `cloud.enabled: false` in `doctordoc.demo.yaml` does **not** keep the cloud off in the web UI. The "Local only"
  switch replaces that setting, and **any visitor can switch it off**.
- In a test with the demo's settings, no API key and a fake cloud server in place of the real address: a visitor who
  switched "Local only" off, and then switched on `cloud_vlm` in the Engines list or chose it as the OCR engine, had
  the page image sent. With "Local only" left on, nothing was sent to the cloud. You cannot make visitors leave it on.
- The demo config sets no `cloud.base_url`, so the address is the default, `https://api.openai.com/v1`.
- **To keep the cloud off in a hosted demo, also set an empty address** in your copy of the config:

  ```yaml
  cloud:
    enabled: false
    base_url: ""
  ```

  With this, `cloud_vlm` could not be switched on or chosen, even with "Local only" off. **The demo config in this
  repository does not have the `base_url` line.**
- The sidebar says "Local only" keeps pages on "this computer". In a hosted demo that is **your server**: a visitor's
  file is uploaded to it.

(Seen during development with Streamlit's test runner on one machine, no result file saved. In Chromium, only the
path through the Engines list and Compare engines was repeated.)

## Streamlit Community Cloud (simplest)

1. Push the repo to a public GitHub repository.
2. At share.streamlit.io choose **Create app**, pick the repo and branch, and set the main
   file to `src/doctordoc/ui/app.py`.
3. Under **Advanced settings** choose Python 3.12 and add this to **Secrets**:
   ```toml
   DOCTORDOC_UI_CONFIG = "doctordoc.demo.yaml"
   ```
   (Streamlit passes top-level secrets to the app as environment variables.)
4. The repo's `requirements.txt` (`.[ui,ocr]`) installs DoctorDoc with RapidOCR. Community
   Cloud uses only the first dependency file it finds, so do not add another one.
5. If the log says `libGL.so.1: cannot open shared object file`, add a file `packages.txt`
   containing one line, `libgl1` (OpenCV, which RapidOCR uses, needs it).
6. The theme comes from `.streamlit/config.toml`, which Community Cloud reads from the repo root.

Free apps have small memory (the official page says limits can change without notice).
RapidOCR is light, but a 10 MB scanned PDF with many pages can still be slow: keep uploads small.

## Hugging Face Spaces

Hugging Face has deprecated the built-in Streamlit option: create a **Docker** Space instead.
Add a `Dockerfile` to the Space repo like this (CPU, no GPU):

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir ".[ui,ocr]"
ENV DOCTORDOC_UI_CONFIG=doctordoc.demo.yaml
EXPOSE 7860
CMD ["streamlit", "run", "src/doctordoc/ui/app.py", "--server.port=7860", "--server.address=0.0.0.0"]
```

and put this at the top of the Space's `README.md`:

```yaml
---
title: DoctorDoc
sdk: docker
app_port: 7860
---
```

The Space listens on port 7860. Keep the Space public only if you accept strangers uploading files.

## Keep the demo honest

Put the sample files from `examples/` in the Space description, and say on the page that this is
the light CPU setup: scanned pages use RapidOCR, and neither OCR nor the plain PDF reader finds real tables (in a local test the digital invoice's table came out as text lines; with Docling it is a real table), and Docling, field
extraction and the engine comparison for heavy engines are only available when you run
DoctorDoc yourself.
