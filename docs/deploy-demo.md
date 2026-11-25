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
| Cloud fallback | would send visitors' pages to a third party | `cloud.enabled: false` in `doctordoc.demo.yaml`, and no API key is set |
| Ollama / Extract tab | there is no model server on a free host | the Extract tab shows "Cannot reach the LLM server". Leave it, or point `llm` at a hosted OpenAI-compatible server and keep the key in the host's secrets (then page text leaves the machine) |
| Big uploads | memory | `max_file_mb: 10` in the demo config |
| Private documents | strangers use it | say on the page that only non-sensitive files should be uploaded (files are processed in memory and a temp folder, then deleted; nothing is stored by DoctorDoc) |

The demo config is `doctordoc.demo.yaml`; the app reads it from the environment variable
`DOCTORDOC_UI_CONFIG`.

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
