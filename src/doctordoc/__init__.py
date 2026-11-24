"""DoctorDoc: one door for any document.

from doctordoc import parse
doc = parse("report.pdf")
print(doc.to_markdown())
"""

from doctordoc.config import Config
from doctordoc.models import (
    BBox,
    Block,
    BlockType,
    Decision,
    Document,
    Page,
    PageKind,
    Table,
)
from doctordoc.pipeline import parse, parse_many

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
