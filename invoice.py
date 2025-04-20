import streamlit as st
from datetime import datetime
from urllib.parse import unquote
import os
import base64
from weasyprint import HTML
from io import BytesIO

# Load and encode logo
def get_base64_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode('utf-8')

logo_path = "Avisa_GRC_Black_290.png"
logo_base64 = get_base64_image(logo_path) if os.path.exists(logo_path) else None

# Streamlit UI
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

# Query params (optional pre-fill)
query_params = st.query_params
client_name_q = unquote("".join(query_params.get("client", [""])))
billing_address_q = unquote("".join(query_params.get("billing", [""])))
delivery_address_q = unquote("".join(query_params.get("address", [""])))
qty_q = query_params.get("qty", [""])[0]
rate_q = query_params.get("rate", [""])[0]

# Input fields
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

# Generate document
if st.button("Generate Document"):
    total = sum(item["qty"] * item["rate"] for item in items)
    grand_total = round(total * 1.18)

    html_template = """
    <!DOCTYPE html>
    <html lang=\"en\">
    <head>
        <meta charset=\"UTF-8\">
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
        <title>Invoice</title>
        <style>
            body { font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px; }
            .container { background: white; padding: 20px; border-radius: 8px; max-width: 800px; margin: auto; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
            .total-section { margin-top: 20px; float: right; width: 50%; }
            .terms { font-size: 0.9em; margin-top: 30px; }
        </style>
    </head>
    <body>
        <div class='container'>
            <div style='text-align:center;'>{{logo}}</div>
            <h2 style='text-align:center;'>{{document_type}}</h2>
            <p><strong>To:</strong> {{recipient_name}}</p>
            <p><strong>Delivery Address:</strong><br>{{delivery_address}}</p>
            <p><strong>Date:</strong> {{invoice_date}}</p>

            <table>
                <thead>
                    <tr>
                        <th>HSN</th>
                        <th>Description</th>
                        <th>QTY</th>
                        <th>Unit</th>
                        <th>Rate</th>
                        <th>Amount</th>
                    </tr>
                </thead>
                <tbody>
                    {{item_rows}}
                </tbody>
            </table>

            <div class='total-section'>
                <table>
                    <tr><th>Subtotal</th><td>₹{{subtotal}}</td></tr>
                    <tr><th>CGST</th><td>₹{{cgst}}</td></tr>
                    <tr><th>SGST</th><td>₹{{sgst}}</td></tr>
                    <tr><th>Transportation</th><td>{{transportation}}</td></tr>
                    <tr><th>Total</th><td><strong>₹{{total_amount}}</strong></td></tr>
                </table>
            </div>

            <div class='terms'>
                <p>1. {{term_1}}</p>
                <p>2. {{term_2}}</p>
                <p>3. Payment Terms: {{payment_terms}}</p>
                <p>4. Actual billing will be done as per the number of pieces supplied.</p>
                <p>5. Labour accommodation shall be provided.</p>
            </div>
        </div>
    </body>
    </html>
    """

    logo_html = f"<img src='data:image/png;base64,{logo_base64}' style='height:80px;'>" if logo_base64 else "<strong>[Logo Missing]</strong>"
    html_filled = html_template
    html_filled = html_filled.replace("{{logo}}", logo_html)
    html_filled = html_filled.replace("{{document_type}}", doc_type)
    html_filled = html_filled.replace("{{recipient_name}}", client_name)
    html_filled = html_filled.replace("{{delivery_address}}", delivery_address.replace("\n", "<br>"))
    html_filled = html_filled.replace("{{invoice_date}}", invoice_date.strftime('%d-%m-%Y'))

        # Construct multiple line items rows
        table_rows = ""
        for item in items:
            amount = item["qty"] * item["rate"]
            table_rows += f"""
            <tr>
                <td>{item['hsn']}</td>
                <td>{item['desc']}</td>
                <td style='text-align:center;'>{item['qty']}</td>
                <td style='text-align:center;'>{item['unit']}</td>
                <td style='text-align:right;'>₹{item['rate']:,.2f}</td>
                <td style='text-align:right;'>₹{amount:,.2f}</td>
            </tr>
            """

        html_filled = html_filled.replace("{{item_rows}}", table_rows)

        html_filled = html_filled.replace("{{subtotal}}", f"{total:,.2f}")
        html_filled = html_filled.replace("{{cgst}}", f"{total*0.09:,.2f}")
        html_filled = html_filled.replace("{{sgst}}", f"{total*0.09:,.2f}")
        html_filled = html_filled.replace("{{transportation}}", transport_included)
        html_filled = html_filled.replace("{{total_amount}}", f"{grand_total:,.2f}")
        html_filled = html_filled.replace("{{term_1}}", terms[0] if len(terms) > 0 else "")
        html_filled = html_filled.replace("{{term_2}}", terms[1] if len(terms) > 1 else "")
        html_filled = html_filled.replace("{{payment_terms}}", terms[2] if len(terms) > 2 else "")

        # Show HTML preview
        st.components.v1.html(html_filled, height=1000, scrolling=True)

        # Generate PDF from HTML using WeasyPrint
        try:
            pdf_bytes = HTML(string=html_filled).write_pdf()
            filename = f"{doc_type}_{client_name.replace(' ', '_')}.pdf"
            st.download_button("\ud83d\udcc5 Download PDF", data=pdf_bytes, file_name=filename)
        except Exception as e:
            st.error(f"PDF generation failed: {e}")
