import streamlit as st
from datetime import datetime
from urllib.parse import unquote
import os
import base64
import pdfkit
import requests
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

    html_path = os.path.join(os.path.dirname(__file__), "pdf.html")
    html_template = open(html_path, "r").read() if os.path.exists(html_path) else "<p><strong>HTML template missing.</strong></p>"
