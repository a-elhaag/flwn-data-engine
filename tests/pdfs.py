"""Build small, genuinely valid PDFs for tests."""


def make_pdf(pages: list[str]) -> bytes:
    """One text line per page. An empty string makes a blank page, which is what a scan looks like
    to a text extractor (the page has no text layer)."""
    count = len(pages)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{4 + 2 * i} 0 R' for i in range(count))}] /Count {count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index, text in enumerate(pages):
        content = f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode() if text else b""
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Contents {5 + 2 * index} 0 R"
                " /Resources << /Font << /F1 3 0 R >> >> >>"
            ).encode()
        )
        objects.append(
            f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream"
        )
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def make_docx(
    paragraphs: list[tuple[str | None, str]], table: list[list[str]] | None = None
) -> bytes:
    """A minimal Word file. Each paragraph is (style name or None, text)."""
    import io
    import zipfile

    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    ids = {name: f"S{i}" for i, name in enumerate({s for s, _ in paragraphs if s})}
    styles = "".join(
        f'<w:style w:styleId="{sid}"><w:name w:val="{name}"/></w:style>'
        for name, sid in ids.items()
    )
    body = ""
    for style, text in paragraphs:
        props = f'<w:pPr><w:pStyle w:val="{ids[style]}"/></w:pPr>' if style else ""
        body += f"<w:p>{props}<w:r><w:t>{text}</w:t></w:r></w:p>"
    if table:
        rows = "".join(
            "<w:tr>"
            + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row)
            + "</w:tr>"
            for row in table
        )
        body += f"<w:tbl>{rows}</w:tbl>"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/styles.xml", f"<w:styles {ns}>{styles}</w:styles>")
        archive.writestr(
            "word/document.xml", f"<w:document {ns}><w:body>{body}</w:body></w:document>"
        )
    return buffer.getvalue()
