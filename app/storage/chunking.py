"""Split extracted text into searchable chunks that remember where they came from.

Chunks follow the document's own structure: paragraphs stay whole, a Markdown heading starts a
new heading path ("Design > Auth > Tokens") that every chunk under it carries, and the page number
of PDFs is kept. Each chunk repeats a little of the one before so an answer that straddles a
boundary is still found. Pure functions: no I/O, easy to test.
"""

import hashlib
import re
from dataclasses import dataclass

TARGET = 1800  # characters per chunk, about 400 tokens
OVERLAP = 200  # characters repeated at the start of the next chunk
HARD_MAX = 2600  # one paragraph longer than this is split
_HEADING = re.compile(r"^(#{1,6})\s+(\S.*?)\s*$")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Section:
    """Text with a known page (PDFs) or none (plain text)."""

    text: str
    page: int | None = None


@dataclass(frozen=True)
class Piece:
    text: str
    heading_path: str | None
    page: int | None
    start: int  # character offsets into the whole extracted text
    end: int

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.heading_path or ''}\n{self.text}".encode()).hexdigest()

    @property
    def embedding_text(self) -> str:
        """What is embedded and reranked: the text with its heading path for context."""
        return f"{self.heading_path}\n{self.text}" if self.heading_path else self.text


@dataclass(frozen=True)
class _Block:
    text: str
    heading_path: str | None
    page: int | None
    start: int
    end: int
    is_heading: bool = False


def _split_long(text: str) -> list[tuple[str, int]]:
    """Cut an oversized paragraph at sentence ends (or, failing that, by length): (text, offset)."""
    sentences, position = [], 0
    for match in _SENTENCE_END.finditer(text):
        sentences.append((text[position : match.start() + 1], position))
        position = match.end()
    sentences.append((text[position:], position))
    parts: list[tuple[str, int]] = []
    current, current_start = "", 0
    for sentence, offset in sentences:
        while len(sentence) > TARGET:  # one huge sentence: cut by length with overlap
            if current:
                parts.append((current, current_start))
                current = ""
            parts.append((sentence[:TARGET], offset))
            sentence, offset = sentence[TARGET - OVERLAP :], offset + TARGET - OVERLAP
        if current and len(current) + 1 + len(sentence) > TARGET:
            parts.append((current, current_start))
            current = ""
        if not current:
            current_start = offset
        current = f"{current} {sentence}".strip() if current else sentence
    if current:
        parts.append((current, current_start))
    return parts


def _blocks(sections: list[Section]) -> list[_Block]:
    blocks: list[_Block] = []
    headings: list[tuple[int, str]] = []
    base = 0
    for section in sections:
        position = 0
        for chunk in _PARAGRAPH_BREAK.split(section.text):
            start = section.text.index(chunk, position) if chunk else position
            position = start + len(chunk)
            text = chunk.strip()
            if not text:
                continue
            heading = _HEADING.match(text.split("\n", 1)[0])
            if heading:
                level = len(heading.group(1))
                headings = [(lv, title) for lv, title in headings if lv < level]
                headings.append((level, heading.group(2)))
            path = " > ".join(title for _, title in headings) or None
            if len(text) > HARD_MAX:
                for part, offset in _split_long(text):
                    blocks.append(
                        _Block(
                            part,
                            path,
                            section.page,
                            base + start + offset,
                            base + start + offset + len(part),
                        )
                    )
            else:
                blocks.append(
                    _Block(
                        text,
                        path,
                        section.page,
                        base + start,
                        base + start + len(text),
                        bool(heading),
                    )
                )
        base += len(section.text) + 2
    return blocks


def chunk_sections(sections: list[Section], max_chunks: int) -> tuple[list[Piece], bool]:
    """Chunks in reading order, and whether the text was cut short at `max_chunks`.

    A chunk never spans a page (so a PDF citation is exact) and a new heading starts a new chunk.
    Its heading path and page describe its own text, not the overlap repeated from the chunk before.
    """
    pieces: list[Piece] = []
    current: list[_Block] = []
    fresh = 0  # blocks before this index are overlap carried over from the previous chunk
    size = 0

    def flush() -> None:
        body = current[fresh:] or current
        anchor = next((block for block in body if not block.is_heading), body[0]) if body else None
        if anchor is not None:
            pieces.append(
                Piece(
                    text="\n\n".join(block.text for block in current),
                    heading_path=anchor.heading_path,
                    page=anchor.page,
                    start=current[0].start,
                    end=current[-1].end,
                )
            )

    def restart(carry: list[_Block]) -> None:
        nonlocal current, fresh, size
        current, fresh = list(carry), len(carry)
        size = sum(len(block.text) + 2 for block in carry)

    for block in _blocks(sections):
        fresh_body = current[fresh:]
        new_section = block.is_heading and any(not earlier.is_heading for earlier in fresh_body)
        new_page = bool(fresh_body) and block.page != fresh_body[-1].page
        too_big = bool(current) and size + 2 + len(block.text) > TARGET
        if fresh_body and (new_section or new_page or too_big):
            flush()
            if len(pieces) >= max_chunks:
                return pieces, True
            carry: list[_Block] = []
            if too_big and not (new_section or new_page):
                total = 0
                for earlier in reversed(current):  # repeat the tail of the previous chunk
                    if total + len(earlier.text) > OVERLAP:
                        break
                    carry.insert(0, earlier)
                    total += len(earlier.text) + 2
                if (
                    total + len(block.text) > TARGET
                ):  # a carried tail must never squeeze out new text
                    carry = []
            restart(carry)
        current.append(block)
        size += len(block.text) + 2
    if current[fresh:]:
        flush()
    return pieces[:max_chunks], len(pieces) > max_chunks
