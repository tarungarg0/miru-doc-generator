import streamlit as st
from datetime import datetime
from urllib.parse import unquote
import base64
from io import BytesIO
import os
import requests

# ---------- LOGO ----------
def get_base64_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode("utf-8")

logo_path = "MIRU GRC _INDIAS FASTEST GROWING BRAND_Black.png"
logo_base64 = get_base64_image(logo_path) if os.path.exists(logo_path) else None

# ---------- HEADER ----------
st.markdown("""
<h2 style='text-align: center; font-family: Bebas Neue Pro Expanded;'>MIRU Document Generator</h2>
<hr style='margin-top: 0;'>
""", unsafe_allow_html=True)

# ---------- QUERY PARAMS ----------
query_params = st.query_params
client_name_q = unquote("".join(query_params.get("client", [""])))
billing_address_q = unquote("".join(query_params.get("billing", [""])))
delivery_address_q = unquote("".join(query_params.get("address", [""])))
project_name_q = unquote("".join(query_params.get("project", [""])))
qty_q = query_params.get("qty", [""])[0]
rate_q = query_params.get("rate", [""])[0]

# ---------- DOCUMENT TYPE ----------
doc_type = st.selectbox("Document Type", ["Invoice", "Proforma Invoice", "Quotation"])

# ---------- COMPANY DETAILS (NEW – EDITABLE) ----------
st.subheader("Company Details")

company_name = st.text_input("Company Name", value="MIXD STUDIO BY RMT")
company_gst = st.text_input("GST Number", value="07ACDFM6440P1ZS")
company_phone = st.text_input("Phone Number", value="+91 9310519154")
company_email = st.text_input("Email", value="contact@mirugrc.com")

# ---------- CLIENT DETAILS ----------
st.subheader("Client Details")

project_name = st.text_input("Project Name", value=project_name_q)
client_name = st.text_input("Client Name", value=client_name_q)
billing_address = st.text_area("Billing Address", value=billing_address_q)
delivery_address = st.text_area("Delivery Address", value=delivery_address_q)
invoice_date = st.date_input("Invoice Date", value=datetime.today())

# ---------- TERMS ----------
terms_templates = {
    "Standard": [
        "Prices are exclusive of GST.",
        "Material will be delivered within 10–15 working days.",
        "Payment within 10 days of delivery.",
        "Actual billing will be done as per the number of pieces supplied.",
        "Labour accommodation shall be provided by client."
    ]
}

template_choice = st.selectbox("Select Terms Template", list(terms_templates.keys()))
initial_terms = terms_templates[template_choice]

term_count = st.number_input("Number of terms", min_value=1, max_value=10, value=len(initial_terms))
terms = []
for i in range(term_count):
    default = initial_terms[i] if i < len(initial_terms) else ""
    terms.append(st.text_area(f"Term {i+1}", value=default, key=f"term_{i}"))

# ---------- ITEMS ----------
item_count = st.number_input("Number of line items", min_value=1, value=1)
transport_included = st.radio("Transport Charges", ["Included", "Extra"], index=0)

items = []
for i in range(item_count):
    st.markdown(f"### Item {i+1}")
    hsn = st.selectbox(f"HSN Code {i+1}", ["68109990", "68109100", "69072100"], key=f"hsn_{i}")
    desc = st.text_input(f"Description {i+1}", key=f"desc_{i}")
    qty = st.number_input(f"Quantity {i+1}", value=float(qty_q) if i == 0 and qty_q else 0.0)
    unit = st.selectbox(f"Unit {i+1}", ["RFT", "SQFT", "SQM", "PC", "KG"])
    rate = st.number_input(f"Rate {i+1}", value=float(rate_q) if i == 0 and rate_q else 0.0)

    items.append({
        "hsn": hsn,
        "desc": desc,
        "qty": qty,
        "unit": unit,
        "rate": rate
    })

# ---------- INR FORMAT ----------
def format_inr(amount):
    amount = float(amount)
    s = f"{amount:,.2f}"
    i, d = s.split(".")
    i = i.replace(",", "")
    if len(i) > 3:
        i = ",".join([i[:-3][::-1][i:i+2] for i in range(0, len(i[:-3]), 2)][::-1]) + "," + i[-3:]
    return f"{i}.{d}"

# ---------- GENERATE ----------
if st.button("Generate PDF"):
    item_rows = "".join([
        f"<tr><td>{i['hsn']}</td><td>{i['desc']}</td><td>{i['qty']}</td><td>{i['unit']}</td><td>₹{format_inr(i['rate'])}</td><td>₹{format_inr(i['qty']*i['rate'])}</td></tr>"
        for i in items
    ])

    total = sum(i["qty"] * i["rate"] for i in items)
    gst = total * 0.09
    grand_total = round(total + gst * 2)

    logo_html = f"<img src='data:image/png;base64,{logo_base64}' style='height:50px;'>" if logo_base64 else ""

    html_template = f"""
<!DOCTYPE html>
<html>
<head>
<style>
@page {{ margin: 10mm 20mm; }}
body {{ font-family: Poppins, sans-serif; }}
table {{ width:100%; border-collapse: collapse; }}
th, td {{ border:1px solid #ccc; padding:8px; }}
th {{ font-size:14px; }}
td {{ font-size:12px; }}
</style>
</head>
<body>

<div style="display:flex; justify-content:space-between;">
  <div>{logo_html}</div>
  <div style="text-align:right;">
    <strong style="font-size:24px;">{company_name}</strong><br>
    GST: {company_gst}<br>
    Phone: {company_phone}<br>
    Email: {company_email}
  </div>
</div>

<h2 style="text-align:right;">{doc_type}</h2>

<b>Recipient</b><br>
{project_name}<br>
{client_name}<br>
{billing_address}<br><br>

<b>Delivery Address</b><br>
{delivery_address}<br><br>

<table>
<tr><th>HSN</th><th>Description</th><th>Qty</th><th>Unit</th><th>Rate</th><th>Amount</th></tr>
{item_rows}
</table>

<table style="width:40%; float:right;">
<tr><th>Subtotal</th><td>₹{format_inr(total)}</td></tr>
<tr><th>CGST</th><td>₹{format_inr(gst)}</td></tr>
<tr><th>SGST</th><td>₹{format_inr(gst)}</td></tr>
<tr><th>Total</th><td><b>₹{format_inr(grand_total)}</b></td></tr>
</table>

<div style="margin-top:120px;">
<b>Terms</b>
{"".join([f"<p>{i+1}. {t}</p>" for i, t in enumerate(terms)])}
</div>

</body>
</html>
"""

    st.components.v1.html(html_template, height=1000, scrolling=True)

    response = requests.post(
        "https://api.pdfshift.io/v3/convert/pdf",
        headers={
            "X-API-Key": "sk_b043ae1f2d6f66581b3d6ccce3884a0f750967e3",
            "Content-Type": "application/json"
        },
        json={"source": html_template}
    )

    st.download_button(
        "📥 Download PDF",
        data=response.content,
        file_name=f"{doc_type}_{client_name.replace(' ', '_')}.pdf"
    )
