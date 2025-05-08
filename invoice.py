import streamlit as st
from datetime import datetime
from urllib.parse import unquote
import base64
from weasyprint import HTML
HTML(string=filled_html).write_pdf(pdf_file)
from io import BytesIO

# Load and encode logo
def get_base64_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode('utf-8')

logo_path = "Avisa_GRC_Black_290.png"
import os
logo_base64 = get_base64_image(logo_path) if os.path.exists(logo_path) else None

st.markdown("""
    <h2 style='text-align: center; font-family: Bebas Neue Pro Expanded;'>MIRU Document Generator</h2>
    <hr style='margin-top: 0;'>
    <style>{{
        label, .stTextInput>div>div>input, .stTextArea textarea, .stNumberInput input {{{{
            font-size: 16px;
            font-family: 'Bebas Neue Pro Expanded', sans-serif;
            color: #2E2E2E;
        }}}}}
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
    }}</style>
""", unsafe_allow_html=True)

query_params = st.query_params
client_name_q = unquote("".join(query_params.get("client", [""])))
billing_address_q = unquote("".join(query_params.get("billing", [""])))
delivery_address_q = unquote("".join(query_params.get("address", [""])))
qty_q = query_params.get("qty", [""])[0]
rate_q = query_params.get("rate", [""])[0]

doc_type = st.selectbox("Document Type", ["Invoice", "Proforma Invoice", "Quotation"])
client_name = st.text_input("Client Name", value=client_name_q)
billing_address = st.text_area("Billing Address", value=billing_address_q)
delivery_address = st.text_area("Delivery Address", value=delivery_address_q)
invoice_date = st.date_input("Invoice Date", value=datetime.today())

term_count = st.number_input("Number of terms", min_value=1, max_value=10, value=1)
terms = [st.text_area(f"Term {i+1}", key=f"term_{i}") for i in range(term_count)]

item_count = st.number_input("Number of line items", min_value=1, value=1)
transport_included = st.radio("Transport Charges", ["Included", "Extra"], index=0)

items = []
for i in range(item_count):
    st.markdown(f"### Item {i+1}")
    hsn = st.text_input(f"HSN Code {i+1}", key=f"hsn_{i}")
    desc = st.text_input(f"Description {i+1}", key=f"desc_{i}")
    qty_default = float(qty_q) if i == 0 and qty_q else 0
    qty = st.number_input(f"Quantity {i+1}", key=f"qty_{i}", value=qty_default)
    unit = st.selectbox(f"Unit {i+1}", ["RFT", "SQFT", "SQM", "PC", "KG"], key=f"unit_{i}")
    rate_default = float(rate_q) if i == 0 and rate_q else 0
    rate = st.number_input(f"Rate {i+1}", key=f"rate_{i}", value=rate_default)
    items.append({"hsn": hsn, "desc": desc, "qty": qty, "unit": unit, "rate": rate})

if st.button("Generate PDF"):
    item_rows = "".join([
        f"<tr><td>{item['hsn']}</td><td>{item['desc']}</td><td>{item['qty']}</td><td>{item['unit']}</td><td>₹{item['rate']}</td><td>₹{item['qty'] * item['rate']:,.2f}</td></tr>"
        for item in items
    ])
    total = sum(item["qty"] * item["rate"] for item in items)
    grand_total = round(total * 1.18)

    logo_html = f"<img src='data:image/png;base64,{logo_base64}' style='height:60px;'>" if logo_base64 else "<strong>[Logo Missing]</strong>"

    html_template = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Invoice</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f4f4f4; }}
            .container {{ max-width: 800px; margin: 0 auto; padding: 20px; background-color: #fff; border-radius: 8px; box-shadow: 0 0 10px rgba(0, 0, 0, 0.1); }}
            .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
            .company-logo {{ width: 150px; height: auto; }}
            .company-details {{ text-align: right; }}
            .company-details p {{ margin: 0; }}
            .document-type {{ text-align: right; font-size: 1.2em; margin-bottom: 20px; }}
            .section-title {{ margin-bottom: 5px; font-weight: bold; }}
            .section-content {{ margin-bottom: 20px; }}
            .recipient-date {{ display: flex; justify-content: space-between; margin-bottom: 20px; }}
            table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
            table, th, td {{ border: 1px solid #ccc; }}
            th, td {{ padding: 10px; text-align: left; }}
            .total-section {{ display: flex; justify-content: flex-end; margin-top: 20px; }}
            .total-table {{ width: 50%; border-collapse: collapse; }}
            .total-table th, .total-table td {{ border: 1px solid #ccc; padding: 10px; text-align: right; }}
            .terms, .billing-details {{ font-size: 0.9em; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div>{logo_html}</div>
                <div class="company-details">
                    <p><strong>A Brand of RMT GREEN BUILDERS</strong></p>
                    <p>GST: 08AAJCM6422D1ZN</p>
                    <p>Phone: +91 9310519154 | Mail : contact@mirugrc.com</p>
                </div>
            </div>

            <div class="document-type"><strong>{doc_type}</strong></div>

            <div class="recipient-date">
                <div>
                    <div class="section-title">Recipient</div>
                    <div class="section-content">{client_name}</div>
                </div>
                <div style="text-align: right;">
                    <div class="section-title">Date</div>
                    <div class="section-content">{invoice_date}</div>
                </div>
            </div>

            <div class=\"delivery-info\" style=\"margin-bottom: 40px;\">
                <div class="section-title">Delivery Address</div>
                <div class="section-content">{delivery_address}</div>
            </div>

            <div style=\"margin-top: 40px;\"></div><table>
                <thead>
                    <tr>
                        <th>HSN</th><th>Description</th><th>QTY</th><th>Unit</th><th>Rate</th><th>Amount</th>
                    </tr>
                </thead>
                <tbody>
                    {item_rows}
                </tbody>
            </table>

            <div class="total-section">
                <table class="total-table">
                    <tr><th>Subtotal:</th><td>₹{total:,.2f}</td></tr>
                    <tr><th>CGST:</th><td>₹{total*0.09:,.2f}</td></tr>
                    <tr><th>SGST:</th><td>₹{total*0.09:,.2f}</td></tr>
                    <tr><th>Transportation:</th><td>{transport_included}</td></tr>
                    <tr><th><strong>Total (Round off):</strong></th><td><strong>₹{grand_total:,.2f}</strong></td></tr>
                </table>
            </div>

            <div class="terms">
                <div class="section-title">Terms</div>
                <div class="section-content">
                    <p>1. {terms[0] if len(terms) > 0 else ''}</p>
                    <p>2. {terms[1] if len(terms) > 1 else ''}</p>
                    <p>3. {terms[2] if len(terms) > 2 else ''}</p>
                    <p>3. {terms[3] if len(terms) > 3 else ''}</p>
                    <p>3. {terms[4] if len(terms) > 4 else ''}</p>
                    <p>3. {terms[5] if len(terms) > 5 else ''}</p>
                    <p>3. {terms[6] if len(terms) > 6 else ''}</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    item_rows = "".join([
        f"<tr><td>{item['hsn']}</td><td>{item['desc']}</td><td>{item['qty']}</td><td>{item['unit']}</td><td>₹{item['rate']}</td><td>₹{item['qty'] * item['rate']:,.2f}</td></tr>"
        for item in items
    ])

    filled_html = html_template.replace("{item_rows}", item_rows)

    st.components.v1.html(filled_html, height=800, scrolling=True)
    pdf_file = BytesIO()
    pisa.CreatePDF(filled_html, dest=pdf_file)
    pdf_bytes = pdf_file.getvalue()
    filename = f"{doc_type}_{client_name.replace(' ', '_')}.pdf"
    st.download_button("📥 Download PDF", data=pdf_bytes, file_name=filename)
