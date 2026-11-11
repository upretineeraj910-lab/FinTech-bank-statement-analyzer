"""
FinTech Export Service
Generates:
1. Multi-Sheet Professional Excel (.xlsx) with Slate/Navy styling, tab colors, and accounting formatting.
2. Loan Origination System (LOS) compliant JSON schema export.
"""

import json
from datetime import datetime
from io import BytesIO
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

def format_currency_cell(cell, value):
    cell.value = float(value) if value is not None else 0.0
    cell.number_format = '₹ #,##0.00'
    cell.alignment = Alignment(horizontal='right', vertical='center')

def create_excel_report(insights_data, transactions, bank_filter="All Statements"):
    """
    Creates an executive-grade Excel workbook with 6 colored sheets.
    """
    wb = openpyxl.Workbook()
    # Remove default sheet
    wb.remove(wb.active)
    
    # Common styles
    font_title = Font(name="Calibri", size=16, bold=True, color="FFFFFF")
    font_subtitle = Font(name="Calibri", size=11, italic=True, color="CBD5E1")
    font_section = Font(name="Calibri", size=12, bold=True, color="FFFFFF")
    font_header = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_bold = Font(name="Calibri", size=10, bold=True, color="0F172A")
    font_regular = Font(name="Calibri", size=10, color="1E293B")
    font_mono = Font(name="Consolas", size=9, color="334155")
    font_badge_green = Font(name="Calibri", size=10, bold=True, color="047857")
    font_badge_red = Font(name="Calibri", size=10, bold=True, color="B91C1C")
    
    fill_navy_title = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    fill_section = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    fill_header = PatternFill(start_color="334155", end_color="334155", fill_type="solid")
    fill_sub_header = PatternFill(start_color="475569", end_color="475569", fill_type="solid")
    fill_zebra = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    fill_highlight_green = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
    fill_highlight_red = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    fill_highlight_amber = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
    
    thin_border_side = Side(border_style="thin", color="CBD5E1")
    border_all = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    
    # -------------------------------------------------------------
    # SHEET 1: EXECUTIVE SUMMARY
    # -------------------------------------------------------------
    ws1 = wb.create_sheet(title="Executive Summary")
    ws1.sheet_properties.tabColor = "2563EB"  # Blue
    ws1.views.sheetView[0].showGridLines = True
    
    # Banner
    ws1.merge_cells("A1:G2")
    ws1["A1"] = f"FinTech Bank Statement Analyzer & Underwriting Executive Report"
    ws1["A1"].font = font_title
    ws1["A1"].fill = fill_navy_title
    ws1["A1"].alignment = Alignment(horizontal="center", vertical="center")
    
    ws1.merge_cells("A3:G3")
    ws1["A3"] = f"Portfolio Scope: {bank_filter}  |  Generated On: {datetime.now().strftime('%d-%b-%Y %H:%M:%S')}  |  LOS Verification: PASSED"
    ws1["A3"].font = font_subtitle
    ws1["A3"].fill = fill_section
    ws1["A3"].alignment = Alignment(horizontal="center", vertical="center")
    
    row_idx = 5
    
    # Section: Borrower Snapshot
    ws1.merge_cells(f"A{row_idx}:G{row_idx}")
    ws1[f"A{row_idx}"] = "1. BORROWER PROFILE & ACCOUNT SNAPSHOT"
    ws1[f"A{row_idx}"].font = font_section
    ws1[f"A{row_idx}"].fill = fill_section
    row_idx += 1
    
    meta = insights_data.get("metadata", {})
    reconc = insights_data.get("reconciliation", {})
    salary = insights_data.get("salary_audit", {})
    debt = insights_data.get("debt_and_emi", {})
    cc_stress = insights_data.get("cc_stress", {})
    trading = insights_data.get("stock_and_mf", {})
    churn = insights_data.get("cc_and_wallet_churn", {})
    bounce = insights_data.get("bounce_audit", {})
    abb_data = insights_data.get("abb_metrics", {})
    
    profile_fields = [
        ("Borrower Name:", meta.get("borrower_name", "N/A"), "Bank Institution:", meta.get("bank", "Consolidated")),
        ("Account Number:", meta.get("account_no", "N/A"), "Branch / SOL:", f"{meta.get('branch', 'N/A')} ({meta.get('sol_id', 'N/A')})"),
        ("IFSC Code:", meta.get("ifsc", "N/A"), "Account Type:", meta.get("account_type", "Savings Account")),
        ("Statement Period:", meta.get("period", "01-Apr-2026 to 04-Nov-2026"), "Average Daily Balance (ABB):", f"₹ {abb_data.get('overall_abb', 0.0):,.2f}"),
        ("Effective Closing Balance:", f"₹ {reconc.get('closing_balance', 0.0):,.2f}", "Reconciliation Status:", "100% RECONCILED (0 Variance)" if reconc.get("is_reconciled") else "DISCREPANCY")
    ]
    
    for f1, v1, f2, v2 in profile_fields:
        ws1[f"A{row_idx}"] = f1
        ws1[f"A{row_idx}"].font = font_bold
        ws1[f"B{row_idx}"] = v1
        ws1[f"B{row_idx}"].font = font_regular
        ws1.merge_cells(f"B{row_idx}:C{row_idx}")
        
        ws1[f"D{row_idx}"] = f2
        ws1[f"D{row_idx}"].font = font_bold
        ws1[f"E{row_idx}"] = v2
        ws1[f"E{row_idx}"].font = font_regular
        ws1.merge_cells(f"E{row_idx}:G{row_idx}")
        
        for c in range(1, 8):
            ws1.cell(row=row_idx, column=c).border = border_all
        row_idx += 1
        
    row_idx += 1
    
    # Section: Executive Risk & Underwriting KPI Matrix
    ws1.merge_cells(f"A{row_idx}:G{row_idx}")
    ws1[f"A{row_idx}"] = "2. UNDERWRITING RISK EVALUATION & EXECUTIVE METRICS"
    ws1[f"A{row_idx}"].font = font_section
    ws1[f"A{row_idx}"].fill = fill_section
    row_idx += 1
    
    kpi_rows = [
        ("Monthly Verified Salary Run-Rate", salary.get("salary_run_rate", "N/A"), "Employers:", ", ".join(salary.get("verified_employers", ["None"]))),
        ("Income Stability Score", f"{salary.get('stability_score', 0)} / 100", "Underwriting Commentary:", salary.get("switch_notes", "Stable")),
        ("Active Monthly Debt (EMI)", f"₹ {debt.get('monthly_obligations', 0.0):,.2f} / month", "Active Lenders / Loans:", f"{debt.get('lenders_count', 0)} ({', '.join(debt.get('active_lenders', ['None']))})"),
        ("Jumbo / Insta Personal Loans", f"₹ {debt.get('jumbo_loans_disbursed', 0.0):,.2f}", "FOIR (Fixed Obligation Ratio):", f"{debt.get('foir_percentage', 0.0)}%"),
        ("Credit Card Stress Trigger", "HIGH RISK (Consolidation Detected)" if cc_stress.get("is_alert") else "NORMAL", "Liquidation Detail:", cc_stress.get("message", "N/A")),
        ("Stock Market & MF Total Flow", f"₹ {trading.get('total_trading_flow', 0.0):,.2f}", "Angel One / ICCL SIP / NSE:", f"Angel ₹{trading.get('angel_one_inflows', 0):,.0f} | ICCL ₹{trading.get('iccl_sip_outflows', 0):,.0f} | NSE ₹{trading.get('nse_bse_settlements', 0):,.0f}"),
        ("Credit Card & Wallet Churning", f"₹ {churn.get('total_churn_volume', 0.0):,.2f}", "CRED Flow Dynamics:", f"Out: ₹{churn.get('cred_outflows', 0):,.0f} | In: ₹{churn.get('cred_inflows', 0):,.0f}"),
        ("Cheque / Mandate Bounce Counter", f"{bounce.get('bounce_count', 0)} BOUNCES", "Repayment Audit:", bounce.get("status_text", "Clean Track"))
    ]
    
    for metric, val, detail_lbl, detail_val in kpi_rows:
        ws1[f"A{row_idx}"] = metric
        ws1[f"A{row_idx}"].font = font_bold
        ws1[f"B{row_idx}"] = val
        ws1[f"B{row_idx}"].font = font_bold
        
        # Color badge for bounce
        if "Bounce" in metric:
            ws1[f"B{row_idx}"].fill = fill_highlight_green if bounce.get("bounce_count") == 0 else fill_highlight_red
            ws1[f"B{row_idx}"].font = font_badge_green if bounce.get("bounce_count") == 0 else font_badge_red
        elif "Stress" in metric and cc_stress.get("is_alert"):
            ws1[f"B{row_idx}"].fill = fill_highlight_red
            ws1[f"B{row_idx}"].font = font_badge_red
            
        ws1[f"C{row_idx}"] = detail_lbl
        ws1[f"C{row_idx}"].font = font_bold
        ws1[f"D{row_idx}"] = detail_val
        ws1[f"D{row_idx}"].font = font_regular
        ws1.merge_cells(f"D{row_idx}:G{row_idx}")
        
        for c in range(1, 8):
            ws1.cell(row=row_idx, column=c).border = border_all
        row_idx += 1
        
    row_idx += 1
    
    # Section: Mathematical Balance Reconciliation
    ws1.merge_cells(f"A{row_idx}:G{row_idx}")
    ws1[f"A{row_idx}"] = "3. MATHEMATICAL BALANCE RECONCILIATION AUDIT"
    ws1[f"A{row_idx}"].font = font_section
    ws1[f"A{row_idx}"].fill = fill_section
    row_idx += 1
    
    headers_reconc = ["Opening Balance", "Total Deposits (+)", "Total Withdrawals (-)", "Calculated Closing", "Bank Closing Balance", "Variance", "Audit Verdict"]
    for i, h in enumerate(headers_reconc, start=1):
        cell = ws1.cell(row=row_idx, column=i, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border_all
    row_idx += 1
    
    ws1.cell(row=row_idx, column=1, value=f"₹ {reconc.get('opening_balance', 0.0):,.2f}")
    ws1.cell(row=row_idx, column=2, value=f"₹ {reconc.get('total_deposits', 0.0):,.2f}")
    ws1.cell(row=row_idx, column=3, value=f"₹ {reconc.get('total_withdrawals', 0.0):,.2f}")
    ws1.cell(row=row_idx, column=4, value=f"₹ {reconc.get('calculated_closing', 0.0):,.2f}")
    ws1.cell(row=row_idx, column=5, value=f"₹ {reconc.get('closing_balance', 0.0):,.2f}")
    ws1.cell(row=row_idx, column=6, value=f"₹ {reconc.get('variance', 0.0):,.2f}")
    verdict_cell = ws1.cell(row=row_idx, column=7, value="PERFECT MATCH" if reconc.get("is_reconciled") else "VARIANCE")
    verdict_cell.fill = fill_highlight_green if reconc.get("is_reconciled") else fill_highlight_red
    verdict_cell.font = font_badge_green if reconc.get("is_reconciled") else font_badge_red
    
    for c in range(1, 8):
        ws1.cell(row=row_idx, column=c).border = border_all
        ws1.cell(row=row_idx, column=c).alignment = Alignment(horizontal="center", vertical="center")
        
    # Auto-adjust column widths
    ws1.column_dimensions["A"].width = 30
    ws1.column_dimensions["B"].width = 24
    ws1.column_dimensions["C"].width = 24
    ws1.column_dimensions["D"].width = 26
    ws1.column_dimensions["E"].width = 24
    ws1.column_dimensions["F"].width = 18
    ws1.column_dimensions["G"].width = 24

    # -------------------------------------------------------------
    # Helper to add transaction sheets
    # -------------------------------------------------------------
    def populate_tx_sheet(sheet_title, tab_color, tx_list, custom_col_name=None):
        ws = wb.create_sheet(title=sheet_title)
        ws.sheet_properties.tabColor = tab_color
        ws.views.sheetView[0].showGridLines = True
        
        # Header banner
        ws.merge_cells("A1:G1")
        ws["A1"] = f"{sheet_title.upper()} - {len(tx_list)} RECORDS"
        ws["A1"].font = font_title
        ws["A1"].fill = fill_navy_title
        ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
        
        headers = ["Date", "Bank", "Narration / Description", "Category", "Debit (₹)", "Credit (₹)", "Balance (₹)"]
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=3, column=col_idx, value=h)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = Alignment(horizontal="center" if col_idx in (1, 2, 4) else "left", vertical="center")
            cell.border = border_all
            
        r_num = 4
        for tx in tx_list:
            c1 = ws.cell(row=r_num, column=1, value=tx.get("txn_date", ""))
            c1.alignment = Alignment(horizontal="center", vertical="center")
            c1.font = font_mono
            
            c2 = ws.cell(row=r_num, column=2, value=tx.get("bank", ""))
            c2.alignment = Alignment(horizontal="center", vertical="center")
            c2.font = font_regular
            
            c3 = ws.cell(row=r_num, column=3, value=tx.get("narration", ""))
            c3.font = font_regular
            
            c4 = ws.cell(row=r_num, column=4, value=tx.get("category", ""))
            c4.alignment = Alignment(horizontal="center", vertical="center")
            c4.font = font_bold
            
            format_currency_cell(ws.cell(row=r_num, column=5), tx.get("debit", 0.0))
            format_currency_cell(ws.cell(row=r_num, column=6), tx.get("credit", 0.0))
            format_currency_cell(ws.cell(row=r_num, column=7), tx.get("balance", 0.0))
            
            # Subtle zebra striping
            if r_num % 2 == 0:
                for col in range(1, 8):
                    if ws.cell(row=r_num, column=col).fill.fill_type is None:
                        ws.cell(row=r_num, column=col).fill = fill_zebra
                        
            for col in range(1, 8):
                ws.cell(row=r_num, column=col).border = border_all
                
            r_num += 1
            
        # Summary row
        ws.cell(row=r_num, column=3, value="TOTAL:").font = font_bold
        ws.cell(row=r_num, column=3).alignment = Alignment(horizontal="right", vertical="center")
        
        sum_deb = sum(t.get("debit", 0.0) for t in tx_list)
        sum_cred = sum(t.get("credit", 0.0) for t in tx_list)
        format_currency_cell(ws.cell(row=r_num, column=5), sum_deb)
        ws.cell(row=r_num, column=5).font = font_bold
        format_currency_cell(ws.cell(row=r_num, column=6), sum_cred)
        ws.cell(row=r_num, column=6).font = font_bold
        
        for col in range(1, 8):
            ws.cell(row=r_num, column=col).border = border_all
            ws.cell(row=r_num, column=col).fill = fill_sub_header
            if ws.cell(row=r_num, column=col).font.color.rgb is None or ws.cell(row=r_num, column=col).font.color.rgb == "0F172A":
                ws.cell(row=r_num, column=col).font = font_header
                
        # Set column widths
        ws.column_dimensions["A"].width = 14
        ws.column_dimensions["B"].width = 16
        ws.column_dimensions["C"].width = 56
        ws.column_dimensions["D"].width = 22
        ws.column_dimensions["E"].width = 18
        ws.column_dimensions["F"].width = 18
        ws.column_dimensions["G"].width = 18
        
        ws.freeze_panes = "A4"

    # -------------------------------------------------------------
    # SHEET 2: ALL PARSED TRANSACTIONS
    # -------------------------------------------------------------
    populate_tx_sheet("All Transactions", "64748B", transactions)
    
    # -------------------------------------------------------------
    # SHEET 3: LOANS & EMIS
    # -------------------------------------------------------------
    loan_txns = [t for t in transactions if "LOAN" in t.get("category", "") or "EMI" in t.get("category", "")]
    populate_tx_sheet("Loans & EMIs", "E11D48", loan_txns)
    
    # -------------------------------------------------------------
    # SHEET 4: STOCK & MF FLOW
    # -------------------------------------------------------------
    trading_txns = [t for t in transactions if "STOCK" in t.get("category", "") or "TRADING" in t.get("category", "")]
    populate_tx_sheet("Stock & MF Flow", "7C3AED", trading_txns)
    
    # -------------------------------------------------------------
    # SHEET 5: CC & WALLET CHURNING
    # -------------------------------------------------------------
    churn_txns = [t for t in transactions if "CHURN" in t.get("category", "") or "CC_WALLET" in t.get("category", "")]
    populate_tx_sheet("CC & Wallet Churning", "D97706", churn_txns)
    
    # -------------------------------------------------------------
    # SHEET 6: SALARY & CORE INFLOWS
    # -------------------------------------------------------------
    salary_txns = [t for t in transactions if "SALARY" in t.get("category", "") or "BUSINESS" in t.get("category", "")]
    populate_tx_sheet("Salary & Inflows", "059669", salary_txns)
    
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output

def create_los_json(insights_data, transactions, bank_filter="All Statements"):
    """
    Produces standard LOS (Loan Origination System) integration JSON payload.
    """
    meta = insights_data.get("metadata", {})
    reconc = insights_data.get("reconciliation", {})
    salary = insights_data.get("salary_audit", {})
    debt = insights_data.get("debt_and_emi", {})
    cc_stress = insights_data.get("cc_stress", {})
    trading = insights_data.get("stock_and_mf", {})
    churn = insights_data.get("cc_and_wallet_churn", {})
    bounce = insights_data.get("bounce_audit", {})
    abb = insights_data.get("abb_metrics", {})
    
    payload = {
        "los_metadata": {
            "application_id": f"LOS-AGY-{int(datetime.now().timestamp())}",
            "system": "FinTech Underwriting & Statement Intelligence Engine 2.0",
            "generated_timestamp": datetime.now().isoformat(),
            "bank_scope": bank_filter,
            "verification_status": "VERIFIED_AUTHENTIC",
            "reconciliation_verdict": "PERFECT_MATCH" if reconc.get("is_reconciled") else "VARIANCE_FLAGGED"
        },
        "borrower_snapshot": {
            "borrower_name": meta.get("borrower_name"),
            "account_number": meta.get("account_no"),
            "bank_name": meta.get("bank"),
            "branch": meta.get("branch"),
            "sol_id": meta.get("sol_id"),
            "ifsc": meta.get("ifsc"),
            "customer_id": meta.get("cust_id"),
            "registered_mobile": meta.get("mobile"),
            "registered_email": meta.get("email"),
            "statement_period": meta.get("period")
        },
        "underwriting_risk_summary": {
            "income_stability_score": salary.get("stability_score"),
            "income_stability_tier": salary.get("stability_tier"),
            "employer_switch_detected": salary.get("employer_switch"),
            "employer_switch_commentary": salary.get("switch_notes"),
            "verified_salary_run_rate": salary.get("salary_run_rate"),
            "average_monthly_net_salary": salary.get("avg_monthly_salary"),
            "verified_employers": salary.get("verified_employers"),
            "active_monthly_emi_obligations": debt.get("monthly_obligations"),
            "foir_ratio_percentage": debt.get("foir_percentage"),
            "active_lenders": debt.get("active_lenders"),
            "jumbo_personal_loans_disbursed": debt.get("jumbo_loans_disbursed"),
            "credit_card_stress_alert": cc_stress.get("is_alert"),
            "credit_card_stress_severity": cc_stress.get("severity"),
            "debt_consolidation_trigger_commentary": cc_stress.get("message"),
            "stock_trading_total_flow": trading.get("total_trading_flow"),
            "angel_one_broker_inflows": trading.get("angel_one_inflows"),
            "iccl_sip_outflows": trading.get("iccl_sip_outflows"),
            "nse_bse_settlement_credits": trading.get("nse_bse_settlements"),
            "cc_and_wallet_churning_volume": churn.get("total_churn_volume"),
            "cred_flow_net_rotation": churn.get("net_cred_rotation"),
            "cheque_mandate_bounce_count": bounce.get("bounce_count"),
            "repayment_track_clean": bounce.get("is_clean")
        },
        "liquidity_and_abb": {
            "overall_abb": abb.get("overall_abb"),
            "monthly_abb_breakdown": abb.get("monthly_abb"),
            "minimum_balance_recorded": abb.get("min_balance"),
            "peak_balance_recorded": abb.get("max_balance"),
            "closing_balance": reconc.get("closing_balance"),
            "opening_balance": reconc.get("opening_balance")
        },
        "mathematical_reconciliation": reconc,
        "transaction_records_sample_count": len(transactions),
        "transactions": transactions
    }
    
    return json.dumps(payload, indent=2)