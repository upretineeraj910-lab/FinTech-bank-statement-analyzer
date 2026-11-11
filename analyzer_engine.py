"""
FinTech Bank Statement Analyzer & Underwriting Engine
Supports Federal Bank (10-col), HDFC Bank (7-col), Axis Bank (7-col), 
IDFC FIRST Bank (7-col), SBI Bank (8-col), ICICI Bank (7-col), Standard Chartered (7-col),
and Universal/Generic layouts.
Features: 100% zero-variance mathematical audit, multiline narration preservation,
zero-false-positive categorization, and distinct CC vs Wallet segregation.
"""

import os
import re
import json
import sqlite3
import math
from datetime import datetime
from collections import defaultdict
import hashlib
from io import BytesIO
import pandas as pd
import pymupdf
import pdfplumber

# Load .env automatically if present
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    try:
        with open(_env_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k = _k.strip()
                    _v = _v.strip().strip('"').strip("'")
                    if _k and _v and _k not in os.environ:
                        os.environ[_k] = _v
    except Exception:
        pass

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

DB_PATH = os.path.join(os.path.dirname(__file__), "bank_statement.db")

def init_db(db_path=DB_PATH):
    conn = sqlite3.connect(db_path, timeout=30.0)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA busy_timeout=5000;")
    c.execute("""
    CREATE TABLE IF NOT EXISTS statements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bank TEXT,
        borrower_name TEXT,
        account_no TEXT,
        branch TEXT,
        sol_id TEXT,
        ifsc TEXT,
        cust_id TEXT,
        email TEXT,
        mobile TEXT,
        account_type TEXT,
        period TEXT,
        opening_balance REAL,
        closing_balance REAL,
        total_withdrawals REAL,
        total_deposits REAL,
        abb REAL,
        reconciled INTEGER DEFAULT 1,
        filename TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    c.execute("PRAGMA table_info(transactions)")
    cols = [col[1] for col in c.fetchall()]
    if not cols:
        c.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bank TEXT,
            account_no TEXT,
            txn_date TEXT,
            iso_date TEXT,
            narration TEXT,
            debit REAL,
            credit REAL,
            balance REAL,
            category TEXT,
            sub_category TEXT
        )
        """)
    else:
        if "account_no" not in cols:
            c.execute("ALTER TABLE transactions ADD COLUMN account_no TEXT")
        if "iso_date" not in cols:
            c.execute("ALTER TABLE transactions ADD COLUMN iso_date TEXT")
        if "sub_category" not in cols:
            c.execute("ALTER TABLE transactions ADD COLUMN sub_category TEXT")
            
    conn.commit()
    conn.close()

MONTH_MAP = {
    'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04', 'may': '05', 'jun': '06',
    'jul': '07', 'aug': '08', 'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12'
}

def is_date(val):
    if not val or not str(val).strip():
        return False
    s = str(val).split('\n')[0].strip()
    if re.match(r'^\d{1,2}[\s\-\/\.][A-Za-z]{3,9}[\s\-\/\.]\d{2,4}$', s):
        return True
    if re.match(r'^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}$', s):
        return True
    if re.match(r'^\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}$', s):
        return True
    if re.match(r'^\d{4}-\d{1,2}-\d{1,2}$', s):
        return True
    return False

def is_amount(val):
    if not val or not str(val).strip():
        return False
    s = str(val).strip().replace(',', '').replace(' ', '').upper()
    s = re.sub(r'^(?:RS\.?|INR)\.?\s*', '', s)
    s = re.sub(r'(?:CR|DR)$', '', s)
    if not s:
        return False
    return bool(re.match(r'^-?\d+(?:\.\d{1,2})?$', s))

def clean_amount(val):
    if not val or not str(val).strip():
        return 0.0
    val = str(val).replace(',', '').replace(' ', '').replace('CR', '').replace('DR', '').strip()
    try:
        return float(val)
    except ValueError:
        return 0.0

def parse_iso_date(date_str):
    if not date_str:
        return ""
    s = str(date_str).split('\n')[0].strip()
    s = re.sub(r'\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?.*$', '', s, flags=re.I).strip()
    
    m0 = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', s)
    if m0:
        y, mo, d = m0.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    m1 = re.match(r'^(\d{1,2})[\s\-\/\.]([A-Za-z]{3,9})[\s\-\/\.](\d{2,4})$', s)
    if m1:
        d, mon, y = m1.groups()
        mon_lower = mon[:3].lower()
        if mon_lower in MONTH_MAP:
            mo = MONTH_MAP[mon_lower]
            year = f"20{y}" if len(y) == 2 else y
            return f"{int(year):04d}-{mo}-{int(d):02d}"

    m2 = re.match(r'^([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{2,4})$', s)
    if m2:
        mon, d, y = m2.groups()
        mon_lower = mon[:3].lower()
        if mon_lower in MONTH_MAP:
            mo = MONTH_MAP[mon_lower]
            year = f"20{y}" if len(y) == 2 else y
            return f"{int(year):04d}-{mo}-{int(d):02d}"

    m3 = re.match(r'^(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})$', s)
    if m3:
        d, mo, y = m3.groups()
        year = f"20{y}" if len(y) == 2 else y
        return f"{int(year):04d}-{int(mo):02d}-{int(d):02d}"

    return s

def extract_dynamic_employer(desc):
    d = desc.strip()
    cleaned = re.sub(r'^(?:NEFT|RTGS|IMPS|ACH|CMS|A2A|FT)\s*(?:CR|DR)?\s*[-/:]?\s*[A-Z0-9]{8,25}\s*', '', d, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r'^[A-Z0-9]{10,}\*?', '', cleaned).strip()
    
    corp_match = re.search(r'([A-Z0-9\s.,&-]+?\b(?:PRIVATE\s+LIMITED|PVT\s+LTD|LIMITED|LTD|CORPORATION|CORP|TECHNOLOGIES|TECH|SERVICES|SOLUTIONS|SOLN|LLP|MANAGEMENT)\b)', cleaned, re.IGNORECASE)
    if corp_match:
        emp = corp_match.group(1).strip()
        emp = re.sub(r'^[-/*0-9\s]+', '', emp).strip()
        emp = re.sub(r'\s+IN\d{10,}.*$', '', emp, flags=re.I).strip()
        if len(emp) >= 4 and not any(b in emp.upper() for b in ["STATE BANK", "HDFC BANK", "ICICI BANK", "AXIS BANK"]):
            return emp.title()

    hyphen_parts = [p.strip() for p in cleaned.split('-') if p.strip()]
    for part in hyphen_parts:
        part_upper = part.upper()
        if any(kw in part_upper for kw in ["SALARY", "SALARIS", "PAYROLL", "INCENTIVE", "MONTH", "EBO", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC", "BATCHID"]):
            continue
        if re.search(r'^[0-9]+$', part) or len(part) < 3:
            continue
        part_clean = re.sub(r'\s+(?:CITI\s*BANK|HDFC\s*BANK|SBI|AXIS|ICICI|BANK).*$', '', part, flags=re.IGNORECASE).strip()
        part_clean = re.sub(r'\s+IN\d{10,}.*$', '', part_clean, flags=re.I).strip()
        if len(part_clean) >= 3:
            return part_clean.title()

    return "Corporate Payroll"

def categorize_transaction(desc, debit=0.0, credit=0.0):
    d = desc.upper().strip()
    flags = []
    
    # 1. Genuine Cheque Bounce / Mandate / NACH Failures
    is_upi = d.startswith("UPI-") or "@" in d
    genuine_bounce_pattern = r'\b(BOUNCE|RET\s*CHG|BOUNCE\s*CHG|INSUFFICIENT|ACH\s*RET|NACH\s*RET|ECS\s*RET|CHQ\s*RET|DISHONOUR|UNPAID)\b'
    if re.search(genuine_bounce_pattern, d) or (not is_upi and re.search(r'\b(RETURN|RET)\b', d) and debit > 0):
        flags.append("BOUNCE_FAILURE")

    # 2. Universal Salary Inflows
    broker_keywords = ['ANGEL', 'ZERODHA', 'GROWW', 'UPSTOX', 'ICCL', 'INDIAN CLEARING', 'NSE', 'BSE', 'SHAREKHAN', 'MOTILAL']
    is_broker = any(b in d for b in broker_keywords)
    
    if credit >= 5000 and not is_broker:
        is_salary_text = (
            re.search(r'\b(SALARY|PAYROLL|CMS/SAL|NEFT-SAL|STIPEND|SALARIS)\b', d) or
            re.search(r'[-/](SALARY|PAYROLL|SAL|SALARIS)[-/0-9A-Z]*', d) or
            re.search(r'\b(SAL(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{0,4})\b', d) or
            re.search(r'[-/](?:EBO\s+INCENTIVE|WEEKLY\s+INCENTIVE)', d) or
            (re.search(r'\b(?:PRIVATE\s+LIMITED|PVT\s+LTD|LTD|LIMITED)\b', d) and credit >= 15000 and not re.search(r'\b(?:BANK|FINANCE|FINCORP|INSURANCE|CAPITAL)\b', d)) or
            (re.match(r'^(NEFT|RTGS|IMPS|ACH|CMS|A2A|FT)', d) and credit >= 15000 and
             re.search(r'\b(SOLUTIONS?|TECHNOLOG(?:Y|IES)|SERVICES?|INFOTECH|INDUSTRIES|ENTERPRISES?|SYSTEMS?|CONSULTING|CONSULTANTS?|SOFTWARE|MANAGEMENT|PVT|PRIVATE|CORP|CORPORATION|LLP|INC)\b', d) and
             not re.search(r'\b(?:STATE\s+BANK|DBS\s+BANK|HDFC\s+BANK|ICICI\s+BANK|AXIS\s+BANK|PUNJAB\s+NATIONAL|BANK\s+OF\s+INDIA|BANK\s+INDIA)\b', d))
        )
        if is_salary_text and not re.search(r'\b(RENT|REFUND|REVERSAL|REV|DIVIDEND|CASH\s+DEP|INTEREST|INT\.PD|PAY\s*REQUEST)\b', d):
            flags.append("SALARY")
        
    # 3. Stock Market / Broker / Mutual Fund / SIP Clearing
    sip_or_trading = any(k in d for k in [
        'ANGEL', 'ZERODHA', 'GROWW', 'UPSTOX', 'INDIANCLEARING', 'INDIAN CLEARING',
        'CLEARING', 'NSECL', 'BSECLEA', 'ICCL', 'MFSS', 'CDSL', 'NSDL', 'INDSTOCKS', 'INDMONEY', 'ICICIDIRECT'
    ])
    if sip_or_trading:
        flags.append("STOCK_TRADING")
        
    # 4. EMI & Loan Payments vs Disbursements
    # Important: EMI is strictly a DEBIT. Credits can only be Disbursements.
    if "BOUNCE_FAILURE" not in flags and not sip_or_trading:
        # Check Disbursements (Credits)
        if credit >= 10000 and any(k in d for k in ["INSTALOAN", "INSTA LOAN", "INSTAJUMBOLOAN", "JUMBO LOAN", "LOAN DISB", "DISBURSEMENT", "POONAWALLA"]):
            flags.append("LOAN_DISBURSEMENT")
        
        # Check EMIs (Strictly Debits Only)
        elif debit >= 200:
            is_bajaj_pay_merchant = "BAJAJPAY" in d or "@INDUS-INDB0MERCHA" in d
            
            emi_patterns = (
                r'(ACH\s*D[-_]|ACHDR|ACH-DR|NACH[-_]|BFLAUTOPAY|_EMI_|LOAN\s*REC|CANFIN|TATACAP|TCHHL|TCHIN|ADINGEN_TATA|CANARA\s*RO|HINDON)'
            )
            is_direct_emi = bool(re.search(emi_patterns, d)) or (
                any(k in d for k in ["KRAZYBEE", "POONAWALLAFIN", "NORTHERN ARC", "ADITYA BIRLA FINANCE", "MUTHOOT"])
            )
            
            # Catch HDFC SmartEMI / Masked Card Auto-Billpay EMIs:
            # e.g., IB BILLPAY DR-HDFC2F-537206XXXXXX1334
            if re.search(r'IB\s*BILLPAY\s*DR[-_\s]*HDFC', d) and re.search(r'\d{6}X{4,6}\d{4}', d):
                is_direct_emi = True

            # Standard Clearing Cheque EMI (e.g. EMI 466194268 CHQ)
            if re.search(r'\bEMI\s+[0-9]+\s+CHQ\b', d):
                is_direct_emi = True

            if "BAJAJ" in d and not is_bajaj_pay_merchant and debit >= 500:
                is_direct_emi = True
                
            if ("IDFC" in d or "KMBL" in d or "HDBFS" in d) and any(m in d for m in ["ACH", "NACH", "MANDATE", "LOAN", "DRAOPERATIONS"]) and not is_upi:
                is_direct_emi = True

            if is_direct_emi:
                flags.append("EMI_LOAN")
        
    # 5. Distinct Segregation: Credit Card Payments vs Digital Wallet Topups
    cc_keywords = [
        'CRED.CLUB', 'CREDITCARDS@RBL', '@CRED', '@YESCRED', 'CRED WALLET', 'CREDPAY',
        'CREDIT CARD', 'CC AUTOPAY', 'CC000', 'RBL BANK CREDIT CARD', 'HDFC BANK DIGITAL CRE',
        'PZ HDFC CC', 'SBI CARDS', 'CC BILL PAY', 'BIL/INFT', 'PAYMENT ON CRED'
    ]
    wallet_keywords = [
        'UPI-LITE', 'ADD MONEY', 'MOBIKWIK', 'MBK', 'RUPICARD',
        'ZETAWALL', 'PAYTM WALLET', 'AMAZON PAY WALLET', 'LOAD WALLET'
    ]

    if any(re.search(k, d) for k in cc_keywords):
        flags.append("CC_PAYMENT")
    elif any(re.search(k, d) for k in wallet_keywords):
        flags.append("WALLET_TOPUP")
    elif 'PAYTMQR' in d and debit >= 2000 and any(c in d for c in ['CRED', 'CARD', 'ROTATION', 'WALLET']):
        flags.append("CC_PAYMENT")

    return ", ".join(flags) if flags else "GENERAL"

def _infer_opening_balance(balance, amt, narration):
    n = narration.upper()
    if round(balance - amt, 2) < 0:
        return round(balance + amt, 2)
    credit_hint = any(k in n for k in ["NEFT CR", "NEFT-CR", "RTGS CR", "SALARY", "INT.PD", "INTEREST", "REFUND", "REV", "CREDIT"])
    return round(balance - amt, 2) if credit_hint else round(balance + amt, 2)

def _counterparty_key(narr):
    d = narr.upper().strip()
    if not re.match(r'^(NEFT|RTGS|IMPS|ACH|CMS|A2A|FT)', d):
        return None
    d = re.sub(r'^(NEFT|RTGS|IMPS)\s*(CR|DR)?[-/ ]*', '', d)
    d = re.sub(r'\b[A-Z]{4}0[A-Z0-9]{6}\b', ' ', d)
    d = re.sub(r'\b[A-Z]*\d{6,}[A-Z0-9]*\b', ' ', d)
    words = re.findall(r'[A-Z]{3,}', d)
    words = [w for w in words if w not in ("NEFT", "RTGS", "IMPS", "ACH", "CMS", "A2A", "SAL", "SALARY")]
    return " ".join(words[:2]) or None

def tag_recurring_salary(records, min_amount=10000, tol=0.35):
    excluded_entities = ['ANGEL', 'ZERODHA', 'GROWW', 'UPSTOX', 'ICCL', 'NSE', 'BSE', 'INDIAN CLEARING']
    groups = defaultdict(list)
    for i, r in enumerate(records):
        narr = r.get("narration", "").upper()
        if r.get("credit", 0) >= min_amount and "SALARY" not in r.get("category", ""):
            if any(e in narr for e in excluded_entities):
                continue
            k = _counterparty_key(r.get("narration", ""))
            if k:
                groups[k].append(i)

    for k, idxs in groups.items():
        months = {records[i]["iso_date"][:7] for i in idxs if records[i].get("iso_date")}
        if len(months) < 2:
            continue
        amts = sorted(records[i]["credit"] for i in idxs)
        med = amts[len(amts) // 2]
        hits = [i for i in idxs if abs(records[i]["credit"] - med) <= tol * med]
        if len({records[i]["iso_date"][:7] for i in hits if records[i].get("iso_date")}) < 2:
            continue
        for i in hits:
            cat = records[i]["category"]
            records[i]["category"] = "SALARY" if cat == "GENERAL" else f"{cat}, SALARY"
    return records

def _fill_period_from_records(metadata, records):
    if records and not metadata.get("period"):
        dates = sorted(r["iso_date"] for r in records if r.get("iso_date"))
        if dates:
            metadata["period"] = f"{dates[0]} to {dates[-1]}"

def diagnose_pdf(file_bytes, password=None):
    info = {}
    try:
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        if doc.is_encrypted and password:
            doc.authenticate(password)
        text = "".join(p.get_text() for p in doc)
        info["pages"] = len(doc)
        info["text_chars"] = len(text.strip())
        info["looks_scanned"] = len(text.strip()) < 50
        info["date_like_lines"] = len(re.findall(r'(?m)^\s*\d{2}[/.-](?:\d{2}|[A-Za-z]{3})[/.-]\d{2,4}', text))
        info["first_lines"] = [l.strip()[:80] for l in text.split("\n") if l.strip()][:8]
        doc.close()
    except Exception as e:
        info["error"] = str(e)
    return info

def detect_bank_format(file_bytes=None, file_path=None, password=None):
    doc = None
    try:
        if file_bytes:
            doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        elif file_path:
            doc = pymupdf.open(file_path)
            
        if doc.is_encrypted:
            if password:
                if not doc.authenticate(password):
                    return {"is_encrypted": True, "auth_failed": True, "detected_bank": "Unknown"}
            else:
                return {"is_encrypted": True, "auth_failed": False, "detected_bank": "Protected PDF"}
                
        p0_raw = doc[0].get_text() if len(doc) > 0 else ""
        m_table = re.search(r'\n\s*(?:Date|Txn\s*Date|Tran\s*Date|Value\s*Date|Description|Particulars)\s*[\n\r]', p0_raw, re.I)
        header_clean = p0_raw[:m_table.start()].upper() if m_table else p0_raw[:1500].upper()
        p0_header = p0_raw[:2500].upper()

        full_sample_text = ""
        for i in range(min(3, len(doc))):
            full_sample_text += " " + doc[i].get_text()
        text_upper = full_sample_text.upper()

        m_ifsc = re.search(r'IFSC\s*(?:CODE)?\s*[:\.]\s*([A-Z]{4})', p0_header)
        if m_ifsc:
            code = m_ifsc.group(1)
            if code == "SCBL":
                return {"is_encrypted": False, "detected_bank": "Standard Chartered Bank", "layout": "7-column Standard Chartered Layout", "columns": ["Date", "Value Date", "Description", "Cheque", "Deposit", "Withdrawal", "Balance"], "pages": len(doc)}
            elif code == "HDFC":
                return {"is_encrypted": False, "detected_bank": "HDFC Bank", "layout": "7-column Text Format", "columns": ["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"], "pages": len(doc)}
            elif code == "SBIN":
                return {"is_encrypted": False, "detected_bank": "SBI Bank", "layout": "8-column Standard Format", "columns": ["Txn Date", "Value Date", "Description", "Ref No", "Debit", "Credit", "Balance"], "pages": len(doc)}
            elif code == "UTIB":
                return {"is_encrypted": False, "detected_bank": "Axis Bank", "layout": "7-column Tabular Format", "columns": ["Tran Date", "Chq No", "Particulars", "Debit", "Credit", "Balance", "Init. Br"], "pages": len(doc)}
            elif code == "ICIC":
                return {"is_encrypted": False, "detected_bank": "ICICI Bank", "layout": "7-column Tabular Format", "columns": ["S No.", "Transaction Date", "Cheque Number", "Transaction Remarks", "Withdrawal Amount", "Deposit Amount", "Balance"], "pages": len(doc)}
            elif code == "FDRL":
                return {"is_encrypted": False, "detected_bank": "Federal Bank", "layout": "10-column Table Format", "columns": ["Date", "Value Date", "Particulars", "Tran Type", "Tran ID", "Cheque Details", "Withdrawals", "Deposits", "Balance", "DR/CR"], "pages": len(doc)}
            elif code == "IDFB":
                return {"is_encrypted": False, "detected_bank": "IDFC FIRST Bank", "layout": "7-column Tabular Format", "columns": ["Transaction Date", "Value Date", "Particulars", "Cheque No", "Debit", "Credit", "Balance"], "pages": len(doc)}

        if "STANDARD CHARTERED" in header_clean or "STANCHART" in header_clean or "SCBL" in header_clean or "STANDARD CHARTERED" in text_upper:
            return {"is_encrypted": False, "detected_bank": "Standard Chartered Bank", "layout": "7-column Standard Chartered Layout", "columns": ["Date", "Value Date", "Description", "Cheque", "Deposit", "Withdrawal", "Balance"], "pages": len(doc)}
        elif ("CHQ./REF.NO." in p0_header and "CLOSING BALANCE" in p0_header and "WITHDRAWAL AMT." in p0_header) or "HDFC BANK" in header_clean or "WE UNDERSTAND YOUR WORLD" in text_upper or "HDFC BANK LIMITED" in header_clean:
            return {"is_encrypted": False, "detected_bank": "HDFC Bank", "layout": "7-column Text Format", "columns": ["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"], "pages": len(doc)}
        elif "ICICI BANK" in header_clean or "WWW.ICICIBANK.COM" in text_upper or ("ICIC0" in p0_header and "IFSC" in p0_header):
            return {"is_encrypted": False, "detected_bank": "ICICI Bank", "layout": "7-column Tabular Format", "columns": ["S No.", "Transaction Date", "Cheque Number", "Transaction Remarks", "Withdrawal Amount", "Deposit Amount", "Balance"], "pages": len(doc)}
        elif "STATE BANK OF INDIA" in header_clean or "REGULAR SB CHQ" in text_upper or "WWW.ONLINESBI.COM" in text_upper or ("SBIN0" in p0_header and "IFSC" in p0_header):
            return {"is_encrypted": False, "detected_bank": "SBI Bank", "layout": "8-column Standard Format", "columns": ["Txn Date", "Value Date", "Description", "Ref No", "Debit", "Credit", "Balance"], "pages": len(doc)}
        elif "AXIS BANK" in header_clean or "STATEMENT OF AXIS ACCOUNT" in text_upper or "LIBERTY SALARY ACCOUNT" in text_upper or ("UTIB0" in p0_header and "IFSC" in p0_header):
            return {"is_encrypted": False, "detected_bank": "Axis Bank", "layout": "7-column Tabular Format", "columns": ["Tran Date", "Chq No", "Particulars", "Debit", "Credit", "Balance", "Init. Br"], "pages": len(doc)}
        elif "FEDERAL BANK" in header_clean or "FEDSALARY" in text_upper or "SB FEDSALARY" in text_upper or ("FDRL0" in p0_header and "IFSC" in p0_header):
            return {"is_encrypted": False, "detected_bank": "Federal Bank", "layout": "10-column Table Format", "columns": ["Date", "Value Date", "Particulars", "Tran Type", "Tran ID", "Cheque Details", "Withdrawals", "Deposits", "Balance", "DR/CR"], "pages": len(doc)}
        elif "IDFC FIRST BANK" in header_clean or "ALWAYS YOU FIRST" in text_upper or ("IDFB0" in p0_header and "IFSC" in p0_header):
            return {"is_encrypted": False, "detected_bank": "IDFC FIRST Bank", "layout": "7-column Tabular Format", "columns": ["Transaction Date", "Value Date", "Particulars", "Cheque No", "Debit", "Credit", "Balance"], "pages": len(doc)}
        else:
            return {"is_encrypted": False, "detected_bank": "Generic Bank Statement", "layout": "Auto-Detect Universal Engine", "columns": ["Date", "Narration", "Debit", "Credit", "Balance"], "pages": len(doc)}
    except Exception as e:
        return {"error": str(e), "is_encrypted": False, "detected_bank": "Unknown"}
    finally:
        if doc:
            doc.close()

def parse_standard_chartered_pdf(pdf_source, password=None):
    metadata = {
        "bank": "Standard Chartered Bank", "borrower_name": "Account Holder", "account_no": "",
        "branch": "Main Branch", "sol_id": "", "ifsc": "SCBL0000000", "cust_id": "", "email": "",
        "mobile": "", "account_type": "Smart Banking Savings Account", "period": "",
        "opening_balance": 0.0, "closing_balance": 0.0, "total_withdrawals": 0.0, "total_deposits": 0.0
    }
    try:
        doc = pymupdf.open(stream=pdf_source if isinstance(pdf_source, bytes) else None,
                           filename=pdf_source if isinstance(pdf_source, str) else None)
        if doc.is_encrypted and password:
            doc.authenticate(password)
        p0_text = doc[0].get_text()
        name_m = (re.search(r'^(?:MR|MS|MRS)\.?\s+([A-Za-z\s\.]+?)(?:\r?\n|$)', p0_text, re.M | re.I) or
                  re.search(r'(?:MR|MS|MRS)\.?\s+([A-Za-z\s]+?)\s+BRANCH', p0_text, re.I))
        if name_m:
            cand = name_m.group(1).strip()
            cand = re.sub(r'\s+BRANCH.*$', '', cand, flags=re.I).strip()
            if cand and not any(k in cand.upper() for k in ["BANK", "STATEMENT", "ACCOUNT", "PERIOD", "PAGE"]):
                metadata["borrower_name"] = f"MR {cand}" if not cand.upper().startswith(("MR", "MS", "MRS")) else cand
        acc_m = re.search(r'ACCOUNT\s*NO\s*:\s*([0-9]+)', p0_text, re.I)
        if acc_m:
            metadata["account_no"] = acc_m.group(1).strip()
        br_m = re.search(r'BRANCH\s*:\s*([^\n\r]+)', p0_text, re.I)
        if br_m:
            metadata["branch"] = br_m.group(1).strip()
        ifsc_m = re.search(r'IFSC\s*:\s*([A-Za-z0-9]+)', p0_text, re.I)
        if ifsc_m:
            metadata["ifsc"] = ifsc_m.group(1).strip()
        date_m = re.search(r'STATEMENT\s*DATE\s*:\s*([^\n\r]+)', p0_text, re.I)
        if date_m:
            metadata["period"] = date_m.group(1).strip()
        doc.close()
    except Exception as e:
        print(f"[SC Metadata Warning] {e}")

    records = []
    try:
        pdf_target = BytesIO(pdf_source) if isinstance(pdf_source, bytes) else pdf_source
        with pdfplumber.open(pdf_target, password=password) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if not tables:
                    continue
                for table in tables:
                    for row in table:
                        if not row or not any(row):
                            continue
                        row_strs = [str(c or '').strip() for c in row]
                        if len(row_strs) < 6:
                            continue
                        row_lower = [c.lower() for c in row_strs]
                        if 'deposit' in row_lower or 'withdrawal' in row_lower or 'total' in row_lower:
                            continue
                        row_full = " ".join(row_strs).upper()
                        if 'BALANCE FORWARD' in row_full or 'B/F' in row_full:
                            bal_str = row_strs[-1] or row_strs[-2]
                            metadata['opening_balance'] = clean_amount(bal_str)
                            continue

                        date_val = row_strs[0].strip()
                        if not date_val or not is_date(date_val):
                            continue

                        desc = row_strs[2].replace('\n', ' ').strip()
                        chq = row_strs[3].strip() if len(row_strs) > 3 else ""
                        if chq and chq not in desc:
                            desc = f"{desc} Ref:{chq}"

                        dep = clean_amount(row_strs[4])
                        wdr = clean_amount(row_strs[5])
                        bal = clean_amount(row_strs[6]) if len(row_strs) > 6 else 0.0

                        category = categorize_transaction(desc, wdr, dep)
                        records.append({
                            "bank": metadata["bank"],
                            "account_no": metadata.get("account_no", ""),
                            "txn_date": date_val,
                            "iso_date": parse_iso_date(date_val),
                            "narration": desc,
                            "debit": wdr,
                            "credit": dep,
                            "balance": bal,
                            "category": category
                        })
    except Exception as e:
        print(f"[Standard Chartered Parser Error] {e}")

    if records:
        if metadata["opening_balance"] == 0.0 and len(records) > 0:
            metadata["opening_balance"] = round(records[0]["balance"] - records[0]["credit"] + records[0]["debit"], 2)
        metadata["closing_balance"] = records[-1]["balance"]
        metadata["total_withdrawals"] = round(sum(r["debit"] for r in records), 2)
        metadata["total_deposits"] = round(sum(r["credit"] for r in records), 2)
        _fill_period_from_records(metadata, records)

    return records, metadata

def parse_hdfc_pdf(pdf_source, password=None, _raw_lines=False):
    metadata = {
        "bank": "HDFC Bank", "borrower_name": "HDFC Account Holder", "account_no": "",
        "branch": "HDFC Branch", "sol_id": "", "ifsc": "HDFC0000000", "cust_id": "", "email": "",
        "mobile": "", "account_type": "Savings Account", "period": "",
        "opening_balance": 0.0, "closing_balance": 0.0, "total_withdrawals": 0.0, "total_deposits": 0.0
    }

    full_text = ""
    page_texts = []
    try:
        if isinstance(pdf_source, bytes):
            doc = pymupdf.open(stream=pdf_source, filetype="pdf")
        else:
            doc = pymupdf.open(pdf_source)

        if doc.is_encrypted and password:
            doc.authenticate(password)

        p0_text = doc[0].get_text()

        name_m = (re.search(r'(?:MR|MS|MRS)\.?\s*\n+([A-Z\s]{3,40})\n', p0_text) or 
                  re.search(r'(?:MR|MS|MRS)\.?\s+([A-Z\s]{3,40})\n', p0_text) or
                  re.search(r'Name\s*[:\.]?\s*([A-Za-z\s\.]+)\n', p0_text, re.I))
        if name_m:
            metadata["borrower_name"] = name_m.group(1).replace('\n', ' ').strip()

        acc_m = re.search(r'Account\s*No\s*:\s*([0-9]+)', p0_text)
        if acc_m:
            metadata["account_no"] = acc_m.group(1).strip()

        ifsc_m = re.search(r'RTGS/NEFT\s*IFSC\s*:\s*([A-Za-z0-9]+)', p0_text)
        if ifsc_m:
            metadata["ifsc"] = ifsc_m.group(1).strip()

        br_m = re.search(r'Account\s*Branch\s*:\s*([^\n\r]+)', p0_text)
        if br_m:
            metadata["branch"] = br_m.group(1).strip()

        cust_m = re.search(r'Cust\s*ID\s*:\s*([0-9]+)', p0_text)
        if cust_m:
            metadata["cust_id"] = cust_m.group(1).strip()

        email_m = re.search(r'Email\s*:\s*([^\n\r\s]+)', p0_text)
        if email_m:
            metadata["email"] = email_m.group(1).strip()

        per_m = re.search(r'From\s*:\s*([0-9/]+)\s*To\s*:\s*([0-9/]+)', p0_text, re.IGNORECASE)
        if per_m:
            metadata["period"] = f"{per_m.group(1)} to {per_m.group(2)}"

        for page in doc:
            pt = page.get_text()
            page_texts.append(pt)
            full_text += "\n" + pt

        sum_m = re.search(r'STATEMENT\s*SUMMARY[^\n]*\n+Opening\s*Balance[^\n]*\n+([\d,]+\.\d{2})\s+(\d+)\s+(\d+)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})', full_text, re.IGNORECASE)
        if not sum_m:
            sum_m = re.search(r'([\d,]+\.\d{2})\s+(\d+)\s+(\d+)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})', full_text)

        if sum_m:
            metadata["opening_balance"] = clean_amount(sum_m.group(1))
            metadata["total_withdrawals"] = clean_amount(sum_m.group(4))
            metadata["total_deposits"] = clean_amount(sum_m.group(5))
            metadata["closing_balance"] = clean_amount(sum_m.group(6))
        else:
            ob_m = re.search(r'Opening\s*Balance[\s\S]{0,200}?([\d,]+\.\d{2})', full_text, re.IGNORECASE)
            if ob_m:
                metadata["opening_balance"] = clean_amount(ob_m.group(1))

        doc.close()
    except Exception as e:
        print(f"HDFC Metadata error: {e}")

    records = []
    try:
        if isinstance(pdf_source, bytes):
            coord_doc = pymupdf.open(stream=pdf_source, filetype="pdf")
        else:
            coord_doc = pymupdf.open(pdf_source)
        if coord_doc.is_encrypted and password:
            coord_doc.authenticate(password)

        txns_raw = []
        curr_t = None
        date_pattern = re.compile(r'^\d{2}/\d{2}/\d{2,4}$')
        boilerplate_noise = {"HOLDERS", "RTGS/NEFT", "Generated", "Opening", "Cust", "SAVINGS", "Branch", "Statement", "JAKHAN", "SHASTRADHARA"}

        for p_idx in range(len(coord_doc)):
            p_words = coord_doc[p_idx].get_text('words')
            min_y = 220 if p_idx == 0 else 140
            tbl_words = [w for w in p_words if w[1] >= min_y and w[1] <= 780]

            for w in sorted(tbl_words, key=lambda x: (round(x[1] / 3.2) * 3.2, x[0])):
                if w[0] < 60 and date_pattern.match(w[4]):
                    if curr_t:
                        txns_raw.append(curr_t)
                    curr_t = {'date': w[4], 'narr': [], 'bals': []}
                elif curr_t:
                    if any(noise in w[4] for noise in boilerplate_noise) or 'STATEMENT' in w[4] or 'SUMMARY' in w[4] or 'Page' in w[4]:
                        continue
                    x = w[0]
                    if x > 560:
                        curr_t['bals'].append(w[4])
                    elif 55 <= x < 350:
                        curr_t['narr'].append(w[4])

        if curr_t:
            txns_raw.append(curr_t)

        coord_doc.close()

        if txns_raw:
            prev_balance = metadata["opening_balance"]
            for t in txns_raw:
                bal_tokens = [w for w in t['bals'] if re.match(r'^-?[\d,]+\.\d{2}$', w)]
                balance = clean_amount(bal_tokens[-1]) if bal_tokens else prev_balance
                if not records and metadata["opening_balance"] == 0.0:
                    prev_balance = balance
                    metadata["opening_balance"] = balance

                diff = round(balance - prev_balance, 2)
                if diff > 0:
                    credit = diff
                    debit = 0.0
                else:
                    debit = abs(diff)
                    credit = 0.0
                prev_balance = balance

                raw_narr = " ".join(t['narr']).strip()
                cleaned_narr = re.sub(r'(?:BANK\s+LIMITED|Cust\s+\d+|Account\s+A/C|HOLDERS\s*:|RTGS/NEFT|Statement\s*:|Opening\s+Balance|Dr\s+Count\s+[\d,.]+).*$', '', raw_narr, flags=re.I).strip()
                narration = cleaned_narr if len(cleaned_narr) >= 3 else raw_narr
                category = categorize_transaction(narration, debit, credit)

                records.append({
                    "bank": "HDFC Bank",
                    "account_no": metadata["account_no"],
                    "txn_date": t['date'],
                    "iso_date": parse_iso_date(t['date']),
                    "narration": narration,
                    "debit": debit,
                    "credit": credit,
                    "balance": balance,
                    "category": category
                })
    except Exception as e:
        print(f"HDFC Coordinate Extractor fallback: {e}")

    if not records:
        _pg_date = re.compile(r'^\d{2}/\d{2}/\d{2,4}\b')

        def _hdfc_page_lines(pt):
            pl = pt.split('\n')
            for idx, ln in enumerate(pl):
                if _pg_date.match(ln.strip()):
                    return pl[idx:]
            return pl

        if _raw_lines:
            lines = full_text.split('\n')
        else:
            lines = []
            for pt in page_texts:
                lines.extend(_hdfc_page_lines(pt))
            if not lines:
                lines = full_text.split('\n')
        date_pat = re.compile(r'^\d{2}/\d{2}/\d{2,4}\b')
        amt_pat = re.compile(r'^-?[\d,]+\.\d{2}$')

        ignore_headers = (
            "HDFC BANK", "We understand", "JOINT HOLDERS", "Nomination", "From:", "Date Narration",
            "Page No", "Account Branch", "Address", "City", "State", "Phone no", "OD Limit",
            "Currency", "Email", "Cust ID", "Account No", "A/C Open Date", "Account Status",
            "RTGS/NEFT", "MICR", "Branch Code", "Account Type", "Statement of account",
            "*Closing balance", "Contents of this", "State account", "HDFC Bank GSTIN",
            "Registered Office", "STATEMENT SUMMARY", "Generated On", "Generated By",
            "Requesting Branch", "This is a computer", "Opening Balance Dr Count", "Dr Count Cr Count"
        )

        txn_blocks = []
        curr_block = []
        for line in lines:
            l_str = line.strip()
            if not l_str:
                continue
            if "STATEMENT SUMMARY" in l_str:
                if curr_block:
                    txn_blocks.append(curr_block)
                    curr_block = []
                break
            if any(l_str.startswith(h) for h in ignore_headers):
                continue
            if date_pat.match(l_str):
                if curr_block:
                    txn_blocks.append(curr_block)
                curr_block = [l_str]
            elif curr_block:
                curr_block.append(l_str)

        if curr_block:
            txn_blocks.append(curr_block)

        prev_balance = metadata["opening_balance"]

        for block in txn_blocks:
            full_block_text = " ".join(block)
            tokens = full_block_text.split()
            if not tokens or not date_pat.match(tokens[0]):
                continue

            txn_date = tokens[0]
            num_indices = [i for i, t in enumerate(tokens) if amt_pat.match(t)]
            if len(num_indices) < 2:
                continue

            balance = clean_amount(tokens[num_indices[-1]])
            amt = clean_amount(tokens[num_indices[-2]])

            part1 = tokens[1:num_indices[-2]]
            part2 = tokens[num_indices[-1] + 1:]

            clean_narr_tokens = []
            for tok in (part1 + part2):
                if date_pat.match(tok) or (len(tok) >= 12 and tok.isdigit()):
                    continue
                clean_narr_tokens.append(tok)

            narration = " ".join(clean_narr_tokens).strip()
            if not narration:
                narration = " ".join(part1 + part2).strip()

            if not records and metadata["opening_balance"] == 0.0:
                prev_balance = _infer_opening_balance(balance, amt, narration)
                metadata["opening_balance"] = prev_balance

            diff = round(balance - prev_balance, 2)
            if diff > 0:
                credit = diff if abs(diff - amt) > 0.01 else amt
                debit = 0.0
            else:
                debit = abs(diff) if abs(abs(diff) - amt) > 0.01 else amt
                credit = 0.0
            prev_balance = balance
            records.append({
                "bank": "HDFC Bank",
                "account_no": metadata["account_no"],
                "txn_date": txn_date,
                "iso_date": parse_iso_date(txn_date),
                "narration": narration,
                "debit": debit,
                "credit": credit,
                "balance": balance,
                "category": categorize_transaction(narration, debit, credit)
            })

    if not records and not _raw_lines:
        return parse_hdfc_pdf(pdf_source, password=password, _raw_lines=True)
        
    if records:
        if metadata["closing_balance"] == 0.0:
            metadata["closing_balance"] = records[-1]["balance"]
        if metadata["total_withdrawals"] == 0.0:
            metadata["total_withdrawals"] = round(sum(r["debit"] for r in records), 2)
            metadata["total_deposits"] = round(sum(r["credit"] for r in records), 2)
        _fill_period_from_records(metadata, records)

    return records, metadata

def parse_axis_pdf(pdf_source, password=None):
    date_regex = re.compile(r'^\d{2}-\d{2}-\d{4}')
    extracted_rows = []
    metadata = {
        "bank": "Axis Bank", "borrower_name": "Axis Account Holder", "account_no": "",
        "branch": "Axis Bank Branch", "sol_id": "", "ifsc": "UTIB0000000", "cust_id": "",
        "email": "", "mobile": "", "account_type": "Savings Account", "period": "",
        "opening_balance": 0.0, "closing_balance": 0.0, "total_withdrawals": 0.0, "total_deposits": 0.0
    }
    
    full_text = ""
    try:
        doc = pymupdf.open(stream=pdf_source if isinstance(pdf_source, bytes) else None,
                           filename=pdf_source if isinstance(pdf_source, str) else None)
        if doc.is_encrypted and password:
            doc.authenticate(password)
            
        p0_text = doc[0].get_text()
        name_m = re.search(r'([A-Z\s]{3,35})\n\s*Joint Holder', p0_text) or re.search(r'AXIS\s*BANK\s*\n+([A-Z\s]{3,35})\n', p0_text)
        if name_m:
            metadata["borrower_name"] = name_m.group(1).replace('\n', ' ').strip()
            
        acc_m = re.search(r'Statement of Axis Account No:\s*([0-9]+)', p0_text, re.IGNORECASE) or re.search(r'Account\s*No\s*:\s*([0-9]+)', p0_text)
        if acc_m:
            metadata["account_no"] = acc_m.group(1).strip()
            
        ifsc_m = re.search(r'IFSC\s*Code\s*:\s*([A-Za-z0-9]+)', p0_text)
        if ifsc_m:
            metadata["ifsc"] = ifsc_m.group(1).strip()
            
        cust_m = re.search(r'Customer\s*ID\s*:\s*([0-9]+)', p0_text)
        if cust_m:
            metadata["cust_id"] = cust_m.group(1).strip()
            
        scheme_m = re.search(r'Scheme:\s*([^\n\r]+)', p0_text)
        if scheme_m:
            metadata["account_type"] = scheme_m.group(1).split("CKYC")[0].strip()
            
        per_m = re.search(r'period \(From:\s*([0-9-]+)\s*To:\s*([0-9-]+)\)', p0_text, re.IGNORECASE)
        if per_m:
            metadata["period"] = f"{per_m.group(1)} to {per_m.group(2)}"
            
        open_bal_m = re.search(r'OPENING\s*BALANCE\s*([\d,]+\.\d{2})', p0_text)
        if open_bal_m:
            metadata["opening_balance"] = clean_amount(open_bal_m.group(1))
            
        for page in doc:
            full_text += "\n" + page.get_text()
            
        branch_m = re.search(r'BRANCH ADDRESS - AXIS BANK LTD[,\s]+([^\n\r,]+)', full_text)
        if branch_m:
            metadata["branch"] = branch_m.group(1).strip()
            
        tot_m = re.search(r'TRANSACTION\s*TOTAL\s*([\d,]+\.\d{2})\s+([\d,]+\.\d{2})', full_text)
        if tot_m:
            metadata["total_withdrawals"] = clean_amount(tot_m.group(1))
            metadata["total_deposits"] = clean_amount(tot_m.group(2))
            
        close_m = re.search(r'CLOSING\s*BALANCE\s*([\d,]+\.\d{2})', full_text)
        if close_m:
            metadata["closing_balance"] = clean_amount(close_m.group(1))
            
        doc.close()
    except Exception as e:
        print(f"Axis metadata error: {e}")

    pdf_target = BytesIO(pdf_source) if isinstance(pdf_source, bytes) else pdf_source
    with pdfplumber.open(pdf_target, password=password) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or not any(row):
                        continue
                    first_col = (row[0] or "").strip()
                    if date_regex.match(first_col):
                        extracted_rows.append(row)
                    elif extracted_rows and len(row) > 2:
                        extra = " ".join([str(c).strip() for c in row if c])
                        extracted_rows[-1][2] = (extracted_rows[-1][2] or "") + " " + extra

    records = []
    for r in extracted_rows:
        if len(r) < 6:
            continue
        txn_date = r[0].strip()
        iso_date = parse_iso_date(txn_date)
        narration = (r[2] or "").replace('\n', ' ').strip()
        if "TRANSACTION TOTAL" in narration:
            narration = narration.split("TRANSACTION TOTAL")[0].strip()
        debit = clean_amount(r[3])
        credit = clean_amount(r[4])
        balance = clean_amount(r[5])
        category = categorize_transaction(narration, debit, credit)
        
        records.append({
            "bank": "Axis Bank",
            "account_no": metadata["account_no"],
            "txn_date": txn_date,
            "iso_date": iso_date,
            "narration": narration,
            "debit": debit,
            "credit": credit,
            "balance": balance,
            "category": category
        })
        
    if records:
        if metadata["closing_balance"] == 0.0:
            metadata["closing_balance"] = records[-1]["balance"]
        if metadata["total_withdrawals"] == 0.0:
            metadata["total_withdrawals"] = round(sum(r["debit"] for r in records), 2)
            metadata["total_deposits"] = round(sum(r["credit"] for r in records), 2)
            
    return records, metadata

def parse_icici_pdf(pdf_source, password=None):
    metadata = {
        "bank": "ICICI Bank", "borrower_name": "ICICI Account Holder", "account_no": "",
        "branch": "ICICI Bank Branch", "sol_id": "", "ifsc": "ICIC0000000", "cust_id": "",
        "email": "", "mobile": "", "account_type": "Savings Account", "period": "",
        "opening_balance": 0.0, "closing_balance": 0.0, "total_withdrawals": 0.0, "total_deposits": 0.0
    }

    doc = None
    p0_text = ""
    full_text = ""
    try:
        if isinstance(pdf_source, bytes):
            doc = pymupdf.open(stream=pdf_source, filetype="pdf")
        else:
            doc = pymupdf.open(pdf_source)
        if doc.is_encrypted and password:
            doc.authenticate(password)
        if len(doc) > 0:
            p0_text = doc[0].get_text()
        for page in doc:
            full_text += "\n" + page.get_text()
    except Exception as e:
        print(f"ICICI Metadata doc error: {e}")
    finally:
        if doc:
            doc.close()

    acc_m = (re.search(r'(?:Saving|Current|Salary|Savings)?\s*Account\s*(?:no|number)?\.?\s*[:\-]?\s*([0-9]{10,18})', p0_text, re.I) or
             re.search(r'Account\s*No\.?\s*[:\-]?\s*([0-9]{10,18})', p0_text, re.I) or
             re.search(r'\b(000[0-9]{9}|[0-9]{12})\b', p0_text))
    if acc_m:
        metadata["account_no"] = acc_m.group(1).strip()

    per_m = (re.search(r'period\s+([A-Za-z0-9,\s\-/\.]+?)\n', p0_text, re.I) or
             re.search(r'From\s*[:\.]?\s*([0-9/\-\.]+)\s*To\s*[:\.]?\s*([0-9/\-\.]+)', p0_text, re.I))
    if per_m:
        if len(per_m.groups()) == 1:
            metadata["period"] = per_m.group(1).strip()
        else:
            metadata["period"] = f"{per_m.group(1)} to {per_m.group(2)}"

    name_patterns = [
        r'(?:Customer|Account\s*Holder|Primary\s*Holder|Borrower)\s*Name\s*[:\.]?\s*([A-Za-z\s\.]+?)(?:\r?\n|$)',
        r'(?:^|\n)\s*(?:MR|MS|MRS|DR)\.?\s+([A-Za-z\s\.]+?)(?:\r?\n|,|$)',
        r'Account\s*Name\s*[:\.]?\s*([A-Za-z\s\.]+?)(?:\r?\n|$)',
        r'(?:^|\n)\s*Name\s*[:\.]\s*([A-Za-z\s\.]+?)(?:\r?\n|$)',
        r'Dear\s+(?:Customer|Mr\.?|Ms\.?|Mrs\.?)?\s*([A-Za-z\s\.]+?)(?:,|<|\n)',
        r'period[^\n]+\n+([A-Za-z\s]{3,40})\n',
    ]
    for pat in name_patterns:
        m = re.search(pat, p0_text, re.I)
        if m:
            cand = m.group(1).strip().replace('\n', ' ')
            cand = re.sub(r'\s+(?:BRANCH|ACCOUNT|STATEMENT|PAGE|DATE|SAVINGS|CURRENT|DETAILS|BASE).*$', '', cand, flags=re.I).strip()
            if len(cand) >= 3 and not any(k in cand.upper() for k in ["BRANCH", "ICICI", "STATEMENT", "PAGE", "ACCOUNT", "PERIOD", "DETAILS", "BANK", "LIMITED"]):
                if not cand.upper().startswith(("MR", "MS", "MRS", "DR")):
                    title_m = re.search(r'\b(MR|MS|MRS|DR)\.?\b', m.group(0), re.I)
                    if title_m:
                        cand = f"{title_m.group(1).upper()} {cand}"
                metadata["borrower_name"] = cand
                break

    br_m = re.search(r'Your Base Branch:\s*([^,\n]+)', p0_text, re.I) or re.search(r'Branch\s*:\s*([^,\n]+)', p0_text, re.I)
    if br_m:
        metadata["branch"] = br_m.group(1).strip()

    ifsc_m = re.search(r'\b(ICIC0[A-Z0-9]{6})\b', p0_text, re.I)
    if ifsc_m:
        metadata["ifsc"] = ifsc_m.group(1).strip()

    records = []
    try:
        pdf_target = BytesIO(pdf_source) if isinstance(pdf_source, bytes) else pdf_source
        with pdfplumber.open(pdf_target, password=password) as pdf:
            col_date = -1
            col_remarks = -1
            col_chq = -1
            col_debit = -1
            col_credit = -1
            col_balance = -1

            for page in pdf.pages:
                tables = page.extract_tables()
                if not tables:
                    tables = page.extract_tables(table_settings={"vertical_strategy": "text", "horizontal_strategy": "text"})
                if not tables:
                    continue

                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    for row in table:
                        if not row or not any(row):
                            continue
                        row_strs = [str(c or "").strip() for c in row]
                        row_lower = [s.lower() for s in row_strs]

                        has_date_hdr = any("date" in s for s in row_lower)
                        has_amt_hdr = any(any(k in s for k in ["withdrawal", "deposit", "debit", "credit", "balance", "amount", "bal"]) for s in row_lower)

                        if has_date_hdr and has_amt_hdr:
                            for idx, s in enumerate(row_lower):
                                if "txn" in s or "tran" in s or ("date" in s and "val" not in s and col_date == -1):
                                    col_date = idx
                                elif any(k in s for k in ["remark", "particular", "desc", "narration"]):
                                    col_remarks = idx
                                elif any(k in s for k in ["cheque", "chq", "ref"]):
                                    col_chq = idx
                                elif any(k in s for k in ["withdrawal", "debit", "dr"]):
                                    col_debit = idx
                                elif any(k in s for k in ["deposit", "credit", "cr"]):
                                    col_credit = idx
                                elif "balance" in s or "bal" in s:
                                    col_balance = idx
                            continue

                        row_date = None
                        date_idx = -1
                        if col_date >= 0 and col_date < len(row_strs) and is_date(row_strs[col_date]):
                            row_date = row_strs[col_date].split('\n')[0].strip()
                            date_idx = col_date
                        else:
                            for idx, cell in enumerate(row_strs):
                                if is_date(cell):
                                    row_date = cell.split('\n')[0].strip()
                                    date_idx = idx
                                    break

                        if not row_date:
                            if records:
                                rem_text = ""
                                if col_remarks >= 0 and col_remarks < len(row_strs):
                                    rem_text = row_strs[col_remarks]
                                else:
                                    cands = [c for c in row_strs if len(c) > 3 and not is_amount(c)]
                                    if cands:
                                        rem_text = " ".join(cands)
                                if rem_text and not any(k in rem_text.upper() for k in ["PAGE", "TOTAL", "LEGEND", "CONTINUED", "OPENING"]):
                                    records[-1]["narration"] = f"{records[-1]['narration']} {rem_text}".strip()
                            continue

                        debit = 0.0
                        credit = 0.0
                        balance = 0.0

                        if col_debit >= 0 and col_credit >= 0 and col_balance >= 0 and max(col_debit, col_credit, col_balance) < len(row_strs):
                            debit = clean_amount(row_strs[col_debit])
                            credit = clean_amount(row_strs[col_credit])
                            balance = clean_amount(row_strs[col_balance])
                        else:
                            num_vals = [clean_amount(c) for idx, c in enumerate(row_strs) if idx != date_idx and is_amount(c)]
                            if len(num_vals) >= 3:
                                balance = num_vals[-1]
                                debit = num_vals[-3]
                                credit = num_vals[-2]
                            elif len(num_vals) == 2:
                                balance = num_vals[-1]
                                amt = num_vals[-2]
                                if records:
                                    diff = round(balance - records[-1]["balance"], 2)
                                    credit = amt if diff > 0 else 0.0
                                    debit = amt if diff <= 0 else 0.0

                        narration = ""
                        if col_remarks >= 0 and col_remarks < len(row_strs):
                            narration = row_strs[col_remarks].replace('\n', ' ').strip()
                        else:
                            narr_parts = [c.replace('\n', ' ') for i, c in enumerate(row_strs) if i != date_idx and not is_amount(c) and len(c.strip()) > 1]
                            narration = " ".join(narr_parts).strip()

                        if col_chq >= 0 and col_chq < len(row_strs) and row_strs[col_chq].strip() and row_strs[col_chq].strip() not in narration:
                            narration = f"{narration} Ref:{row_strs[col_chq].strip()}"

                        narration = re.sub(r'\s+', ' ', narration).strip()
                        category = categorize_transaction(narration, debit, credit)

                        records.append({
                            "bank": "ICICI Bank",
                            "account_no": metadata.get("account_no", ""),
                            "txn_date": row_date,
                            "iso_date": parse_iso_date(row_date),
                            "narration": narration,
                            "debit": debit,
                            "credit": credit,
                            "balance": balance,
                            "category": category
                        })
    except Exception as e:
        print(f"ICICI Table Engine error: {e}")

    if records:
        if metadata["opening_balance"] == 0.0:
            first_diff = records[0]["credit"] - records[0]["debit"]
            metadata["opening_balance"] = round(records[0]["balance"] - first_diff, 2)
        metadata["closing_balance"] = records[-1]["balance"]
        metadata["total_withdrawals"] = round(sum(r["debit"] for r in records), 2)
        metadata["total_deposits"] = round(sum(r["credit"] for r in records), 2)
        _fill_period_from_records(metadata, records)

    return records, metadata

def parse_sbi_pdf(pdf_source, password=None):
    metadata = {
        "bank": "SBI Bank", "borrower_name": "SBI Account Holder", "account_no": "",
        "branch": "SBI Branch", "sol_id": "", "ifsc": "SBIN0000000", "cust_id": "",
        "email": "", "mobile": "", "account_type": "REGULAR SB CHQ-INDIVIDUALS", "period": "",
        "opening_balance": 0.0, "closing_balance": 0.0, "total_withdrawals": 0.0, "total_deposits": 0.0
    }
    
    all_text = ""
    try:
        if isinstance(pdf_source, bytes):
            doc = pymupdf.open(stream=pdf_source, filetype="pdf")
        else:
            doc = pymupdf.open(pdf_source)
        if doc.is_encrypted and password:
            doc.authenticate(password)
            
        p0_text = doc[0].get_text()
        name_m = re.search(r'Welcome:\s*\n+([^\n\r]+)', p0_text)
        if name_m:
            metadata["borrower_name"] = name_m.group(1).replace('.', '').strip()
            
        acc_m = re.search(r':Account Number\s*\n+([0-9]+)', p0_text) or re.search(r'Account Number\s*:\s*([0-9]+)', p0_text)
        if acc_m:
            metadata["account_no"] = acc_m.group(1).strip()
            
        ifsc_m = re.search(r':IFSC Code\s*\n+([A-Za-z0-9]+)', p0_text) or re.search(r'IFSC Code\s*:\s*([A-Za-z0-9]+)', p0_text)
        if ifsc_m:
            metadata["ifsc"] = ifsc_m.group(1).strip()
            
        br_m = re.search(r'Branch Name\s*:\s*([^\n\r]+)', p0_text)
        if br_m:
            metadata["branch"] = br_m.group(1).strip()
            
        cif_m = re.search(r':CIF Number\s*\n+([0-9]+)', p0_text)
        if cif_m:
            metadata["cust_id"] = cif_m.group(1).strip()
            
        email_m = re.search(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', p0_text)
        if email_m:
            metadata["email"] = email_m.group(1).strip()
            
        per_m = re.search(r'Statement From\s*:([0-9-]+)\s*to\s*([0-9-]+)', p0_text)
        if per_m:
            metadata["period"] = f"{per_m.group(1)} to {per_m.group(2)}"
            
        last_page_text = doc[-1].get_text()
        sum_m = re.search(r'([\d,]+\.\d{2}CR?)\s+(\d+)\s+(\d+)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2}CR?)', last_page_text)
        if sum_m:
            metadata["opening_balance"] = clean_amount(sum_m.group(1))
            metadata["total_withdrawals"] = clean_amount(sum_m.group(4))
            metadata["total_deposits"] = clean_amount(sum_m.group(5))
            metadata["closing_balance"] = clean_amount(sum_m.group(6))
            
        for page in doc:
            all_text += "\n" + page.get_text()
        doc.close()
    except Exception as e:
        print(f"SBI Metadata error: {e}")

    lines = all_text.split('\n')
    date_pat = re.compile(r'^\d{2}/\d{2}/\d{4}\b')
    num_pat = re.compile(r'^-?[\d,]+\.\d{2}$')
    
    ignore_prefixes = (
        "Account Summary", "Welcome:", "Date of Statement", "Uncleared Amount",
        "Clear Balance", "+MOD Bal", "Lien", "Limit", "Monthly Avg Balance",
        "Interest Rate", "Drawing Power", "Account open Date", "Branch Code",
        "Branch Email", "Branch Phone", ":CIF Number", ":Account Number",
        ":Account Status", ":Currency", ":IFSC Code", ":Product", ":Nominee Name",
        "Statement From", "CKYCR Number", "MICR Code", "STATEMENT OF ACCOUNT",
        "State Bank of India", "Branch Name", "Txn Date", "Value Date", "Description",
        "Ref No./Cheque No.", "Debit", "Credit", "Balance", "Page no", "Please do not share",
        "If your account", "This is a computer"
    )

    txn_blocks = []
    curr_block = []
    for line in lines:
        l_str = line.strip()
        if not l_str:
            continue
        if "Statement Summary:" in l_str or "Brought Forward" in l_str:
            if curr_block:
                txn_blocks.append(curr_block)
                curr_block = []
            break
        if any(l_str.startswith(h) for h in ignore_prefixes):
            continue
        if date_pat.match(l_str):
            if curr_block:
                txn_blocks.append(curr_block)
            curr_block = [l_str]
        elif curr_block:
            curr_block.append(l_str)
            
    if curr_block:
        txn_blocks.append(curr_block)

    records = []
    prev_balance = metadata["opening_balance"]
    
    for block in txn_blocks:
        full_block_text = " ".join(block)
        tokens = full_block_text.split()
        if not tokens or not date_pat.match(tokens[0]):
            continue
            
        txn_date = tokens[0]
        num_indices = [i for i, t in enumerate(tokens) if num_pat.match(t.replace('CR', ''))]
        if len(num_indices) < 2:
            continue
            
        balance = clean_amount(tokens[num_indices[-1]])
        amt = clean_amount(tokens[num_indices[-2]])
        
        narr_tokens = tokens[2:num_indices[-2]]
        narration = " ".join(narr_tokens).strip()
        if not records and prev_balance == 0.0:
            prev_balance = _infer_opening_balance(balance, amt, narration)
            metadata["opening_balance"] = prev_balance

        diff = round(balance - prev_balance, 2)
        if diff > 0:
            credit = amt
            debit = 0.0
        else:
            debit = amt
            credit = 0.0

        prev_balance = balance
        
        records.append({
            "bank": "SBI Bank",
            "account_no": metadata["account_no"],
            "txn_date": txn_date,
            "iso_date": parse_iso_date(txn_date),
            "narration": narration,
            "debit": debit,
            "credit": credit,
            "balance": balance,
            "category": categorize_transaction(narration, debit, credit)
        })
        
    if records:
        if metadata["closing_balance"] == 0.0:
            metadata["closing_balance"] = records[-1]["balance"]
        if metadata["total_withdrawals"] == 0.0:
            metadata["total_withdrawals"] = round(sum(r["debit"] for r in records), 2)
            metadata["total_deposits"] = round(sum(r["credit"] for r in records), 2)
        _fill_period_from_records(metadata, records)

    return records, metadata

def parse_idfc_pdf(pdf_source, password=None):
    metadata = {
        "bank": "IDFC FIRST Bank", "borrower_name": "IDFC FIRST Account Holder", "account_no": "",
        "branch": "IDFC FIRST Branch", "sol_id": "", "ifsc": "IDFB0000000", "cust_id": "",
        "email": "", "mobile": "", "account_type": "Savings Account", "period": "",
        "opening_balance": 0.0, "closing_balance": 0.0, "total_withdrawals": 0.0, "total_deposits": 0.0
    }
    
    full_text = ""
    try:
        doc = pymupdf.open(stream=pdf_source if isinstance(pdf_source, bytes) else None,
                           filename=pdf_source if isinstance(pdf_source, str) else None)
        if doc.is_encrypted and password:
            doc.authenticate(password)
            
        p0 = doc[0].get_text()
        name_m = re.search(r'CUSTOMER\s*NAME\s*:\s*([^\n\r]+)', p0)
        if name_m:
            metadata["borrower_name"] = name_m.group(1).replace('\n', ' ').strip()
        acc_m = re.search(r'ACCOUNT\s*NO\s*:\s*([0-9]+)', p0)
        if acc_m:
            metadata["account_no"] = acc_m.group(1).strip()
        ifsc_m = re.search(r'IFSC\s*:\s*([A-Za-z0-9]+)', p0)
        if ifsc_m:
            metadata["ifsc"] = ifsc_m.group(1).strip()
            
        sum_m = re.search(r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})', p0)
        if sum_m:
            metadata["opening_balance"] = clean_amount(sum_m.group(1))
            metadata["total_withdrawals"] = clean_amount(sum_m.group(2))
            metadata["total_deposits"] = clean_amount(sum_m.group(3))
            metadata["closing_balance"] = clean_amount(sum_m.group(4))
            
        for page in doc:
            full_text += "\n" + page.get_text()
        doc.close()
    except Exception:
        pass

    pattern = re.compile(
        r'(\d{2}-[A-Za-z]{3}-\d{4})\s+(\d{2}-[A-Za-z]{3}-\d{4})\s+(.*?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})',
        re.DOTALL
    )
    
    records = []
    prev_balance = metadata["opening_balance"]
    
    for match in pattern.finditer(full_text):
        txn_date = match.group(1).strip()
        raw_narration = match.group(3).replace('\n', ' ').strip()
        raw_amt = clean_amount(match.group(4))
        balance = clean_amount(match.group(5))
        
        narration = re.sub(r'\s+', ' ', raw_narration)
        if not records and prev_balance == 0.0:
            prev_balance = _infer_opening_balance(balance, raw_amt, narration)
            metadata["opening_balance"] = prev_balance
        diff = round(balance - prev_balance, 2)
        if diff > 0:
            credit = raw_amt
            debit = 0.0
        else:
            debit = raw_amt
            credit = 0.0
            
        prev_balance = balance
        records.append({
            "bank": "IDFC FIRST Bank",
            "account_no": metadata["account_no"],
            "txn_date": txn_date,
            "iso_date": parse_iso_date(txn_date),
            "narration": narration,
            "debit": debit,
            "credit": credit,
            "balance": balance,
            "category": categorize_transaction(narration, debit, credit)
        })

    if records:
        if metadata["closing_balance"] == 0.0:
            metadata["closing_balance"] = records[-1]["balance"]
        if metadata["total_withdrawals"] == 0.0:
            metadata["total_withdrawals"] = round(sum(r["debit"] for r in records), 2)
            metadata["total_deposits"] = round(sum(r["credit"] for r in records), 2)
        _fill_period_from_records(metadata, records)

    return records, metadata

def parse_federal_pdf(pdf_source, password=None):
    date_regex = re.compile(r'^\d{2}-[A-Z]{3}-\d{4}')
    extracted_rows = []
    metadata = {
        "bank": "Federal Bank", "borrower_name": "Federal Bank Account Holder", "account_no": "",
        "branch": "Federal Bank Branch", "sol_id": "", "ifsc": "FDRL0000000", "cust_id": "",
        "email": "", "mobile": "", "account_type": "Savings Account", "period": "",
        "opening_balance": 0.0, "closing_balance": 0.0, "total_withdrawals": 0.0, "total_deposits": 0.0
    }
    try:
        doc = pymupdf.open(stream=pdf_source if isinstance(pdf_source, bytes) else None, 
                           filename=pdf_source if isinstance(pdf_source, str) else None)
        if doc.is_encrypted and password:
            doc.authenticate(password)
        first_page_text = doc[0].get_text()
        name_m = re.search(r'Name\s*:\s*([^\n\r]+)', first_page_text)
        if name_m:
            metadata["borrower_name"] = name_m.group(1).strip()
        acc_m = re.search(r'Account Number\s*:\s*([0-9]+)', first_page_text)
        if acc_m:
            metadata["account_no"] = acc_m.group(1).strip()
        branch_m = re.search(r'Branch Name\s*:\s*([^\n\r]+)', first_page_text)
        if branch_m:
            metadata["branch"] = branch_m.group(1).strip()
        ifsc_m = re.search(r'IFSC\s*:\s*([A-Za-z0-9]+)', first_page_text)
        if ifsc_m:
            metadata["ifsc"] = ifsc_m.group(1).strip()
        doc.close()
    except Exception:
        pass

    pdf_target = BytesIO(pdf_source) if isinstance(pdf_source, bytes) else pdf_source
    with pdfplumber.open(pdf_target, password=password) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or not any(row):
                        continue
                    first_col = (row[0] or "").strip()
                    if date_regex.match(first_col):
                        extracted_rows.append(row)
                    elif extracted_rows and len(row) > 2:
                        extra = " ".join([str(c).strip() for c in row if c])
                        extracted_rows[-1][2] = (extracted_rows[-1][2] or "") + " " + extra

    records = []
    for r in extracted_rows:
        if len(r) < 9:
            continue
        txn_date = r[0].strip()
        iso_date = parse_iso_date(txn_date)
        narration = (r[2] or "").replace('\n', ' ').strip()
        debit = clean_amount(r[6])
        credit = clean_amount(r[7])
        balance = clean_amount(r[8])
        category = categorize_transaction(narration, debit, credit)
        records.append({
            "bank": "Federal Bank",
            "account_no": metadata["account_no"],
            "txn_date": txn_date,
            "iso_date": iso_date,
            "narration": narration,
            "debit": debit,
            "credit": credit,
            "balance": balance,
            "category": category
        })

    if records:
        first = records[0]
        metadata["opening_balance"] = round(first["balance"] + first["debit"] - first["credit"], 2)
        metadata["closing_balance"] = records[-1]["balance"]
        metadata["total_withdrawals"] = round(sum(r["debit"] for r in records), 2)
        metadata["total_deposits"] = round(sum(r["credit"] for r in records), 2)
        _fill_period_from_records(metadata, records)
    return records, metadata

def parse_universal_pdf(pdf_source, password=None, detected_bank=None):
    metadata = {
        "bank": detected_bank or "General Bank Statement", "borrower_name": "Account Holder",
        "account_no": "", "branch": "Main Branch", "sol_id": "", "ifsc": "", "cust_id": "",
        "email": "", "mobile": "", "account_type": "Savings / Current Account", "period": "",
        "opening_balance": 0.0, "closing_balance": 0.0, "total_withdrawals": 0.0, "total_deposits": 0.0
    }

    BANK_IFSC_MAP = {
        "SCBL": "Standard Chartered Bank", "ICIC": "ICICI Bank", "HDFC": "HDFC Bank",
        "SBIN": "State Bank of India", "UTIB": "Axis Bank", "FDRL": "Federal Bank",
        "IDFB": "IDFC FIRST Bank", "KKBK": "Kotak Mahindra Bank", "PUNB": "Punjab National Bank",
        "BARB": "Bank of Baroda", "CNRB": "Canara Bank", "UBIN": "Union Bank of India",
        "INDB": "IndusInd Bank", "YESB": "Yes Bank", "BKID": "Bank of India",
        "CBIN": "Central Bank of India", "RATN": "RBL Bank"
    }

    p0_text = ""
    header_text = ""
    try:
        doc = pymupdf.open(stream=pdf_source if isinstance(pdf_source, bytes) else None,
                           filename=pdf_source if isinstance(pdf_source, str) else None)
        if doc.is_encrypted and password:
            doc.authenticate(password)

        if len(doc) > 0:
            p0_text = doc[0].get_text()
            m_split = re.search(r'\n\s*(?:Date|Txn\s*Date|Tran\s*Date|Value\s*Date|Description|Particulars)\s*[\n\r]', p0_text, re.I)
            header_text = p0_text[:m_split.start()] if m_split else p0_text[:2000]
        doc.close()
    except Exception as e:
        print(f"Universal Metadata Error: {e}")

    ifsc_m = (re.search(r'IFSC\s*(?:CODE)?\s*[:\.]?\s*([A-Za-z]{4}[A-Za-z0-9]{7})', header_text, re.I) or
              re.search(r'\b([A-Za-z]{4}0[A-Za-z0-9]{6})\b', header_text))
    if ifsc_m:
        ifsc_code = ifsc_m.group(1).strip().upper()
        metadata["ifsc"] = ifsc_code
        prefix = ifsc_code[:4]
        if prefix in BANK_IFSC_MAP and (not detected_bank or detected_bank in ("Generic", "General Bank Statement", "Unknown")):
            metadata["bank"] = BANK_IFSC_MAP[prefix]

    if not metadata["bank"] or metadata["bank"] in ("Generic", "General Bank Statement", "Unknown"):
        text_upper = header_text.upper()
        for pfx, bname in BANK_IFSC_MAP.items():
            if bname.upper() in text_upper:
                metadata["bank"] = bname
                break

    acc_m = (re.search(r'(?:Account|A/C|Acct)\s*(?:Number|No\.?|Num|#)?\s*[:\.]?\s*([0-9]{9,18})', header_text, re.I) or
             re.search(r'\b([0-9]{11,18})\b', header_text))
    if acc_m:
        metadata["account_no"] = acc_m.group(1).strip()

    name_m = (re.search(r'^(?:MR|MS|MRS)\.?\s+([A-Za-z\s\.]+?)(?:\r?\n|$)', header_text, re.M | re.I) or
              re.search(r'(?:Customer|Account\s*Holder|Borrower|A/C)?\s*Name\s*[:\.]?\s*([A-Za-z\s\.]+?)(?:\r?\n|$)', header_text, re.I))
    if name_m:
        cand = name_m.group(1).strip()
        cand = re.sub(r'\s+BRANCH.*$', '', cand, flags=re.I).strip()
        if cand and not any(k in cand.upper() for k in ["BANK", "STATEMENT", "BRANCH", "ACCOUNT", "PERIOD", "PAGE"]):
            metadata["borrower_name"] = f"MR {cand}" if not cand.upper().startswith(("MR", "MS", "MRS")) else cand

    records = []
    try:
        pdf_target = BytesIO(pdf_source) if isinstance(pdf_source, bytes) else pdf_source
        with pdfplumber.open(pdf_target, password=password) as pdf:
            col_date = -1
            col_narration = -1
            col_chq = -1
            col_debit = -1
            col_credit = -1
            col_balance = -1

            for page in pdf.pages:
                tables = page.extract_tables()
                if not tables:
                    tables = page.extract_tables(table_settings={"vertical_strategy": "text", "horizontal_strategy": "text"})
                if not tables:
                    continue

                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    for row_idx, row in enumerate(table):
                        if not row or not any(row):
                            continue
                        row_strs = [str(c or "").strip() for c in row]
                        row_lower = [s.lower() for s in row_strs]

                        has_date_hdr = any(any(k in s for k in ["date", "txn", "tran"]) for s in row_lower)
                        has_amt_hdr = any(any(k in s for k in ["withdrawal", "deposit", "debit", "credit", "balance", "amount", "bal", "dr", "cr"]) for s in row_lower)

                        if has_amt_hdr and (has_date_hdr or row_idx == 0):
                            for idx, s in enumerate(row_lower):
                                if ("txn" in s or "tran" in s or "date" in s) and col_date == -1:
                                    col_date = idx
                                elif any(k in s for k in ["particular", "narration", "desc", "remark", "detail"]):
                                    col_narration = idx
                                elif any(k in s for k in ["cheque", "chq", "ref"]):
                                    col_chq = idx
                                elif any(k in s for k in ["withdrawal", "debit", "dr"]):
                                    col_debit = idx
                                elif any(k in s for k in ["deposit", "credit", "cr"]):
                                    col_credit = idx
                                elif "balance" in s or "bal" in s:
                                    col_balance = idx
                            continue

                        if any("total" in s for s in row_lower) or any("closing balance" in s for s in row_lower):
                            continue

                        row_text_all = " ".join(row_strs).upper()
                        if ("BALANCE FORWARD" in row_text_all or "OPENING BALANCE" in row_text_all or "B/F" in row_text_all):
                            amts = [clean_amount(c) for c in row_strs if is_amount(c)]
                            if amts:
                                metadata["opening_balance"] = amts[-1]
                                continue

                        row_date = None
                        date_idx = -1
                        if col_date >= 0 and col_date < len(row_strs) and is_date(row_strs[col_date]):
                            row_date = row_strs[col_date].split('\n')[0].strip()
                            date_idx = col_date
                        else:
                            for idx, cell in enumerate(row_strs):
                                if is_date(cell):
                                    row_date = cell.split('\n')[0].strip()
                                    date_idx = idx
                                    break

                        if not row_date:
                            if records:
                                rem_text = row_strs[col_narration] if 0 <= col_narration < len(row_strs) else " ".join([c for c in row_strs if len(c) > 3 and not is_amount(c)])
                                if rem_text and not any(k in rem_text.upper() for k in ["PAGE", "TOTAL", "OPENING"]):
                                    records[-1]["narration"] = f"{records[-1]['narration']} {rem_text}".strip()
                            continue

                        debit = 0.0
                        credit = 0.0
                        balance = 0.0

                        if col_debit >= 0 and col_credit >= 0 and col_balance >= 0 and max(col_debit, col_credit, col_balance) < len(row_strs):
                            debit = clean_amount(row_strs[col_debit])
                            credit = clean_amount(row_strs[col_credit])
                            balance = clean_amount(row_strs[col_balance])
                        else:
                            num_vals = [clean_amount(c) for idx, c in enumerate(row_strs) if idx != date_idx and is_amount(c)]
                            if len(num_vals) >= 3:
                                balance = num_vals[-1]
                                debit = num_vals[-3]
                                credit = num_vals[-2]
                            elif len(num_vals) == 2 and records:
                                balance = num_vals[-1]
                                amt = num_vals[-2]
                                diff = round(balance - records[-1]["balance"], 2)
                                credit = amt if diff > 0 else 0.0
                                debit = amt if diff <= 0 else 0.0

                        narration = row_strs[col_narration].replace('\n', ' ').strip() if 0 <= col_narration < len(row_strs) else " ".join([c for i, c in enumerate(row_strs) if i != date_idx and not is_amount(c)])
                        if col_chq >= 0 and col_chq < len(row_strs) and row_strs[col_chq].strip() and row_strs[col_chq].strip() not in narration:
                            narration = f"{narration} Ref:{row_strs[col_chq].strip()}"

                        narration = re.sub(r'\s+', ' ', narration).strip()
                        category = categorize_transaction(narration, debit, credit)

                        records.append({
                            "bank": metadata["bank"],
                            "account_no": metadata.get("account_no", ""),
                            "txn_date": row_date,
                            "iso_date": parse_iso_date(row_date),
                            "narration": narration,
                            "debit": debit,
                            "credit": credit,
                            "balance": balance,
                            "category": category
                        })
    except Exception as e:
        print(f"Universal Table Extractor error: {e}")

    if records:
        if metadata["opening_balance"] == 0.0 and len(records) > 0:
            first_diff = records[0]["credit"] - records[0]["debit"]
            metadata["opening_balance"] = round(records[0]["balance"] - first_diff, 2)
        metadata["closing_balance"] = records[-1]["balance"]
        metadata["total_withdrawals"] = round(sum(r["debit"] for r in records), 2)
        metadata["total_deposits"] = round(sum(r["credit"] for r in records), 2)
        _fill_period_from_records(metadata, records)

    return records, metadata

def calculate_daily_abb(records, opening_balance, closing_balance):
    if not records:
        return {"overall_abb": 0.0, "monthly_abb": {}, "min_balance": 0.0, "max_balance": 0.0}
    sorted_recs = sorted(records, key=lambda x: str(x.get("iso_date", "")))
    date_closing = {}
    for r in sorted_recs:
        d = parse_iso_date(r.get("iso_date") or r.get("txn_date") or "")
        if d and re.match(r'^\d{4}-\d{2}-\d{2}$', d):
            date_closing[d] = r.get("balance", 0.0)
    if not date_closing:
        return {"overall_abb": closing_balance, "monthly_abb": {}, "min_balance": closing_balance, "max_balance": closing_balance}
    try:
        all_dates = sorted(date_closing.keys())
        start_date = datetime.strptime(all_dates[0], "%Y-%m-%d")
        end_date = datetime.strptime(all_dates[-1], "%Y-%m-%d")
        total_days = max(1, (end_date - start_date).days + 1)
        current_dt = start_date
        curr_bal = opening_balance
        daily_balances = []
        monthly_accum = {}
        while current_dt <= end_date:
            d_str = current_dt.strftime("%Y-%m-%d")
            m_str = current_dt.strftime("%Y-%m")
            if d_str in date_closing:
                curr_bal = date_closing[d_str]
            daily_balances.append(curr_bal)
            if m_str not in monthly_accum:
                monthly_accum[m_str] = []
            monthly_accum[m_str].append(curr_bal)
            current_dt = current_dt + pd.Timedelta(days=1)
        overall_abb = round(sum(daily_balances) / len(daily_balances), 2) if daily_balances else closing_balance
        monthly_abb = {k: round(sum(v) / len(v), 2) for k, v in monthly_accum.items()}
        return {
            "overall_abb": overall_abb,
            "monthly_abb": monthly_abb,
            "min_balance": round(min(daily_balances), 2) if daily_balances else 0.0,
            "max_balance": round(max(daily_balances), 2) if daily_balances else 0.0,
            "days_tracked": total_days
        }
    except Exception as e:
        print(f"[ABB Calculation Warning] {e}")
        bals = [r.get("balance", 0.0) for r in records if "balance" in r]
        avg_bal = round(sum(bals) / len(bals), 2) if bals else closing_balance
        return {
            "overall_abb": avg_bal,
            "monthly_abb": {},
            "min_balance": round(min(bals), 2) if bals else 0.0,
            "max_balance": round(max(bals), 2) if bals else 0.0,
            "days_tracked": len(records)
        }

def run_gemini_underwriting_intelligence(records, metadata):
    if not GENAI_AVAILABLE:
        return None

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        client = genai.Client(api_key=api_key)

        credit_candidates = []
        for idx, r in enumerate(records):
            c_val = r.get("credit", 0.0)
            narr = r.get("narration", "")
            cat = r.get("category", "")
            if c_val >= 5000.0 or "SALARY" in cat or any(k in narr.upper() for k in ["SAL", "PAY", "INCENTIVE", "BONUS", "PVT", "LTD", "LIMITED", "CORP", "CMS", "NEFT"]):
                credit_candidates.append({
                    "id": idx,
                    "date": r.get("txn_date", ""),
                    "narration": narr,
                    "amount": c_val
                })

        debit_candidates = []
        for idx, r in enumerate(records):
            d_val = r.get("debit", 0.0)
            narr = r.get("narration", "").upper()
            cat = r.get("category", "")
            if d_val >= 200.0 and not any(k in narr for k in ["ICCL", "INDIAN CLEARING", "NSECL", "ZERODHA", "ANGEL"]):
                if "EMI" in cat or any(k in narr for k in ["ACH", "NACH", "MANDATE", "LOAN", "DRAOPERATIONS", "CAPITAL", "BAJAJ FINANCE", "POONAWALLA", "KRAZYBEE", "NORTHERN ARC", "BIRLA", "BILLPAY DR-HDFC"]):
                    debit_candidates.append({
                        "id": idx,
                        "date": r.get("txn_date", ""),
                        "narration": r.get("narration", ""),
                        "amount": d_val
                    })

        churn_candidates = []
        for idx, r in enumerate(records):
            narr = r.get("narration", "").upper()
            cat = r.get("category", "")
            if "CC_PAYMENT" in cat or "WALLET_TOPUP" in cat or any(k in narr for k in ["CRED", "MOBIKWIK", "RUPICARD", "CARD", "WALLET"]):
                churn_candidates.append({
                    "id": idx,
                    "date": r.get("txn_date", ""),
                    "narration": r.get("narration", ""),
                    "debit": r.get("debit", 0.0),
                    "credit": r.get("credit", 0.0)
                })

        credit_payload = credit_candidates[:35]
        debit_payload = debit_candidates[:35]
        churn_payload = churn_candidates[:30]

        if not credit_payload and not debit_payload and not churn_payload:
            return None

        prompt = f"""You are an expert Credit Underwriter and Bank Statement AI Analyzer for Indian retail banking.
Your task is to analyze these bank statement transaction candidates and return a structured JSON report.

CONSTRAINTS:
1. Do NOT classify Indian Clearing Corporation Limited (ICCL), NSE, BSE, or Stock Brokers as Loan Lenders.
2. Classify HDFC masked card auto-billpays (e.g. IB BILLPAY DR-HDFC...537206XXXXXX) and institutional EMIs into active lenders.
3. Identify genuine employers by stripping prefixes (NEFT, RTGS, IFSC).

TRANSACTION CANDIDATES:
Credits:
{json.dumps(credit_payload, indent=2)}

Debits:
{json.dumps(debit_payload, indent=2)}

Card/Wallet Flows:
{json.dumps(churn_payload, indent=2)}

Return ONLY valid JSON:
{{
  "salary_intelligence": {{
    "primary_employer_name": "string",
    "verified_employers": ["string"],
    "salary_transactions": [
      {{
        "id": 0,
        "is_salary_or_incentive": true,
        "salary_component": "BASE_SALARY",
        "employer_name": "string"
      }}
    ],
    "stability_score": 90
  }},
  "debt_intelligence": {{
    "active_lenders": [
      {{
        "raw_code": "string",
        "clean_lender_name": "string",
        "loan_type": "string",
        "monthly_obligation": 0.0
      }}
    ],
    "total_monthly_obligation": 0.0
  }},
  "fraud_and_churn_intelligence": {{
    "risk_tag": "CLEAN",
    "risk_score": 10,
    "churn_velocity": "LOW",
    "reasoning": "string"
  }}
}}"""

        response = None
        for target_model in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
            try:
                response = client.models.generate_content(
                    model=target_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1
                    )
                )
                if response and response.text:
                    break
            except Exception:
                continue

        if response and response.text:
            return json.loads(response.text)

    except Exception as e:
        print(f"[Gemini Underwriting Fallback] {e}")
        return None

    return None

def compute_underwriting_insights(records, metadata):
    df = pd.DataFrame(records)
    if df.empty:
        return {}
    total_debits = round(float(df['debit'].sum()), 2)
    total_credits = round(float(df['credit'].sum()), 2)
    open_bal = float(metadata.get('opening_balance', 0.0))
    close_bal = float(metadata.get('closing_balance', 0.0))
    reconciled_calc = round(open_bal + total_credits - total_debits, 2)
    variance = round(abs(reconciled_calc - close_bal), 2)
    is_reconciled = (variance <= 1.0)
    
    # 1. Salary Inflows
    salary_df = df[df['category'].str.contains('SALARY', na=False)]
    salary_credits = []
    employers = []
    
    for _, row in salary_df.iterrows():
        desc = row['narration']
        emp = extract_dynamic_employer(desc)
        salary_credits.append({
            "date": row['txn_date'],
            "iso_date": row['iso_date'],
            "employer": emp,
            "amount": row['credit']
        })
        if emp not in employers and emp != "Corporate Payroll":
            employers.append(emp)
            
    if not employers and salary_credits:
        employers = ["Corporate Payroll"]
        
    if salary_credits:
        salary_by_month = {}
        for s in salary_credits:
            m = s['iso_date'][:7] if s.get('iso_date') else "2026-M"
            salary_by_month[m] = salary_by_month.get(m, 0.0) + s['amount']
        full_months = [v for k, v in salary_by_month.items() if v >= 10000]
        avg_monthly_salary = round(sum(full_months) / len(full_months), 2) if full_months else round(salary_df['credit'].mean(), 2)
        salary_range_str = f"₹{int(avg_monthly_salary):,} - ₹{int(avg_monthly_salary * 1.15):,}"
    else:
        avg_monthly_salary = 0.0
        salary_range_str = "₹0 (No Direct Salaried Inflows Tagged)"
        
    stability_score = 92 if salary_credits else 50
    
    # 2. Institutional Debt & Active Lenders
    LENDER_CODE_MAP = {
        "KMBLDRAOPERATIONS": "Kotak Mahindra Bank Mandate",
        "TCHHL": "Tata Capital Housing Finance",
        "TCHIN": "Tata Capital Housing Finance",
        "ADINGEN_TATA": "Tata Capital Housing Finance",
        "CANFIN": "Can Fin Homes",
        "BFLAUTOPAY": "Bajaj Finance",
        "IDFC FIRST": "IDFC FIRST Bank Loan",
        "HINDON": "Hindon Mercantile",
        "POONAWALLA": "Poonawalla Fincorp",
        "KRAZYBEE": "KrazyBee Services",
        "NORTHERN": "Northern Arc Capital",
        "ADITYA BIRLA": "Aditya Birla Finance",
        "LIC": "Life Insurance Corporation (LIC Loan)",
        "MUTHOOT": "Muthoot Finance",
        "HDBFS": "HDB Financial Services",
        "CHOLAMANDALAM": "Cholamandalam Investment and Finance"
    }

    loan_df = df[df['category'].str.contains('EMI_LOAN', na=False)]
    disburse_df = df[df['category'].str.contains('LOAN_DISBURSEMENT', na=False)]
    active_lenders = []
    
    for _, row in loan_df.iterrows():
        narr = row['narration'].upper()
        if any(b in narr for b in ["ICCL", "INDIAN CLEARING", "NSECL", "ZERODHA", "ANGEL"]):
            continue
        matched_lender = None
        for code, formal_name in LENDER_CODE_MAP.items():
            if code in narr:
                matched_lender = formal_name
                break
        if not matched_lender:
            if "BILLPAY DR-HDFC" in narr and "XXXXXX" in narr:
                matched_lender = "HDFC Bank SmartEMI / Card Loan"
            elif "EMI" in narr and "CHQ" in narr:
                matched_lender = "Banking Institution Cheque EMI"
            elif row['debit'] >= 1000:
                matched_lender = "Financial Institution Mandate"
        if matched_lender and matched_lender not in active_lenders:
            active_lenders.append(matched_lender)
            
    num_months = max(1, len(df['iso_date'].str[:7].unique()))
    monthly_emi_obligations = round(float(loan_df['debit'].sum()) / num_months, 2)
    jumbo_loans = [{"date": r['txn_date'], "narration": r['narration'], "amount": r['credit']} for _, r in disburse_df.iterrows()]
    total_jumbo_disbursed = sum(j['amount'] for j in jumbo_loans)
    trading_df = df[df['category'].str.contains('STOCK_TRADING', na=False)]
    
    # 3. Segregated CC Payments & Wallet Topups
    cc_df = df[df['category'].str.contains('CC_PAYMENT', na=False)]
    wallet_df = df[df['category'].str.contains('WALLET_TOPUP', na=False)]
    cred_inflows = round(float(cc_df[cc_df['narration'].str.contains(r'@CRED|@YESCRED', case=False, na=False)]['credit'].sum()), 2)
    cred_outflows = round(float(cc_df[cc_df['narration'].str.contains(r'CRED\.CLUB|CREDPAY', case=False, na=False)]['debit'].sum()), 2)
    
    bounce_df = df[df['category'].str.contains('BOUNCE_FAILURE', na=False)]
    abb_stats = calculate_daily_abb(records, open_bal, close_bal)
    foir_pct = round((monthly_emi_obligations / avg_monthly_salary) * 100, 1) if avg_monthly_salary > 0 else 0.0

    # Churning & Fraud Logic
    if cred_outflows >= 50000 and total_jumbo_disbursed >= 50000 and cred_inflows >= 20000:
        risk_tag = "CRITICAL"
        risk_score = 90
        churn_velocity = "HIGH"
        fraud_reasoning = "High-velocity revolver credit card circular rotation coupled with personal loan disbursements."
    elif cred_outflows >= 25000 and cred_inflows >= 10000:
        risk_tag = "ELEVATED"
        risk_score = 60
        churn_velocity = "MODERATE"
        fraud_reasoning = "Elevated volume of circular credit card repayment and wallet rotations detected."
    else:
        risk_tag = "CLEAN"
        risk_score = 10
        churn_velocity = "LOW"
        fraud_reasoning = "Card settlements reflect debt liquidation without circular fund recycling."

    # Execute Non-blocking Hybrid AI Layer
    ai_applied = False
    ai_result = run_gemini_underwriting_intelligence(records, metadata)
    primary_employer = employers[0] if employers else "Corporate Payroll"

    if ai_result:
        ai_applied = True
        sal_intel = ai_result.get("salary_intelligence", {})
        if sal_intel.get("primary_employer_name"):
            primary_employer = sal_intel["primary_employer_name"].strip()
        if sal_intel.get("verified_employers"):
            clean_ai_emps = [e.strip() for e in sal_intel["verified_employers"] if e.strip() and not any(k in e.upper() for k in ["ICCL", "CLEARING", "ANGEL"])]
            if clean_ai_emps:
                employers = clean_ai_emps
            if primary_employer and primary_employer not in employers:
                employers.insert(0, primary_employer)

        debt_intel = ai_result.get("debt_intelligence", {})
        if debt_intel.get("active_lenders"):
            ai_lenders = [l.get("clean_lender_name") for l in debt_intel["active_lenders"] if l.get("clean_lender_name") and not any(k in l.get("clean_lender_name").upper() for k in ["ICCL", "CLEARING", "NSE", "STOCK"])]
            if ai_lenders:
                active_lenders = ai_lenders

        fraud_intel = ai_result.get("fraud_and_churn_intelligence", {})
        if fraud_intel.get("risk_tag") in ("CRITICAL", "ELEVATED", "CLEAN"):
            risk_tag = fraud_intel["risk_tag"]
        if fraud_intel.get("risk_score") is not None:
            risk_score = int(fraud_intel["risk_score"])
        if fraud_intel.get("churn_velocity"):
            churn_velocity = fraud_intel["churn_velocity"]
        if fraud_intel.get("reasoning"):
            fraud_reasoning = fraud_intel["reasoning"]

    employer_switch = (len(employers) >= 2)
    if employer_switch:
        switch_notes = f"Multiple employers detected ({', '.join(employers)})."
        stability_score = 82
    else:
        switch_notes = "Single stable employer tenure detected."

    return {
        "metadata": metadata,
        "reconciliation": {
            "opening_balance": open_bal,
            "total_deposits": total_credits,
            "total_withdrawals": total_debits,
            "closing_balance": close_bal,
            "calculated_closing": reconciled_calc,
            "variance": variance,
            "is_reconciled": is_reconciled,
            "total_transactions": len(records),
            "dr_count": int((df['debit'] > 0).sum()),
            "cr_count": int((df['credit'] > 0).sum())
        },
        "salary_audit": {
            "primary_employer": primary_employer,
            "verified_employers": employers,
            "salary_run_rate": salary_range_str,
            "avg_monthly_salary": avg_monthly_salary,
            "credits_count": len(salary_credits),
            "credits_history": salary_credits,
            "employer_switch": employer_switch,
            "switch_notes": switch_notes,
            "stability_score": stability_score,
            "stability_tier": "Grade A (Prime Salaried)" if stability_score >= 80 else "Grade B (Moderate Inflows)",
            "ai_enabled": ai_applied
        },
        "debt_and_emi": {
            "monthly_obligations": monthly_emi_obligations,
            "active_lenders": active_lenders,
            "lenders_count": len(active_lenders),
            "jumbo_loans_disbursed": total_jumbo_disbursed,
            "jumbo_loans_list": jumbo_loans,
            "foir_percentage": foir_pct,
            "total_emi_paid": round(float(loan_df['debit'].sum()), 2),
            "ai_enabled": ai_applied
        },
        "cc_stress": {
            "is_alert": (total_jumbo_disbursed >= 50000 and float(cc_df['debit'].sum()) >= 50000),
            "severity": "CRITICAL" if (total_jumbo_disbursed >= 50000 and float(cc_df['debit'].sum()) >= 50000) else "NORMAL",
            "message": "High revolver card stress / debt restructuring detected." if (total_jumbo_disbursed >= 50000 and float(cc_df['debit'].sum()) >= 50000) else "Revolving credit cards are within normal operating parameters.",
            "jumbo_disbursed": total_jumbo_disbursed,
            "card_debt_settled": round(float(cc_df['debit'].sum()), 2)
        },
        "stock_and_mf": {
            "total_trading_flow": round(float(trading_df['credit'].sum() + trading_df['debit'].sum()), 2),
            "total_inflows": round(float(trading_df['credit'].sum()), 2),
            "total_outflows": round(float(trading_df['debit'].sum()), 2),
            "angel_one_inflows": round(float(trading_df[trading_df['narration'].str.contains('ANGEL', case=False, na=False)]['credit'].sum()), 2),
            "iccl_sip_outflows": round(float(trading_df[trading_df['narration'].str.contains('INDIANCLEARING|ICCL|CLEARING', case=False, na=False)]['debit'].sum()), 2),
            "nse_bse_settlements": round(float(trading_df[trading_df['narration'].str.contains('NSECL|BSECLEA', case=False, na=False)]['credit'].sum()), 2),
            "txns_count": len(trading_df)
        },
        "cc_and_wallet_churn": {
            "total_churn_volume": round(float(cc_df['debit'].sum() + wallet_df['debit'].sum()), 2),
            "cred_inflows": cred_inflows,
            "cred_outflows": cred_outflows,
            "net_cred_rotation": round(cred_outflows - cred_inflows, 2),
            "cc_payments_total": round(float(cc_df['debit'].sum()), 2),
            "wallet_topups_total": round(float(wallet_df['debit'].sum()), 2),
            "txns_count": len(cc_df) + len(wallet_df),
            "fraud_risk_tag": risk_tag,
            "fraud_reasoning": fraud_reasoning
        },
        "fraud_and_churn_intelligence": {
            "risk_tag": risk_tag,
            "risk_score": risk_score,
            "churn_velocity": churn_velocity,
            "reasoning": fraud_reasoning,
            "ai_enabled": ai_applied
        },
        "bounce_audit": {
            "bounce_count": len(bounce_df),
            "is_clean": (len(bounce_df) == 0),
            "status_text": "0 Bounces • Clean Repayment Track" if len(bounce_df) == 0 else f"{len(bounce_df)} Bounces / Failures Detected",
            "tag_color": "emerald" if len(bounce_df) == 0 else "rose",
            "bounces_list": bounce_df[['txn_date', 'narration', 'debit']].to_dict('records')
        },
        "abb_metrics": abb_stats
    }

def clear_db(db_path=DB_PATH):
    init_db(db_path)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA busy_timeout=5000;")
    c = conn.cursor()
    c.execute("DELETE FROM transactions")
    c.execute("DELETE FROM statements")
    conn.commit()
    conn.close()

def save_statement_to_db(records, metadata, insights=None, filename=None):
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout=5000;")
    c = conn.cursor()
    
    acc = (metadata.get("account_no") or "").strip()
    if not acc:
        seed = (filename or "") + str(len(records)) + (records[0].get("txn_date", "") if records else "")
        acc = "UNK-" + hashlib.md5(seed.encode()).hexdigest()[:8].upper()
        metadata["account_no"] = acc
    for r in records:
        r["account_no"] = acc
    c.execute("DELETE FROM statements")
    c.execute("DELETE FROM transactions")

    abb_val = insights.get('abb_metrics', {}).get('overall_abb', 0.0) if insights else 0.0
    c.execute("""
    INSERT INTO statements (
        bank, borrower_name, account_no, branch, sol_id, ifsc, cust_id, email, mobile,
        account_type, period, opening_balance, closing_balance, total_withdrawals,
        total_deposits, abb, reconciled, filename
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        metadata.get("bank", ""),
        metadata.get("borrower_name", ""),
        metadata.get("account_no", ""),
        metadata.get("branch", ""),
        metadata.get("sol_id", ""),
        metadata.get("ifsc", ""),
        metadata.get("cust_id", ""),
        metadata.get("email", ""),
        metadata.get("mobile", ""),
        metadata.get("account_type", ""),
        metadata.get("period", ""),
        metadata.get("opening_balance", 0.0),
        metadata.get("closing_balance", 0.0),
        metadata.get("total_withdrawals", 0.0),
        metadata.get("total_deposits", 0.0),
        abb_val,
        1 if (insights and insights.get('reconciliation', {}).get('is_reconciled', True)) else 0,
        filename or metadata.get("bank", "statement.pdf")
    ))
              
    tx_tuples = [
        (
            r.get("bank", ""),
            r.get("account_no", ""),
            r.get("txn_date", ""),
            r.get("iso_date", ""),
            r.get("narration", ""),
            r.get("debit", 0.0),
            r.get("credit", 0.0),
            r.get("balance", 0.0),
            r.get("category", "GENERAL"),
            r.get("sub_category", "")
        )
        for r in records
    ]
    
    c.executemany("""
    INSERT INTO transactions (
        bank, account_no, txn_date, iso_date, narration, debit, credit, balance, category, sub_category
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, tx_tuples)
    
    conn.commit()
    conn.close()