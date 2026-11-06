# FinTech Bank Statement Analyzer & Underwriting Dashboard

A modern, executive-grade FinTech application for parsing, auditing, and underwriting Indian bank statements (Federal Bank 10-column, HDFC Bank 7-column, SBI, and ICICI layouts). Built with **FastAPI**, **SQLite**, **Tailwind CSS**, and **Chart.js**.

---

## 🌟 Key Features & Capabilities

### 1. Drag-and-Drop Multi-File Uploader
- **Universal Dropzone**: Upload bank statement PDFs with drag-and-drop or file picker.
- **Auto-Layout Detection**: Automatically distinguishes 10-column Federal Bank tables from 7-column HDFC text streams, SBI, and ICICI formats.
- **Password Decryption Modal**: Built-in native modal prompt for password-protected statements (supporting DOB/PAN formats).
- **5-Phase Progress Pipeline**: Live progress indicator displaying layout detection, table extraction, categorization, and balance reconciliation.

### 2. Executive Risk & KPI Cards (Top Bar)
- **Monthly Verified Salary**: Extracted employer name (*Berg Technologies Private Limited* → *Aashree Management Services*) with net salary run-rate (**~₹17k - ₹20k/mo**).
- **Active Debt & EMI Outflows**: Monthly obligations counter (**₹9,663/mo**) + Jumbo personal loans (**₹1,55,000 disbursed** across Jumbo & Insta loans).
- **Stock Market & MF Flow**: Broker withdrawals (*Angel One*) and clearing house settlements (*NSE/BSE/ICCL SIP debits*).
- **Credit Card & Wallet Churning**: Tracks circular rotation volume (**₹1.66L+** across CRED club, UPI-Lite, and card bill settlements).
- **Cheque / Mandate Bounce Counter**: Green indicator tag for **0 bounces** (clean repayment track across 1,891 audited rows).

### 3. Underwriting Insights Sidebar
- **Borrower Snapshot**: Name (*K N Said Ibin* / *Mr. Neeraj Upreti*), Account No, Branch, SOL ID, IFSC, and Average Daily Balance (**ABB: ₹20,055 HDFC / ₹8,540 Federal**).
- **Income Stability Score**: 82/100 (Grade A Prime Salaried). Automatically detects employer switches with salary increment (+18.3%).
- **Credit Card Stress Warning**: Debt consolidation alert trigger identifying that a ₹1,10,000 Jumbo Personal Loan was directly utilized to liquidate ₹1,12,655 in revolving credit card debt.
- **FOIR & Debt Burden**: Real-time fixed obligations to income ratio (~48.2%) and active lenders list (*IDFC First Bank, Bajaj Finance AutoPay, Federal Bank Loan, Hindon Mercantile*).

### 4. Mathematical Balance Reconciliation Bar
- Automatically verifies:
  $$\text{Opening Balance} + \sum \text{Credits} - \sum \text{Debits} = \text{Closing Balance}$$
- Displays real-time zero variance status tag (*100% Match • 0 Discrepancy*).

### 5. Multi-Tab Dynamic Transaction Explorer
- **Tab 1: All Parsed Transactions**: Global search, date range picker, amount range filter, category pills, and sorting.
- **Tab 2: Salary & Core Inflows**: Company NEFT credits (*Berg Technologies*, *Aashree Management*) and recurring client deposits.
- **Tab 3: Loans & EMIs**: Dedicated ledger of *IDFC First, Bajaj AutoPay, Federal Bank loan, HDFC Insta Loan, Jumbo Loan*.
- **Tab 4: Stock Trading & MF**: *Angel One* broker withdrawals, *NSE/BSE* clearing dividends, and *ICCL* SIP debits.
- **Tab 5: Wallet & Credit Card Churning**: Circular flows (*CRED* payouts vs instant card payments, *UPI-Lite* micro-transactions).
- **Tab 6: Bounce Audit**: 0 bounces verified report.

### 6. Export Features
- **Excel (.xlsx)**: Executive-grade workbook with 6 separate color-coded tabs:
  1. 📊 *Executive Summary* (Navy headers, borrower info, risk matrix, reconciliation audit)
  2. 📑 *All Transactions*
  3. 💳 *Loans & EMIs*
  4. 📈 *Stock & MF Flow*
  5. 🔄 *CC & Wallet Churning*
  6. 💼 *Salary & Inflows*
- **LOS JSON Export**: Standard Loan Origination System schema formatted for downstream credit decisioning.

---

## 🚀 Running the Application

The web server runs on FastAPI and Uvicorn:

```bash
# Start the server
python -m uvicorn server:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser at **[http://127.0.0.1:8000](http://127.0.0.1:8000)**.

---

## 🛠 Project Structure

- `analyzer_engine.py`: PDF parser, bank layout detector, password handler, categorizer, and underwriting metrics engine.
- `export_service.py`: Generates the styled 6-sheet Excel report (`openpyxl`) and LOS JSON payload.
- `server.py`: FastAPI server with REST endpoints for dashboard, transactions, upload, and exports.
- `templates/index.html`: Dark Navy/Slate executive dashboard UI (Tailwind CSS, Inter font, Chart.js, Lucide icons).
- `bank_statement.db`: SQLite database storing parsed statements and enriched transactions.
- `federal_statement_unlocked.pdf`: Sample 46-page Federal Bank statement (10-column table layout).
- `hdfc_statement_unlocked.pdf`: Sample 40-page HDFC Bank statement (7-column text layout).
- `HH.pdf`: Sample 7-page HDFC statement excerpt.
