"""
PII-safe layout report for a bank-statement PDF that fails to parse.

    python diagnose_statement.py "C:\path\statement.pdf" [password]

Letters are printed as 'a' and digits as '9', so you can paste the whole output safely: it shows the
STRUCTURE (columns, header words, row shapes), never names, account numbers, amounts or narrations.
"""
import re
import sys
from collections import defaultdict

import pdfplumber
import pymupdf

import analyzer_engine as ae

HEADER_WORDS = ("date", "balance", "narration", "remarks", "particulars", "debit", "credit",
                "withdrawal", "deposit", "cheque", "chq", "description", "amount", "ref")


def mask(t):
    return re.sub(r"\d", "9", re.sub(r"[A-Za-z]", "a", t))


def main(path, password=None):
    data = open(path, "rb").read()
    det = ae.detect_bank_format(file_bytes=data, password=password)
    print("detected:", det.get("detected_bank"), "|", det.get("layout"), "| pages:", det.get("pages"))
    print("diagnose:", ae.diagnose_pdf(data, password=password))

    doc = pymupdf.open(stream=data, filetype="pdf")
    if doc.is_encrypted and password:
        doc.authenticate(password)
    p0 = doc[0].get_text()
    print("\n-- bank-name signals on page 1 (offset of first hit, -1 = absent)")
    for key in ("ICICI", "HDFC", "AXIS", "SBI", "STATE BANK", "FEDERAL", "IDFC", "KOTAK", "IFSC", "ICIC0", "CREDIT CARD", "STATEMENT"):
        print(f"   {key:12} {p0.upper().find(key)}")

    print("\n-- page 1 text: first 12 lines (masked)")
    for ln in [l for l in p0.split("\n") if l.strip()][:12]:
        print("   ", mask(ln.strip())[:90])

    print("\n-- header-like lines on pages 1-2 (these are generic words, not PII)")
    for pno in range(min(2, len(doc))):
        for ln in doc[pno].get_text().split("\n"):
            low = ln.lower()
            if sum(w in low for w in HEADER_WORDS) >= 2:
                print(f"   p{pno + 1}: {ln.strip()[:100]}")

    print("\n-- first 4 date-led lines: token x-positions + masked shape (pages 1-3)")
    shown = 0
    for pno in range(min(3, len(doc))):
        lines = []
        for w in sorted(doc[pno].get_text("words"), key=lambda w: (w[1], w[0])):
            if lines and abs(w[1] - lines[-1][0]) <= 3:
                lines[-1][1].append(w)
            else:
                lines.append([w[1], [w]])
        for _, lw in lines:
            lw = sorted(lw, key=lambda w: w[0])
            if any(ae.is_date(w[4]) for w in lw[:4]) and shown < 4:
                print("   ", " ".join(f"{int(w[0])}:{mask(w[4])}" for w in lw)[:200])
                shown += 1
    if not shown:
        print("    (no date-led lines found: dates may be in an unusual format)")

    print("\n-- pdfplumber tables (pages 1-3)")
    with pdfplumber.open(path, password=password) as pdf:
        for pno, page in enumerate(pdf.pages[:3]):
            for label, settings in (("lines", None), ("text", {"vertical_strategy": "text", "horizontal_strategy": "text"})):
                tables = page.extract_tables(settings) if settings else page.extract_tables()
                print(f"   p{pno + 1} strategy={label}: tables={len(tables)} cols={[len(t[0]) if t and t[0] else 0 for t in tables]}")
                for t in tables[:1]:
                    for row in t[:3]:
                        cells = [str(c or "").replace("\n", " ") for c in row]
                        if sum(w in " ".join(cells).lower() for w in HEADER_WORDS) >= 2:
                            print("       header row:", [c[:25] for c in cells])
                        else:
                            print("       row shape :", [mask(c)[:20] for c in cells])

    print("\n-- every parser (counts only)")
    for name, fn in (("universal", lambda: ae.parse_universal_pdf(data, password=password)),
                     ("text_lines", lambda: ae.parse_text_lines_pdf(data, password=password))):
        try:
            recs, meta = fn()
            q = ae.reconcile_statement(recs, meta) if recs else {}
            print(f"   {name:11} rows={len(recs)} reconciled={q.get('is_reconciled')} "
                  f"variance_paise={q.get('variance_paise')} gaps={q.get('gaps')}")
        except Exception as e:
            print(f"   {name:11} CRASH {type(e).__name__}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
