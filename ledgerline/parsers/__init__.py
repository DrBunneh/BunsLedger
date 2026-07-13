"""Content-based source detection and dispatch.

Detection reads the file's content (CSV header / PDF marker), not its name, because
both statements are PDFs and exports get renamed. sources.yaml documents the same
registry for humans; the runtime rules live here to keep detection dependency-light.
"""
from __future__ import annotations

from pathlib import Path

from ..models import CanonicalTxn
from . import monzo_csv

_MONZO_HEADER = "id,created,title,subtitle,amount,currency,categories"


def detect(path: str | Path) -> str:
    """Return the source key ('monzo' | 'nationwide' | 'aqua') or raise."""
    p = Path(path)
    head = p.open("rb").read(4096)
    if head[:5] == b"%PDF-":
        text = _pdf_first_text(p)
        if "aquacard.co.uk" in text:
            return "aqua"
        if "FlexDirect" in text or "07-02-46" in text:
            return "nationwide"
        raise ValueError(f"Unrecognised PDF source: {p.name}")
    first_line = head.decode("utf-8-sig", errors="ignore").splitlines()[0].strip()
    if first_line.replace(" ", "") == _MONZO_HEADER:
        return "monzo"
    raise ValueError(f"Unrecognised source: {p.name}")


def parse(path: str | Path, source: str | None = None) -> list[CanonicalTxn]:
    source = source or detect(path)
    if source == "monzo":
        return monzo_csv.parse(path)
    if source == "aqua":
        from . import aqua_pdf
        return aqua_pdf.parse(path)
    if source == "nationwide":
        from . import nationwide_pdf
        return nationwide_pdf.parse(path)
    raise ValueError(f"No parser for source: {source}")


def _pdf_first_text(path: Path) -> str:
    import fitz  # PyMuPDF, only needed for PDF sources
    with fitz.open(str(path)) as doc:
        return doc[0].get_text()
