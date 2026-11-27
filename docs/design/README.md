# Design mockup

`mockup.png` is a **design mockup**, not a screenshot. It was drawn to show the look the
Streamlit UI should have (light theme, soft blue-grey background, teal accent, rounded cards).
All the data in it is made up (company, invoice, numbers), and it shows things the real UI
does differently on purpose:

- the model list in the real UI shows the models on your **model server** (by default Ollama on this computer);
- the real switch is labelled **Local only (no cloud OCR)**, not "Local only (private)": it only turns cloud OCR off;
- a rejected engine in the router log is **amber** with its reason, never a green tick;
- the real UI shows **one chip per page** with page navigation;
- the real UI has an **Output panel** (rendered Markdown, raw Markdown, JSON, tables, downloads).

The real UI is in `src/doctordoc/ui/`. Run it with `doctordoc ui`.
