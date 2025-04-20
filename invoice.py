import streamlit as st
from datetime import datetime
from urllib.parse import unquote
import base64
from xhtml2pdf import pisa
from io import BytesIO

# Load and encode logo
def get_base64_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode('utf-8')

logo_path = "Avisa_GRC_Black_290.png"
logo_base64 = get_base64_image(logo_path) if os.path.exists(logo_path) else None

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
    total = sum(item["qty"] * item["rate"] for item in items)
    grand_total = round(total * 1.18)

    logo_html = f"<img src='data:image/png;base64,{logo_base64}' style='height:80px;'>" if logo_base64 else "<strong>[Logo Missing]</strong>"

    html_template = f"""
    <html>
    <head><style>
        body {{ font-family: Arial; margin: 20px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ border: 1px solid #ccc; padding: 8px; }}
        th {{ background-color: #f0f0f0; }}
    </style></head>
    <body>
        <div>{logo_html}</div>
        <h2>{doc_type}</h2>
        <p><strong>Client Name:</strong> {client_name}</p>
        <p><strong>Delivery Address:</strong><br>{delivery_address.replace("\n", "<br>")}</p>
        <p><strong>Invoice Date:</strong> {invoice_date.strftime('%d-%m-%Y')}</p>

        <table>
            <tr><th>HSN</th><th>Description</th><th>QTY</th><th>Unit</th><th>Rate</th><th>Amount</th></tr>
            {{item_rows}}
        </table>

        <p><strong>Subtotal:</strong> ₹{total:,.2f}</p>
        <p><strong>CGST:</strong> ₹{total*0.09:,.2f}</p>
        <p><strong>SGST:</strong> ₹{total*0.09:,.2f}</p>
        <p><strong>Transportation:</strong> {transport_included}</p>
        <p><strong>Total:</strong> ₹{grand_total:,.2f}</p>

        <h4>Terms & Conditions</h4>
        <ul>
            <li>{terms[0] if len(terms) > 0 else ''}</li>
            <li>{terms[1] if len(terms) > 1 else ''}</li>
            <li>Payment Terms: {terms[2] if len(terms) > 2 else ''}</li>
        </ul>
    </body></html>
    """

    item_rows = "".join([
        f"<tr><td>{item['hsn']}</td><td>{item['desc']}</td><td>{item['qty']}</td><td>{item['unit']}</td><td>₹{item['rate']}</td><td>₹{item['qty'] * item['rate']:,.2f}</td></tr>"
        for item in items
    ])

    filled_html = html_template.replace("{{item_rows}}", item_rows)

    st.components.v1.html(filled_html, height=800, scrolling=True)
    pdf_file = BytesIO()
pisa.CreatePDF(filled_html, dest=pdf_file)
pdf_bytes = pdf_file.getvalue()
    filename = f"{doc_type}_{client_name.replace(' ', '_')}.pdf"
    st.download_button("📥 Download PDF", data=pdf_bytes, file_name=filename)
