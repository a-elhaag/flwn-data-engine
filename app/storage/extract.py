"""Turn an uploaded file into text. Text files are decoded, PDFs read with pypdf, and scanned
pages and images are read by the Cohere Parse model (which only accepts images).

Anything that cannot yield text raises Unindexable with a reason the user can read.
"""

import io
import logging
from dataclasses import dataclass, field

import pypdfium2
from pypdf import PdfReader

from app.clients import inference
from app.config import settings
from app.storage.chunking import Section
from app.storage.errors import Unindexable

logger = logging.getLogger(__name__)
TEXT_TYPES = {
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/javascript",
    "application/sql",
    "application/x-sh",
    "application/toml",
}
TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".sql",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".c",
    ".h",
    ".cpp",
    ".cs",
    ".rb",
    ".php",
    ".sh",
    ".ini",
    ".cfg",
    ".env",
    ".rst",
    ".log",
}
MAX_TEXT_CHARS = 10_000_000
MIN_CHARS_PER_PAGE = 20  # fewer than this on average and the PDF is treated as scanned


@dataclass
class Extracted:
    sections: list[Section]
    notes: list[str] = field(default_factory=list)  # things the user should know (partial reads)


def _extension(name: str) -> str:
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def file_type(content_type: str | None, name: str) -> str | None:
    """'pdf', 'image', 'text', or None when nothing can read it."""
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime == "application/pdf" or _extension(name) == ".pdf":
        return "pdf"
    if mime in inference.PARSE_MIME_TYPES:
        return "image"
    if mime.startswith("text/") or mime in TEXT_TYPES or _extension(name) in TEXT_EXTENSIONS:
        return "text"
    return None


def _decode(data: bytes) -> list[Section]:
    text = data.decode("utf-8", errors="replace").replace("\x00", "")
    if len(text) > 100 and text.count("�") > len(text) * 0.1:
        raise Unindexable("the file looks like binary data, not text")
    text = text[:MAX_TEXT_CHARS].strip()
    if not text:
        raise Unindexable("the file is empty")
    return [Section(text)]


def _pdf_text(data: bytes) -> tuple[list[Section], int]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise Unindexable("the PDF is password-protected")
        pages = len(reader.pages)
    except Unindexable:
        raise
    except Exception as exc:
        raise Unindexable("the file is not a readable PDF") from exc
    if pages > settings.INDEX_MAX_PAGES:
        raise Unindexable(f"{pages} pages is over the {settings.INDEX_MAX_PAGES} page limit")
    sections = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # one bad page must not lose the rest
            text = ""
        if text:
            sections.append(Section(text, number))
    return sections, pages


def _read_scanned_pages(data: bytes, pages: int) -> Extracted:
    """Render each page to an image and let the Parse model read it. Slow and quota-limited,
    so only the first INDEX_MAX_SCANNED_PAGES pages are read."""
    limit = min(pages, settings.INDEX_MAX_SCANNED_PAGES)
    sections: list[Section] = []
    document = pypdfium2.PdfDocument(data)
    try:
        for number in range(limit):
            image = document[number].render(scale=1.6).to_pil().convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            text = inference.parse_image(buffer.getvalue(), "image/png")
            if text:
                sections.append(Section(text, number + 1))
    finally:
        document.close()
    if not sections:
        raise Unindexable("no readable text found in the scanned pages")
    notes = [f"read the first {limit} of {pages} scanned pages"] if pages > limit else []
    return Extracted(sections, notes)


def extract(data: bytes, content_type: str | None, name: str) -> Extracted:
    kind = file_type(content_type, name)
    if kind == "text":
        return Extracted(_decode(data))
    if kind == "image":
        text = inference.parse_image(data, (content_type or "image/png").split(";")[0].strip())
        if not text:
            raise Unindexable("no readable text found in the image")
        return Extracted([Section(text)])
    if kind == "pdf":
        sections, pages = _pdf_text(data)
        if sum(len(section.text) for section in sections) >= MIN_CHARS_PER_PAGE * max(pages, 1):
            return Extracted(sections)
        return _read_scanned_pages(data, pages)
    raise Unindexable(f"no text extractor for {content_type or 'this file type'}")
