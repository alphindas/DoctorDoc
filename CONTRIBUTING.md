# Contributing to DoctorDoc

## Set up

You need [uv](https://docs.astral.sh/uv/). CI uses Python 3.12. Use `uv pip`, not `pip`.

```bash
git clone https://github.com/alphindas/DoctorDoc.git && cd DoctorDoc
uv venv --python 3.12
uv pip install -e ".[dev,ui,ocr,api]"     # what CI installs
```

- If another virtual environment is active, uv installs into that one. Deactivate it first.
- CI also installs the Tesseract program (Ubuntu: `sudo apt-get install tesseract-ocr tesseract-ocr-eng
  tesseract-ocr-osd`). Without it the real Tesseract tests are skipped.
- CI leaves out the `docling` and `markitdown` extras, and their tests skip themselves. On Linux, `docling` brings in
  PyTorch with the NVIDIA libraries, a large download.

## The checks CI runs

Run these before you push:

```bash
uvx ruff@0.17.6 check .
uvx ruff@0.17.6 format --check .
.venv/bin/python -m pytest -q -rs
```

A third CI job installs with **no extras** and parses a file. To repeat it, use a **separate fresh clone**, so that
your working `.venv` is left alone:

```bash
uv venv --python 3.12
uv pip install .
.venv/bin/doctordoc parse examples/invoice_digital.pdf     # the output must contain RI-2025-1047
```

The exact steps are in [ci.yml](.github/workflows/ci.yml). If it and this file differ, `ci.yml` is right.

**The tests need no Ollama.** The model is scripted, or a small fake server on your own computer. Keep it that way: a
test must not need a real model, a GPU or the internet. Add a test for every fix, and see it fail without the fix.

## Workflow

1. Start a branch from an up-to-date `main`.
2. Commit in small steps, with messages that say what changed and why.
3. Push the branch and open a pull request to `main`. CI runs on pull requests and on `main`, so pushing a branch alone
   does not start it.
4. Merge only when **all CI jobs are green**.

When behaviour changes, update the README (also its limits) and the "Unreleased" part of [CHANGELOG.md](CHANGELOG.md).

## Honesty rules

- **Say what does not work.** Put real limits in the README.
- Test on real files and show real output. Do not claim a result you did not measure.
- **Synthetic data only.** Every company, person and number in test data and examples is made up.
- **Never commit real documents, `.env` files, API keys or other secrets.** `.gitignore` covers `.env` files and
  `doctordoc.yaml`, but not a key in a file with another name: read `git status` and your diff before each commit.
- **Every number in the README needs a source:** a saved result file (link it), or a label such as "seen during
  development, no result file saved".
- Do not edit real output by hand. Say when you shorten it. If the code changes later, add a note; do not rewrite the
  old output.

## Benchmark rules

- **Use a fresh seed** to show how something does on files it was not built on. Seed 1234 was used during development,
  and seed 5678 has a [saved run](benchmarks/results/runs/seed5678-router-fields/RESULTS.md), so pick another.
- **No tuning after seeing the results.** If you change code or settings because of what a run showed, that seed is no
  longer fresh: use a new one for the next claim.
- **Write to new folders and never overwrite saved results.** Give both `--data` and `--out` folders that do not exist
  yet, for example:

  ```bash
  .venv/bin/python -m benchmarks.run --seed 9999 --data ~/doctordoc-run-9999/data --out ~/doctordoc-run-9999/results
  ```

  - The default `--out` overwrites the saved `results.csv` and `RESULTS.md` on a full run, and the default `--data`
    overwrites saved output texts.
  - `--seed` is used only to build the files. If the `--data` folder already has them, the seed is **ignored** without a
    warning.
  - With `--fields` (needs Ollama), the run also uses and updates the answer cache
    `benchmarks/results/llm_cache.json`, which git ignores.
  - `benchmarks.digital_pdfs` has **no `--out`** and can overwrite saved CSVs: run it only in a separate clone.
- Save the code commit, the command and the settings with the results. A full run takes long and is not part of CI.

## License rules

- DoctorDoc is Apache-2.0.
- A default engine must be under the MIT, Apache or BSD license, as in the
  [license table](README.md#engines-and-licenses).
- **No AGPL code, such as PyMuPDF.** It would force users of DoctorDoc to publish their own code.
- **Check the license of any new engine** before you add it: the code, the model weights and what it pulls in. Add it to
  the [license table](README.md#engines-and-licenses). An engine with limits may only be an optional plugin that users
  install themselves.
