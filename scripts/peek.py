"""Show the structure of a bank export without revealing its content.

    uv run python scripts/peek.py raw/<account>/<file>.csv [rows]
    uv run python scripts/peek.py raw/<account>/<file>.pdf [lines]

CSV: prints the encoding guess, the header line unchanged, and the first rows
masked: letters -> x/X, digits -> 9. Separators, decimal commas, date formats,
signs and quoting stay visible, which is all a parser needs.
PDF: prints the extracted text lines of every page, masked the same way (pages
without text are reported: those are scans and would need OCR).
"""

import re
import sys
from pathlib import Path


def mask(s: str) -> str:
    s = re.sub(r"[A-ZÄÖÜ]", "X", s)
    s = re.sub(r"[a-zäöüß]", "x", s)
    return re.sub(r"\d", "9", s)


def peek_pdf(path: Path, n: int) -> None:
    import pdfplumber

    shown = 0
    with pdfplumber.open(path) as pdf:
        print(f"pages: {len(pdf.pages)}")
        for i, page in enumerate(pdf.pages, start=1):
            lines = (page.extract_text() or "").splitlines()
            print(f"--- page {i}: {len(lines)} text lines" + ("  (no text: scanned?)" if not lines else ""))
            for line in lines:
                if shown >= n:
                    return
                print("   ", mask(line))
                shown += 1


def main() -> None:
    path = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    if path.suffix.lower() == ".pdf":
        return peek_pdf(path, n if len(sys.argv) > 2 else 80)
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    lines = text.splitlines()
    print(f"encoding: {enc}, lines: {len(lines)}, BOM: {raw.startswith(b'\xef\xbb\xbf')}")
    print("header:", lines[0] if lines else "(empty)")
    for line in lines[1:n + 1]:
        print("row:   ", mask(line))


if __name__ == "__main__":
    main()
