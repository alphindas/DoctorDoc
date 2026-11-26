# Example output

[`extract_invoice_digital.json`](extract_invoice_digital.json) is **real output, not tuned**: the full,
unedited standard output of one run (nothing was printed to standard error).

```bash
doctordoc extract examples/invoice_digital.pdf --schema invoice --format json
```

| | |
|---|---|
| Date | 2026-11-26 |
| Model | `llama3.2:3b` (Ollama model ID `a80c4f17acd5`) |
| Ollama version | 0.33.1 |
| Code commit | `a19532c` |
| Settings | defaults (no `doctordoc.yaml`) |

What it gets wrong or cannot back up:

- `total` is empty: it has no entry in `fields`, it is on `needs_review`, and the `required` rule reports it.
- The invoice labels `40,000.00` as "Total" and shows no subtotal. The model put that amount in
  `subtotal`, with confidence 0.95. `subtotal` is **not** on the review list.
- `currency` is `USD`, but it was not found in the document: `grounded: false`, on the review list.

This is one run on one synthetic invoice from this repo. It shows the format and these mistakes; it
does not measure accuracy. Another run, model or version may give a different result.
