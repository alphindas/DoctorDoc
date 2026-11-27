"""DoctorDoc: one door for any document.

from doctordoc import parse
doc = parse("report.pdf")
print(doc.to_markdown())
"""

import os

# ONNX Runtime (RapidOCR runs on it) has telemetry that its own Privacy.md says is on by default
# in official builds. This variable turns it off. ONNX Runtime needs it before it starts, so it
# is set here: Python runs this file before any other part of DoctorDoc. setdefault: a value you
# set yourself wins, and ORT_DISABLE_TELEMETRY=0 leaves telemetry on.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

from doctordoc.config import Config  # noqa: E402
from doctordoc.models import (  # noqa: E402
    BBox,
    Block,
    BlockType,
    Decision,
    Document,
    Page,
    PageKind,
    Table,
)
from doctordoc.pipeline import parse, parse_many  # noqa: E402

__version__ = "0.1.0"

__all__ = [
    "BBox",
    "Block",
    "BlockType",
    "Config",
    "Decision",
    "Document",
    "Page",
    "PageKind",
    "Table",
    "parse",
    "parse_many",
    "__version__",
]
