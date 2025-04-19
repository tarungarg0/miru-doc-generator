import streamlit as st

from datetime import datetime
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Side, Font, PatternFill
from openpyxl.drawing.image import Image as XLImage
from io import BytesIO
import os
import tempfile

st.markdown("""
    <h2 style='text-align: center; font-family: Bebas Neue Pro Expanded;'>MIRU Document Generator</h2>
    <hr style='margin-top: 0;'>
    <style>
        label, .stTextInput>div>div>input, .stTextArea textarea, .stNumberInput input {
            font-size: 16px;
            font-family: 'Bebas Neue Pro Expanded', sans-serif;
            color: #2E2E2E;
        }
        .stRadio label {
            font-size: 16px;
            font-family: 'Bebas Neue Pro Expanded', sans-serif;
        }
        .stDownloadButton>button {
            background-color: #1a1a1a;
            color: white;
            font-family: 'Bebas Neue Pro Expanded', sans-serif;
            letter-spacing: 1px;
        }
    </style>
""", unsafe_allow_html=True)

default_template_path = "default_invoice_template.xlsx"

if os.path.exists(default_template_path):
    default_wb = load_workbook(default_template_path)
    default_ws = default_wb.active
else:
    default_wb = None
    default_ws = None

template_file = st.file_uploader("Upload Invoice Template (.xlsx)", type="xlsx")

from urllib.parse import unquote

query_params = st.query_params
client_name_q = unquote("".join(query_params.get("client", [""])))
billing_address_q = unquote("".join(query_params.get("billing", [""])))
delivery_address_q = unquote("".join(query_params.get("address", [""])))
qty_q = query_params.get("qty", [""])[0]
rate_q = query_params.get("rate", [""])[0]

if template_file or default_wb:
    wb = load_workbook(template_file) if template_file else default_wb
    ws = wb.active

    doc_type = st.selectbox("Document Type", ["Invoice", "Proforma Invoice", "Quotation"])

    st.markdown("## 👤 Client Details")
    client_name = st.text_input("Client Name", value=client_name_q)
    billing_address = st.text_area("Billing Address", value=billing_address_q)
    delivery_address = st.text_area("Delivery Address", value=delivery_address_q)
    invoice_date = st.date_input("Invoice Date", value=datetime.today())

    st.markdown("## 📄 Terms & Conditions")
    term_count = st.number_input("Number of terms", min_value=1, max_value=10, value=1)
    terms = []
    for i in range(term_count):
        terms.append(st.text_area(f"Term {i+1}", key=f"term_{i}"))

    st.markdown("## 📦 Line Items")
    item_count = st.number_input("Number of line items", min_value=1, value=1)
    transport_included = st.radio("Transport Charges", ["Included", "Extra"], index=0)

    items = []
    for i in range(item_count):
        st.markdown(f"### Item {i+1}")
        hsn = st.text_input(f"HSN Code {i+1}", key=f"hsn_{i}")
        desc = st.text_input(f"Description {i+1}", key=f"desc_{i}")
        qty_default = float(qty_q) if i == 0 and qty_q else 0
        qty = st.number_input(f"Quantity {i+1}", key=f"qty_{i}", value=qty_default)
        unit_options = ["RFT", "SQFT", "SQM", "PC", "KG"]
        unit = st.selectbox(f"Unit {i+1}", unit_options, key=f"unit_{i}")
        rate_default = float(rate_q) if i == 0 and rate_q else 0
        rate = st.number_input(f"Rate {i+1}", key=f"rate_{i}", value=rate_default)
        items.append({"hsn": hsn, "desc": desc, "qty": qty, "unit": unit, "rate": rate})

    # Change this cell to match your template's header area

    if st.button("Generate Document"):
        # Optional: Preview HTML version
        html_preview = f"""
        <div style='font-family: Bebas Neue Pro Expanded, sans-serif; padding: 20px;'>
            <h2 style='text-align: center;'>MIRU {doc_type}</h2>
            <hr>
            <p><strong>Client Name:</strong> {client_name}</p>
            <p><strong>Billing Address:</strong><br>{billing_address.replace('\n', '<br>')}</p>
            <p><strong>Delivery Address:</strong><br>{delivery_address.replace('\n', '<br>')}</p>
            <p><strong>Date:</strong> {invoice_date.strftime('%d-%m-%Y')}</p>
            <h4>Line Items:</h4>
            <table style='width:100%; border-collapse: collapse;'>
                <tr><th style='border: 1px solid #000;'>HSN</th><th style='border: 1px solid #000;'>Description</th><th style='border: 1px solid #000;'>Qty</th><th style='border: 1px solid #000;'>Unit</th><th style='border: 1px solid #000;'>Rate</th><th style='border: 1px solid #000;'>Amount</th></tr>
                {''.join(f"<tr><td style='border:1px solid #ccc'>{item['hsn']}</td><td style='border:1px solid #ccc'>{item['desc']}</td><td style='border:1px solid #ccc'>{item['qty']}</td><td style='border:1px solid #ccc'>{item['unit']}</td><td style='border:1px solid #ccc'>{item['rate']}</td><td style='border:1px solid #ccc'>₹{item['qty'] * item['rate']:,.2f}</td></tr>" for item in items)}
            </table>
            <br><h4>Summary:</h4>
            <table style='width:50%; border-collapse: collapse; float: right;'>
                <tr><td style='border:1px solid #ccc'>Subtotal</td><td style='border:1px solid #ccc; text-align:right;'>₹{total:,.2f}</td></tr>
                <tr><td style='border:1px solid #ccc'>CGST @9%</td><td style='border:1px solid #ccc; text-align:right;'>₹{total*0.09:,.2f}</td></tr>
                <tr><td style='border:1px solid #ccc'>SGST @9%</td><td style='border:1px solid #ccc; text-align:right;'>₹{total*0.09:,.2f}</td></tr>
                <tr><td style='border:1px solid #ccc'>Transport</td><td style='border:1px solid #ccc; text-align:right;'>{transport_included}</td></tr>
                <tr><td style='border:1px solid #ccc'><strong>Total</strong></td><td style='border:1px solid #ccc; text-align:right;'><strong>₹{grand_total:,.2f}</strong></td></tr>
            </table>

            <br><h4>Terms & Conditions:</h4>
            {''.join(f"<p>{i+1}. {term}</p>" for i, term in enumerate(terms))}
        </div>
        """
        st.components.v1.html(html_preview, height=700, scrolling=True)
        thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
        font_table = Font(name="Bebas Neue Pro Expanded", color="595959")

        ws["A6"] = client_name
        ws["A6"].font = font_table
        ws["A7"] = billing_address
        ws["A7"].font = font_table
        ws["A14"] = delivery_address
        ws["A14"].font = font_table
        ws["I7"] = invoice_date.strftime('%d-%m-%Y')

        
        start_row = 23
        # Insert terms into sheet
        term_start_row = 34
        for i, term in enumerate(terms):
            row = term_start_row + i
            ws.merge_cells(f"A{row}:E{row}")
            clean_term = term.replace('\n', ' ').strip()
            ws[f"A{row}"] = f"{i+1}. {clean_term}"
            ws[f"A{row}"].alignment = Alignment(wrap_text=False, vertical="top")

        # Add borders to header rows
        for row in [21, 22]:
            for col in ["A", "B", "C", "D", "E", "F", "G", "H", "I"]:
                ws[f"{col}{row}"].border = thin_border

        total = 0
        for i, item in enumerate(items):
            if f"B{start_row + i}:D{start_row + i}" in [str(m) for m in ws.merged_cells.ranges]:
                ws.unmerge_cells(f"B{start_row + i}:D{start_row + i}")
            row = start_row + i
            ws[f"A{row}"] = item["hsn"]
            ws.merge_cells(f"B{row}:D{row}")
            ws[f"B{row}"] = item["desc"].upper()
            ws[f"B{row}"].alignment = Alignment(wrap_text=True, vertical="center")
            # Manually increase row height for longer descriptions
            ws.row_dimensions[row].height = 30 if len(item["desc"]) <= 50 else 45
            ws[f"B{row}"].font = Font(name="Bebas Neue Pro Expanded", color="595959")
            ws[f"F{row}"] = item["qty"]
            ws[f"F{row}"].alignment = Alignment(horizontal="center", vertical="center")
            ws[f"G{row}"] = item["unit"].upper()
            ws[f"G{row}"].alignment = Alignment(horizontal="center", vertical="center")
            ws[f"H{row}"] = item["rate"]
            ws[f"H{row}"].alignment = Alignment(horizontal="center", vertical="center")
            amount = item["qty"] * item["rate"]
            ws[f"I{row}"] = f"₹{amount:,.2f}"
            ws[f"I{row}"].alignment = Alignment(horizontal="right", vertical="center")
            total += amount
            for col in ["A", "B", "C", "D", "F", "G", "H", "I"]:
                ws[f"{col}{row}"].border = thin_border
                ws[f"{col}{row}"].font = font_table

        grand_total = round(total * 1.18)
        summary_labels = ["Subtotal", "CGST @9%", "SGST @9%", "Transport", "Total"]
        summary_values = [f"₹{total:,.2f}", f"₹{total*0.09:,.2f}", f"₹{total*0.09:,.2f}", transport_included, f"₹{grand_total:,}"]
        for i, (label, value) in enumerate(zip(summary_labels, summary_values)):
            row = start_row + len(items) + 1 + i
            ws[f"H{row}"] = label
            ws[f"I{row}"] = value
            ws[f"I{row}"].alignment = Alignment(horizontal="right")

        output = BytesIO()
        wb.save(output)
        output.seek(0)

        # Save to a temporary Excel file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_excel:
            tmp_excel.write(output.read())
            tmp_excel_path = tmp_excel.name

        

        doc_type_prefix = doc_type.replace(" ", "_").lower()
        filename = f"{doc_type_prefix}_{client_name.replace(' ', '_')}.xlsx"
        st.download_button("📥 Download Excel Document", data=output, file_name=filename)

        
