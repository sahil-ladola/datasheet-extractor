"""Build the small PDF fixtures used by the test suite.

Only the standard library is used, so the fixtures can be regenerated
anywhere with ``python tests/fixtures/make_fixture.py``. The PDFs are
written by hand at the byte level: a catalog, a page tree, one page object
per page, a Helvetica font, and an uncompressed content stream per page.
That is enough for pdfplumber to extract the text, and each file stays
under a few kilobytes.

Two files are produced:

* ``sample_sensor.pdf``: a two-page datasheet. Page one is marketing text,
  page two is the technical data table. Used by the loader, selection and
  extractor tests.
* ``scanned_no_text.pdf``: a single page with no content stream at all,
  standing in for a scanned image. Used to test ``NoTextError``.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent

SAMPLE_PAGES: list[list[str]] = [
    [
        "TN2405 Temperature sensor with display",
        "ifm electronic gmbh",
        "",
        "Product description",
        "Compact temperature transmitter for hygienic and industrial",
        "applications. Robust stainless steel housing, four-digit display,",
        "switching output and analogue output in one device. Suitable for",
        "food and beverage plants, machine tools and process cooling.",
        "",
        "Ordering information and accessories on request.",
    ],
    [
        "Technical data",
        "",
        "Order number TN2405",
        "Application temperature",
        "Operating voltage 18...32 V DC",
        "Output 4...20 mA / IO-Link",
        "Measuring range -50...150 C",
        "Ambient temperature -25...80 C",
        "Protection IP67",
        "Response time 3000 ms",
        "Weight 120 g",
        "Housing stainless steel 1.4404",
    ],
]


def _escape(text: str) -> str:
    """Escape the three characters that are special inside a PDF string."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content_stream(lines: list[str]) -> bytes:
    """Render ``lines`` top to bottom as a PDF text-drawing stream."""
    parts = ["BT", "/F1 11 Tf", "72 740 Td", "13 TL"]
    for line in lines:
        parts.append(f"({_escape(line)}) Tj T*")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1")


def build_pdf(pages: list[list[str]]) -> bytes:
    """Return the bytes of a PDF with one page per entry in ``pages``.

    An entry with no lines produces a page without a content stream, which
    is how a scanned page looks to a text extractor.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = len(objects) + 1  # reserved; filled in after pages are known
    objects.append(b"")  # placeholder

    page_ids: list[int] = []
    for lines in pages:
        page_dict = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >>"
        )
        if lines:
            stream = _content_stream(lines)
            content_id = add(
                b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                + stream + b"\nendstream"
            )
            page_dict += f" /Contents {content_id} 0 R"
        page_dict += " >>"
        page_ids.append(add(page_dict.encode("latin-1")))

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    )
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


def main() -> None:
    """Write both fixture PDFs next to this script."""
    (HERE / "sample_sensor.pdf").write_bytes(build_pdf(SAMPLE_PAGES))
    (HERE / "scanned_no_text.pdf").write_bytes(build_pdf([[]]))
    print("wrote sample_sensor.pdf and scanned_no_text.pdf")


if __name__ == "__main__":
    main()
