import os
import re
import sqlite3
import pandas as pd
import pdfplumber

def clean_amount(val):
    if not val or not str(val).strip():
        return 0.0
    val = str(val).replace(',', '').replace(' ', '').strip()
    try:
        return float(val)
    except ValueError:
        return 0.0

def categorize_transaction(desc):
    d = desc.upper()
    flags = []
    
    # 1. Cheque Bounce / Mandate / EMI Failures
    if re.search(r'\b(RETURN|BOUNCE|RET CHG|BOUNCE CHG|INSUFFICIENT|ACH RET|NACH RET|ECS RET|CHQ RET|DISHONOUR)\b', d):
        flags.append("BOUNCE_FAILURE")

    # 2. Salary check
    if re.search(r'\b(SALARY|PAYROLL|CMS/SAL|NEFT-SAL)\b', d):
        flags.append("SALARY")
        
    # 3. Stock Market / Broker / Trading / MF Clearing
    if re.search(r'(ANGEL|ZERODHA|GROWW|UPSTOX|INDIAN CLEARING|CLEARING CORP|NSE|BSE|CDSL|NSDL|ICCL)', d):
        flags.append("STOCK_TRADING")
        
    # 4. EMI & Loan Payments / Loan Disbursements
    if re.search(r'(ACHDR|NACH|IDFC FIRST|LOAN REC|HINDON|BFLAUTOPAY|BAJAJ|INSTALOAN|INSTA LOAN)', d) and not re.search(r'INSTAMART', d):
        if "BOUNCE_FAILURE" not in flags:
            flags.append("EMI_LOAN")
        
    # 5. Credit Card & Wallet Rotation / Churning
    if re.search(r'(CRED\.CLUB|CREDITCARDS@RBL|@CRED|CREDIT CARD|CC AUTOPAY|UPI-LITE|PAYTMQR|MOBIKWIK)', d):
        flags.append("CC_WALLET_CHURN")
        
    return ", ".join(flags) if flags else "GENERAL"

def parse_federal_bank(pdf_path):
    date_regex = re.compile(r'^\d{2}-[A-Z]{3}-\d{4}')
    extracted_rows = []
    
    with pdfplumber.open(pdf_path) as pdf:
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
                        extracted_rows[-1][2] += " " + extra
                        
    records = []
    for r in extracted_rows:
        if len(r) < 9:
            continue
        txn_date = r[0].strip()
        narration = r[2].replace('\n', ' ').strip()
        debit = clean_amount(r[6])
        credit = clean_amount(r[7])
        balance = clean_amount(r[8])
        category = categorize_transaction(narration)
        
        records.append({
            "bank": "Federal Bank",
            "txn_date": txn_date,
            "narration": narration,
            "debit": debit,
            "credit": credit,
            "balance": balance,
            "category": category
        })
    return records

def parse_hdfc_bank(pdf_path):
    records = []
    date_regex = re.compile(r'^\d{2}/\d{2}/\d{2}$')
    last_balance = 74.04  # HDFC Opening Balance
    
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            
            lines = text.split('\n')
            for line in lines:
                parts = line.strip().split()
                if not parts:
                    continue
                
                if date_regex.match(parts[0]):
                    txn_date = parts[0]
                    curr_balance = clean_amount(parts[-1])
                    rem_parts = parts[1:-1]
                    
                    narration_tokens = []
                    for token in rem_parts:
                        if (len(token) >= 12 and token.isdigit()) or date_regex.match(token):
                            break
                        narration_tokens.append(token)
                    
                    narration = " ".join(narration_tokens) if narration_tokens else " ".join(rem_parts[:-2])
                    
                    diff = round(curr_balance - last_balance, 2)
                    if diff > 0:
                        credit = diff
                        debit = 0.0
                    else:
                        debit = abs(diff)
                        credit = 0.0
                        
                    last_balance = curr_balance
                    
                    records.append({
                        "bank": "HDFC Bank",
                        "txn_date": txn_date,
                        "narration": narration,
                        "debit": debit,
                        "credit": credit,
                        "balance": curr_balance,
                        "category": "GENERAL"
                    })
                elif records and not parts[0].startswith("Page") and not parts[0].startswith("STATEMENT") and not parts[0].startswith("Contents") and not parts[0].startswith("HDFC"):
                    records[-1]["narration"] += " " + line.strip()

    # Final pass to categorize with full consolidated narration
    for r in records:
        r["category"] = categorize_transaction(r["narration"])
        
    return records

def save_and_analyze(all_records, db_name="bank_statement.db"):
    df = pd.DataFrame(all_records)
    
    conn = sqlite3.connect(db_name)
    df.to_sql("transactions", conn, if_exists="replace", index=False)
    
    print("\n" + "="*50)
    print(f"DATABASE UPDATE: {len(df)} transactions successfully saved to '{db_name}'.")
    print("="*50)
    
    bounces_df = df[df['category'].str.contains('BOUNCE_FAILURE', na=False)]
    print("\n[1] CHEQUE / MANDATE BOUNCE REPORT:")
    if not bounces_df.empty:
        print(f"CRITICAL WARNING: Total {len(bounces_df)} bounce/failure transactions detected!")
        print(bounces_df[['bank', 'txn_date', 'narration', 'debit']].to_string(index=False))
    else:
        print("CLEAN: 0 cheque/EMI bounces found across all statements.")
        
    print("\n[2] FINANCIAL CATEGORY SUMMARY:")
    summary = df.groupby(['bank', 'category'])[['debit', 'credit']].sum().reset_index()
    print(summary.to_string(index=False))
    
    loans_df = df[df['category'].str.contains('EMI_LOAN', na=False)]
    print("\n[3] ACTIVE EMIs & LOANS (Sample Rows):")
    if not loans_df.empty:
        print(loans_df[['bank', 'txn_date', 'narration', 'debit', 'credit']].head(10).to_string(index=False))
    else:
        print("No active loan/EMI deductions detected.")
        
    trading_df = df[df['category'].str.contains('STOCK_TRADING', na=False)]
    print("\n[4] STOCK MARKET & TRADING ACTIVITY:")
    if not trading_df.empty:
        print(trading_df[['bank', 'txn_date', 'narration', 'debit', 'credit']].to_string(index=False))
    else:
        print("No stock market or trading transactions detected.")

    conn.close()

if __name__ == "__main__":
    combined_data = []
    
    fed_file = "federal_statement_unlocked.pdf"
    hdfc_file = "hdfc_statement_unlocked.pdf"
    
    if os.path.exists(fed_file):
        fed_records = parse_federal_bank(fed_file)
        combined_data.extend(fed_records)
        print(f"Federal Bank Parsed: {len(fed_records)} rows.")
    else:
        print(f"File not found: '{fed_file}'")

    if os.path.exists(hdfc_file):
        hdfc_records = parse_hdfc_bank(hdfc_file)
        combined_data.extend(hdfc_records)
        print(f"HDFC Bank Parsed: {len(hdfc_records)} rows.")
    else:
        print(f"File not found: '{hdfc_file}'")

    if combined_data:
        save_and_analyze(combined_data)
    else:
        print("Koi PDF process nahi ho saki.")