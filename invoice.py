import streamlit as st
from datetime import datetime
from urllib.parse import unquote
import base64
from io import BytesIO
import os

# Load and encode logo
def get_base64_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode('utf-8')

logo_path = "MIRU GRC _INDIAS FASTEST GROWING BRAND_grAD _350.png"
logo_base64 = get_base64_image(logo_path) if os.path.exists(logo_path) else None

st.markdown("""
    <h2 style='text-align: center; font-family: Bebas Neue Pro Expanded;'>MIRU Document Generator</h2>
    <hr style='margin-top: 0;'>
    <style>{
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
    }</style>
""", unsafe_allow_html=True)

query_params = st.query_params
client_name_q = unquote("".join(query_params.get("client", [""])))
billing_address_q = unquote("".join(query_params.get("billing", [""])))
delivery_address_q = unquote("".join(query_params.get("address", [""])))
project_name_q = unquote("".join(query_params.get("project", [""])))
qty_q = query_params.get("qty", [""])[0]
rate_q = query_params.get("rate", [""])[0]

doc_type = st.selectbox("Document Type", ["Invoice", "Proforma Invoice", "Quotation"])
project_name = st.text_input("Project Name", value=project_name_q)
client_name = st.text_input("Client Name", value=client_name_q)
billing_address = st.text_area("Billing Address", value=billing_address_q)
delivery_address = st.text_area("Delivery Address", value=delivery_address_q)
invoice_date = st.date_input("Invoice Date", value=datetime.today())

terms_templates = {
    "Standard": [
        "Prices are exclusive of GST.",
        "Material will be delivered within 10-15 working days.",
        "Payment within 10 days of delivery.",
        "Actual billing will be done as per the number of pieces supplied.",
        "Labour accommodation shall be provided by client."
    ],
    "Quick Delivery": [
        "All materials in stock. Delivery within 5 days.",
        "Immediate invoicing after dispatch.",
        "Payment within 3 days of delivery."
    ]
}

template_choice = st.selectbox("Select Terms Template", list(terms_templates.keys()))
initial_terms = terms_templates[template_choice]

# Option to save edited terms as a new template
save_new = st.checkbox("Save these terms as a new template")
new_template_name = ""
if save_new:
    new_template_name = st.text_input("Enter a name for the new template")

term_count = st.number_input("Number of terms", min_value=1, max_value=10, value=len(initial_terms))
terms = []  # editable terms list
for i in range(term_count):
    default = initial_terms[i] if i < len(initial_terms) else ""
    term_text = st.text_area(f"Term {i+1}", value=default, key=f"term_{i}")
    terms.append(term_text)

# Save new template if applicable
if save_new and new_template_name.strip() and st.button("Save Template"):
    terms_templates[new_template_name.strip()] = terms
    st.success(f"Template '{new_template_name.strip()}' saved successfully!")

item_count = st.number_input("Number of line items", min_value=1, value=1)
transport_included = st.radio("Transport Charges", ["Included", "Extra"], index=0)

items = []
for i in range(item_count):
    st.markdown(f"### Item {i+1}")
    hsn_choice = st.selectbox(f"HSN Code {i+1}", ["68109990", "68109100", "69072100", "Other"], key=f"hsn_choice_{i}")
    hsn = st.text_input(f"Enter HSN Code {i+1}" if hsn_choice == "Other" else "", value="" if hsn_choice == "Other" else hsn_choice, key=f"hsn_{i}")
    desc = st.text_input(f"Description {i+1}", key=f"desc_{i}")
    qty_default = float(qty_q) if i == 0 and qty_q else 0
    qty = st.number_input(f"Quantity {i+1}", key=f"qty_{i}", value=qty_default)
    unit = st.selectbox(f"Unit {i+1}", ["RFT", "SQFT", "SQM", "PC", "KG"], key=f"unit_{i}")
    rate_default = float(rate_q) if i == 0 and rate_q else 0
    rate = st.number_input(f"Rate {i+1}", key=f"rate_{i}", value=rate_default)
    items.append({"hsn": hsn, "desc": desc, "qty": qty, "unit": unit, "rate": rate})
    
def format_inr(amount):
    try:
        amount = float(amount)
        s = f"{amount:,.2f}"
        parts = s.split(".")
        integer = parts[0].replace(",", "")
        decimal = parts[1]
        if len(integer) > 3:
            last3 = integer[-3:]
            rest = integer[:-3]
            rest = ",".join([rest[max(i - 2, 0):i] for i in range(len(rest), 0, -2)][::-1])
            return f"{rest},{last3}.{decimal}"
        else:
            return f"{integer}.{decimal}"
    except:
        return amount
        
if st.button("Generate PDF"):
    import streamlit.components.v1 as components
    
    item_rows = "".join([
        f"<tr><td>{item['hsn']}</td><td>{item['desc']}</td><td>{item['qty']}</td><td>{item['unit']}</td><td>₹{format_inr(item['rate'])}</td><td>₹{format_inr(item['qty'] * item['rate'])}</td></tr>"
        for item in items
    ])
    total = sum(item["qty"] * item["rate"] for item in items)
    grand_total = round(total * 1.18)

    logo_html = f"<img src='data:image/png;base64,{logo_base64}' style='height:60px;'>" if logo_base64 else "<strong style=\"font-family: 'Bebas Neue', sans-serif;\">[Logo Missing]</strong>"

    html_template = f"""
    <!DOCTYPE html>
    <html lang=\"en\">
    <head>
        <link href='https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600&display=swap' rel='stylesheet'>
        <meta charset=\"UTF-8\">
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
        <title>Invoice</title>
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&display=swap');

            body {{ font-family: 'Poppins', sans-serif; margin: 10mm 20mm 20mm 20mm; background-color: #fff; }}
            .container {{ width: 100%; padding: 20px; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }}
            .company-details p {{ margin: 0; }}
            .document-type {{ text-align: right; font-size: 1.2em; margin-bottom: 20px; }}
            .section-title {{ margin-bottom: 5px; font-weight: bold; }}
            .section-content {{ margin-bottom: 20px; }}
            .recipient-date {{ display: flex; justify-content: space-between; margin-bottom: 20px; }}
            table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
            table {{ border: 1px solid #ccc; }}
th {{ border: 1px solid #ccc; font-size: 14px; }}
td {{ border: 1px solid #ccc; font-size: 12px; }}
            th, td {{ padding: 10px; text-align: left; }}
            .total-section {{ display: flex; justify-content: flex-end; margin-top: 20px; }}
            .total-table {{ width: 50%; border-collapse: collapse; }}
            .total-table th, .total-table td {{ border: 1px solid #ccc; padding: 10px; text-align: right; font-size: 11px; }}
          .terms {{ 
    font-size: 11px;
    margin-top: 10px;
    page-break-inside: avoid;
    padding-bottom: 20mm;
}}
        </style>
    </head>
    <body>
        <div class=\"container\">
            <div class=\"header\" style=\"margin-bottom: 40px;\">
                <div>{logo_html}</div>
                <div class=\"company-details\" style=\"text-align: right;\">
                    <p><strong style=\"font-family: 'Bebas Neue', sans-serif; font-size: 24px;\">MIXD STUDIO BY RMT</strong></p>
                    <p style=\"font-size: 13px;\">GST: 07ACDFM6440P1ZS</p>
                    <p style=\"font-size: 13px;\">Phone: +91 9310519154 </p>
                    <p style=\"font-size: 13px;\">Mail : contact@mirugrc.com </p>
                </div>
            </div>
            <div class=\"document-type\" style=\"margin-top: 60px; font-size: 24px;\"><strong>{doc_type}</strong></div>
            <div class=\"recipient-date\" style=\"margin-bottom: 40px;\">
                <div>
                    <div class=\"section-title\" style=\"font-family: 'Bebas Neue', sans-serif;\">RECIPIENT</div>
                    <div class=\"section-content\" style=\"font-size: 13px; word-wrap: break-word; word-break: break-word; white-space: pre-wrap; max-width: 300px;\">{project_name}<br>{client_name}<br>{billing_address}</div>
                </div>
                <div style=\"text-align: right;\">
                    <div class=\"section-title\" style=\"font-family: 'Bebas Neue', sans-serif;\">DATE</div>
                    <div class=\"section-content\">{invoice_date}</div>
                </div>
            </div>
            <div class=\"delivery-info\" style=\"margin-bottom: 60px;\">
                <div class=\"section-title\" style=\"font-family: 'Bebas Neue', sans-serif;\">DELIVERY ADDRESS</div>
                <div class=\"section-content\" style=\"font-size: 13px; word-wrap: break-word; word-break: break-word; white-space: pre-wrap; max-width: 300px;\">{delivery_address}</div>
            </div>
            <table>
                <thead>
                    <tr><th style=\"font-family: 'Bebas Neue', sans-serif;\">HSN</th><th>DESCRIPTION</th><th>QTY</th><th>UNIT</th><th>RATE</th><th>AMOUNT</th></tr>
                </thead>
                <tbody>{item_rows}</tbody>
            </table>
            <div class=\"total-section\">
                <table class=\"total-table\">
                    <tr><th>Subtotal:</th><td>₹{format_inr(total)}</td></tr>
                    <tr><th>CGST:</th><td>₹{format_inr(total*0.09)}</td></tr>
                    <tr><th>SGST:</th><td>₹{format_inr(total*0.09)}</td></tr>
                    <tr><th>Transportation:</th><td>{transport_included}</td></tr>
                    <tr><th><strong>Total (Round off):</strong></th><td><strong>₹{format_inr(grand_total)}</strong></td></tr>
                </table>
            </div>
            <div class=\"terms\" style=\"margin-top: 100px; font-size: 11px;\">
                <div class=\"section-title\">Terms</div>
                <div class=\"section-content\">
                    {''.join([f'<p>{i+1}. {t}</p>' for i, t in enumerate(terms)])}
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    import requests
    import streamlit.components.v1 as components
    components.html(html_template, height=1000, scrolling=True)  # Live HTML preview  # Live preview

    response = requests.post(
        "https://api.pdfshift.io/v3/convert/pdf",
        headers={
            "X-API-Key": "sk_b043ae1f2d6f66581b3d6ccce3884a0f750967e3",
            "Content-Type": "application/json"
        },
        json={
            "source": html_template,
            "sandbox": False
        }
    )
    response.raise_for_status()
    pdf_bytes = response.content
    filename = f"{doc_type}_{client_name.replace(' ', '_')}.pdf"
    st.download_button("📥 Download PDF", data=pdf_bytes, file_name=filename)
