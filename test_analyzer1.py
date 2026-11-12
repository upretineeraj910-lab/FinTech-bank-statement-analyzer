import os
import tempfile
import pytest

import analyzer_engine as ae
from analyzer_engine import (
    categorize_transaction as cat, clean_amount, reconcile_statement, calculate_daily_abb,
    tag_recurring_salary, compute_stability, match_lender, _derive_rows_from_balances,
    compute_underwriting_insights, sanitize_ai_result, save_statement_to_db, init_db, _new_metadata,
)


# ---------------- categorizer: table driven ----------------
@pytest.mark.parametrize("narr,debit,credit,expected_in,expected_not_in", [
    ("NEFT-ACME TECHNOLOGIES-SALARY-AUG", 0, 60000, "SALARY", None),
    ("UPI/123456/SALMAN KHAN/PAYMENT", 0, 8000, None, "SALARY"),
    ("NEFT-HDFC0001234-SALONI TRADERS", 0, 9000, None, "SALARY"),
    ("NEFT-XYZ-SAL-0925", 0, 40000, "SALARY", None),
    ("NEFT CR-ICIC0001-ACME PVT LTD-INV 77", 0, 50000, "CORP_INFLOW", "SALARY"),   # needs recurrence
    ("NEFT-RENT FROM TENANT", 0, 20000, None, "SALARY"),
    ("BAJAJ ALLIANZ LIFE PREMIUM ACH D-", 3000, 0, None, "EMI_LOAN"),
    ("ACH D- LIC OF INDIA", 2500, 0, None, "EMI_LOAN"),
    ("ACH D- ICICI PRU MUTUAL FUND SIP", 5000, 0, None, "EMI_LOAN"),
    ("ACH D- TATACAP 12345", 8000, 0, "EMI_LOAN", None),
    ("BFLAUTOPAY 998877", 4500, 0, "EMI_LOAN", None),
    ("ACH D- HDFC BANK LTD 5566", 6000, 0, "EMI_LOAN", None),
    ("UPI-AMBKR-PAYMENT", 500, 0, None, "WALLET_TOPUP"),                           # \bMBK\b
    ("MBK WALLET LOAD", 500, 0, "WALLET_TOPUP", None),
    ("BIL/INFT/ELECTRICITY", 1500, 0, None, "CC_PAYMENT"),
    ("CRED.CLUB@AXISB PAYMENT", 12000, 0, "CC_PAYMENT", None),
    ("ACH RETURN INSUFFICIENT FUNDS", 500, 0, "BOUNCE_FAILURE", None),
    ("ZERODHA BROKING ACH D-", 5000, 0, "STOCK_TRADING", "EMI_LOAN"),
])
def test_categorizer(narr, debit, credit, expected_in, expected_not_in):
    c = cat(narr, debit, credit)
    if expected_in:
        assert expected_in in c, c
    if expected_not_in:
        assert expected_not_in not in c, c


def test_lic_word_boundary():
    assert match_lender("PUBLIC POLICY APPLICATION") is None
    assert match_lender("LIC HOUSING FIN") == "LIC Housing Finance"


# ---------------- clean_amount ----------------
def test_clean_amount():
    assert clean_amount("1,234.50") == 1234.50
    assert clean_amount("500.00DR", is_balance=True) == -500.0
    assert clean_amount("500.00DR") == 500.0
    assert clean_amount("500.00CR", is_balance=True) == 500.0
    assert clean_amount("(250.00)", is_balance=True) == -250.0
    assert clean_amount("garbage") == 0.0
    assert clean_amount("") == 0.0


# ---------------- reconciliation ----------------
def _rec(date, debit, credit, balance, acc="A", narr="X", **kw):
    r = {"txn_date": date, "iso_date": date, "narration": narr, "debit": debit, "credit": credit,
         "balance": balance, "category": "GENERAL", "account_no": acc}
    r.update(kw)
    return r


def test_reconcile_paise_exact():
    recs = [_rec("2026-01-01", 0, 100.10, 1100.10), _rec("2026-01-02", 50.05, 0, 1050.05)]
    md = {"opening_balance": 1000.0, "closing_balance": 1050.05}
    assert reconcile_statement(recs, md)["is_reconciled"]
    md["closing_balance"] = 1050.07            # 2 paise off must fail (old tolerance was Rs 1)
    assert not reconcile_statement(recs, md)["is_reconciled"]


def test_printed_totals_catch_missed_row():
    recs = [_rec("2026-01-01", 0, 50.0, 1050.0)]
    md = {"opening_balance": 1000.0, "closing_balance": 1050.0, "totals_printed": True,
          "total_deposits": 100.0, "total_withdrawals": 50.0}   # printed summary says more activity
    q = reconcile_statement(recs, md)
    assert q["printed_ok"] is False and not q["is_reconciled"]


def test_balance_derivation_detects_gap_and_reverse():
    md = _new_metadata("T", "n", "b", "i"); md["opening_balance"] = 1000.0
    raw = [{"date": "01/01/2026", "narration": "a", "amt": 100.0, "balance": 1100.0},
           {"date": "02/01/2026", "narration": "b", "amt": 30.0, "balance": 1070.0}]
    recs = _derive_rows_from_balances(raw, md, "T", True)
    assert [r["credit"] for r in recs] == [100.0, 0.0] and md["parse_gaps"] == 0

    md2 = _new_metadata("T", "n", "b", "i"); md2["opening_balance"] = 1000.0
    recs = _derive_rows_from_balances(raw[::-1], md2, "T", True)        # newest-first statement
    assert recs[0]["txn_date"] == "01/01/2026" and recs[0]["credit"] == 100.0

    md3 = _new_metadata("T", "n", "b", "i"); md3["opening_balance"] = 1000.0
    gap = [{"date": "01/01/2026", "narration": "a", "amt": 100.0, "balance": 1100.0},
           {"date": "03/01/2026", "narration": "c", "amt": 30.0, "balance": 1000.0}]   # a row (-70) was missed
    _derive_rows_from_balances(gap, md3, "T", True)
    assert md3["parse_gaps"] == 1


def test_zero_opening_balance_does_not_lose_first_txn():
    md = _new_metadata("T", "n", "b", "i"); md["opening_balance"] = 0.0
    raw = [{"date": "01/01/2026", "narration": "x", "amt": 500.0, "balance": 500.0}]
    recs = _derive_rows_from_balances(raw, md, "T", True)               # opening known == 0
    assert recs[0]["credit"] == 500.0


# ---------------- ABB ----------------
def test_abb_uses_last_row_of_day_even_if_rows_come_desc():
    rows = [_rec("2026-01-01", 0, 100, 1100, id=1), _rec("2026-01-01", 0, 100, 1200, id=2),
            _rec("2026-01-02", 0, 0.0, 1200, id=3)]
    asc = calculate_daily_abb(rows)
    desc = calculate_daily_abb(rows[::-1])        # what /api/dashboard feeds (ORDER BY ... DESC)
    assert asc["overall_abb"] == desc["overall_abb"] == 1200.0


def test_abb_multi_account_sums_accounts():
    rows = [_rec("2026-01-01", 0, 0, 1000, acc="A"), _rec("2026-01-02", 0, 0, 1000, acc="A"),
            _rec("2026-01-01", 0, 0, 500, acc="B"), _rec("2026-01-02", 0, 0, 500, acc="B")]
    assert calculate_daily_abb(rows)["overall_abb"] == 1500.0


# ---------------- recurring salary ----------------
def test_recurring_salary_rules():
    def mk(d, amt, narr):
        r = _rec(d, 0, amt, 0, narr=narr)
        r["category"] = cat(narr, 0, amt)
        return r
    ok = tag_recurring_salary([mk("2026-01-05", 50000, "NEFT CR-ICIC0001-ACME PVT LTD-INV 1"),
                               mk("2026-02-06", 51000, "NEFT CR-ICIC0001-ACME PVT LTD-INV 2")])
    assert all("SALARY" in r["category"] for r in ok)
    # one-off client payment: must stay out of income
    one = tag_recurring_salary([mk("2026-01-05", 50000, "NEFT CR-ICIC0001-ACME PVT LTD-INV 1")])
    assert "SALARY" not in one[0]["category"]
    # same counterparty but wildly different amounts / pay-days: not salary
    bad = tag_recurring_salary([mk("2026-01-02", 50000, "NEFT CR-ICIC0001-ACME PVT LTD-INV 1"),
                                mk("2026-02-25", 90000, "NEFT CR-ICIC0001-ACME PVT LTD-INV 2")])
    assert all("SALARY" not in r["category"] for r in bad)
    # a parent's transfer (no corporate name) over only 2 months: not salary
    fam = tag_recurring_salary([mk("2026-01-05", 20000, "NEFT-RAMESH KUMAR-HOME"),
                                mk("2026-02-05", 20000, "NEFT-RAMESH KUMAR-HOME")])
    assert all("SALARY" not in r["category"] for r in fam)


def test_stability_is_computed():
    steady = [{"iso_date": f"2026-0{m}-01", "amount": 50000.0} for m in range(1, 7)]
    erratic = [{"iso_date": "2026-01-01", "amount": 20000.0}, {"iso_date": "2026-03-28", "amount": 90000.0}]
    s1, _ = compute_stability(steady, {f"2026-0{m}" for m in range(1, 7)}, ["Acme"])
    s2, _ = compute_stability(erratic, {f"2026-0{m}" for m in range(1, 7)}, ["Acme"])
    s0, tier0 = compute_stability([], {"2026-01"}, [])
    assert s1 >= 90 and s2 < 50 and s0 == 0 and "No Salaried" in tier0


# ---------------- insights ----------------
def test_foir_none_without_salary_and_no_fake_range():
    recs = [_rec("2026-01-01", 5000, 0, 995000, narr="x"), _rec("2026-01-02", 0, 100, 995100)]
    recs[0]["category"] = "EMI_LOAN"
    ins = compute_underwriting_insights(recs, {"opening_balance": 1000000.0, "closing_balance": 995100.0})
    assert ins["debt_and_emi"]["foir_percentage"] is None


def test_ai_output_is_validated_and_cannot_lower_risk():
    recs = [_rec("2026-01-01", 0, 60000, 60000, narr="NEFT ACME TECHNOLOGIES PVT LTD SALARY"),
            _rec("2026-01-02", 4000, 0, 56000, narr="ACH D- BAJAJFINANCE 1234")]
    ai = sanitize_ai_result({
        "primary_employer_name": "Totally Invented Corp", "verified_employers": ["Acme Technologies", "Evil <script>"],
        "active_lenders": [{"raw_code": "BAJAJFINANCE", "clean_lender_name": "Bajaj Finance"},
                           {"raw_code": "NOPE", "clean_lender_name": "Imaginary Lender"}],
        "risk_tag": "BOGUS", "risk_score": 9999, "churn_velocity": "HIGH", "reasoning": "x" * 1000}, recs)
    assert ai["primary_employer"] is None
    assert ai["employers"] == ["Acme Technologies"]
    assert ai["lenders"] == ["Bajaj Finance"]
    assert ai["risk_tag"] is None and ai["risk_score"] == 100 and len(ai["reasoning"]) <= 300


# ---------------- DB: multi-file cases ----------------
def test_multi_file_case_keeps_all_statements():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "t.db")
        init_db(db)
        md1 = _new_metadata("HDFC Bank", "a", "b", "i"); md1["account_no"] = "111"
        md2 = _new_metadata("SBI Bank", "a", "b", "i"); md2["account_no"] = "222"
        cid, s1 = save_statement_to_db([_rec("2026-01-01", 0, 1, 1)], md1, filename="a.pdf", db_path=db)
        _, s2 = save_statement_to_db([_rec("2026-01-01", 0, 2, 2)], md2, filename="b.pdf", case_id=cid, db_path=db)
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(db)) as c:      # close it, or Windows cannot delete the temp dir
            assert c.execute("SELECT COUNT(*) FROM statements WHERE case_id=?", (cid,)).fetchone()[0] == 2
            assert c.execute("SELECT COUNT(*) FROM transactions WHERE case_id=?", (cid,)).fetchone()[0] == 2
        assert s1 != s2


def test_old_schema_migrates():
    import sqlite3
    from contextlib import closing
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "old.db")
        with closing(sqlite3.connect(db)) as c:
            c.execute("CREATE TABLE statements (id INTEGER PRIMARY KEY AUTOINCREMENT, bank TEXT)")
            c.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, bank TEXT, narration TEXT)")
            c.commit()
        init_db(db)
        with closing(sqlite3.connect(db)) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(transactions)")}
        assert {"case_id", "statement_id", "iso_date"} <= cols
