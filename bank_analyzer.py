"""
Command-line runner for the bank statement analyzer.

Uses the SAME parsers/categorizer as the web server (analyzer_engine.py),
so results are identical and there is no duplicate logic to keep in sync.

Usage:
    python bank_analyzer.py statement1.pdf [statement2.pdf ...] [--password PASS]
"""
import argparse
import os

import pandas as pd

from analyzer_engine import (
    detect_bank_format,
    parse_federal_pdf,
    parse_hdfc_pdf,
    parse_axis_pdf,
    parse_idfc_pdf,
    parse_sbi_pdf,
    parse_icici_pdf,
    parse_universal_pdf,
    tag_recurring_salary,
    compute_underwriting_insights,
)


def parse_any(path, password=None):
    with open(path, "rb") as f:
        content = f.read()
    det = detect_bank_format(file_bytes=content, password=password)
    if det.get("is_encrypted") and (not password or det.get("auth_failed")):
        raise ValueError(f"{path}: password protected / wrong password")
    bank = det.get("detected_bank", "Generic")

    if "Federal" in bank:
        recs, meta = parse_federal_pdf(content, password=password)
    elif "SBI" in bank:
        recs, meta = parse_sbi_pdf(content, password=password)
    elif "Axis" in bank:
        recs, meta = parse_axis_pdf(content, password=password)
    elif "HDFC" in bank:
        recs, meta = parse_hdfc_pdf(content, password=password)
    elif "IDFC" in bank:
        recs, meta = parse_idfc_pdf(content, password=password)
    elif "ICICI" in bank:
        recs, meta = parse_icici_pdf(content, password=password)
    else:
        recs, meta = parse_universal_pdf(content, password=password, detected_bank=bank)

    if not recs:
        recs, meta = parse_universal_pdf(content, password=password, detected_bank=bank)
    return tag_recurring_salary(recs), meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--password", default=None)
    args = ap.parse_args()

    for path in args.pdfs:
        if not os.path.exists(path):
            print(f"File not found: {path}")
            continue
        try:
            recs, meta = parse_any(path, args.password)
        except Exception as e:
            print(f"{path}: FAILED -> {e}")
            continue

        print("\n" + "=" * 60)
        print(f"{path}  |  {meta['bank']}  |  A/C {meta['account_no'] or '-'}  |  {len(recs)} rows")
        print("=" * 60)
        if not recs:
            print("No transactions parsed.")
            continue

        ins = compute_underwriting_insights(recs, meta)
        rec = ins["reconciliation"]
        print(f"Reconciled: {rec['is_reconciled']} (variance {rec['variance']})")
        sal = ins["salary_audit"]
        print(f"Salary credits: {sal['credits_count']} | employers: {sal['verified_employers']} | avg: {sal['avg_monthly_salary']}")
        print(f"Bounces: {ins['bounce_audit']['bounce_count']} | EMI lenders: {ins['debt_and_emi']['active_lenders']}")

        df = pd.DataFrame(recs)
        print("\nCategory summary:")
        print(df.groupby("category")[["debit", "credit"]].sum().to_string())


if __name__ == "__main__":
    main()