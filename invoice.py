import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta
import json
import base64
import hashlib
import os
import requests
from urllib.parse import unquote, quote

st.set_page_config(page_title="MIRU Document Generator", layout="wide")

# ── Google Sheets ──────────────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

@st.cache_resource
def get_gc():
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]), scopes=SCOPES
    )
    return gspread.authorize(creds)

@st.cache_resource
def get_sheet():
    return get_gc().open_by_key(st.secrets["app"]["sheet_id"])

DOC_HEADERS = [
    "doc_id", "doc_type", "status", "project_name", "client_name",
    "billing_address", "delivery_address", "doc_date", "validity_date",
    "transport", "transport_amount", "items_json", "terms_json", "created_at",
    "approval_token", "approved_by", "approved_at", "signature_b64", "notes", "doc_code",
]
TEMPLATE_HEADERS  = ["name", "terms_json"]
MANAGER_HEADERS   = ["name", "whatsapp", "pin", "signature_b64"]
CLIENT_HEADERS    = ["name", "billing_address", "delivery_address", "gst_number", "payment_terms", "notes"]
ITEM_HEADERS      = ["item_code", "description", "unit", "base_rate", "category", "sale_types", "supply_rate", "installation_rate"]

@st.cache_resource
def ensure_sheets():
    sh = get_sheet()
    existing = [ws.title for ws in sh.worksheets()]

    if "Documents" not in existing:
        ws = sh.add_worksheet("Documents", 1000, len(DOC_HEADERS))
        ws.update("A1", [DOC_HEADERS])
    else:
        # Fix headers if they are outdated or have empty columns
        ws = sh.worksheet("Documents")
        ws.update("A1", [DOC_HEADERS])

    if "Terms_Templates" not in existing:
        ws = sh.add_worksheet("Terms_Templates", 100, 2)
        ws.update("A1", [TEMPLATE_HEADERS])
        ws.append_row(["Standard", json.dumps([
            "Prices are exclusive of GST.",
            "Material will be delivered within 10-15 working days.",
            "Payment within 10 days of delivery.",
            "Actual billing will be done as per the number of pieces supplied.",
            "Labour accommodation shall be provided by client.",
        ])])
        ws.append_row(["Quick Delivery", json.dumps([
            "All materials in stock. Delivery within 5 days.",
            "Immediate invoicing after dispatch.",
            "Payment within 3 days of delivery.",
        ])])
    else:
        sh.worksheet("Terms_Templates").update("A1", [TEMPLATE_HEADERS])

    if "Managers" not in existing:
        ws = sh.add_worksheet("Managers", 20, 4)
        ws.update("A1", [MANAGER_HEADERS])
    else:
        sh.worksheet("Managers").update("A1", [MANAGER_HEADERS])

    if "Clients" not in existing:
        ws = sh.add_worksheet("Clients", 500, 6)
        ws.update("A1", [CLIENT_HEADERS])
    else:
        sh.worksheet("Clients").update("A1", [CLIENT_HEADERS])

    if "Items" not in existing:
        ws = sh.add_worksheet("Items", 500, len(ITEM_HEADERS))
        ws.update("A1", [ITEM_HEADERS])
    else:
        ws = sh.worksheet("Items")
        # Resize if needed (Items was originally created with 5 cols)
        if ws.col_count < len(ITEM_HEADERS):
            ws.resize(cols=len(ITEM_HEADERS))
        ws.update("A1", [ITEM_HEADERS])

    if "Work_Orders" not in existing:
        ws = sh.add_worksheet("Work_Orders", 500, len(WO_HEADERS))
        ws.update("A1", [WO_HEADERS])
    else:
        ws = sh.worksheet("Work_Orders")
        if ws.col_count < len(WO_HEADERS):
            ws.resize(cols=len(WO_HEADERS))
        ws.update("A1", [WO_HEADERS])

# ── Helpers ────────────────────────────────────────────────────────────────────

def generate_doc_id(doc_type, code=""):
    records = _fetch_documents()
    prefix = "QT" if doc_type == "Quotation" else "PI"
    year = datetime.now().strftime("%Y")
    code_part = f"-{code.strip().upper()}" if code.strip() else ""
    base = f"{prefix}-{year}{code_part}-"
    count = sum(1 for r in records if str(r.get("doc_id", "")).startswith(base)) + 1
    return f"{base}{count:03d}"

def make_token(doc_id):
    secret = st.secrets["app"]["approval_secret"]
    return hashlib.sha256(f"{doc_id}{secret}".encode()).hexdigest()[:16]

def format_inr(amount):
    try:
        amount = float(amount)
        integer = str(int(amount))
        decimal = f"{amount:.2f}".split(".")[1]
        if len(integer) > 3:
            last3 = integer[-3:]
            rest = integer[:-3]
            groups = []
            while rest:
                groups.insert(0, rest[-2:])
                rest = rest[:-2]
            return ",".join(groups) + "," + last3 + "." + decimal
        return integer + "." + decimal
    except Exception:
        return str(amount)

def amount_in_words(n):
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
            "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
            "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty",
            "Sixty", "Seventy", "Eighty", "Ninety"]

    def two(x):
        return ones[x] if x < 20 else tens[x // 10] + (" " + ones[x % 10] if x % 10 else "")

    def three(x):
        return (ones[x // 100] + " Hundred" + (" " + two(x % 100) if x % 100 else "")) if x >= 100 else two(x)

    n = int(n)
    if n == 0:
        return "Zero Only"
    parts = []
    for label, div in [("Crore", 10_000_000), ("Lakh", 100_000), ("Thousand", 1_000)]:
        if n >= div:
            parts.append(three(n // div) + " " + label)
            n %= div
    if n:
        parts.append(three(n))
    return " ".join(parts) + " Only"

def img_b64(path):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return None

# ── Sheets CRUD ────────────────────────────────────────────────────────────────

def save_document(data, edit_id=None):
    sh = get_sheet()
    ws = sh.worksheet("Documents")
    records = _fetch_documents()
    token = make_token(data["doc_id"])
    row = [
        data["doc_id"], data["doc_type"], data.get("status", "Draft"),
        data["project_name"], data["client_name"],
        data["billing_address"], data["delivery_address"],
        str(data["doc_date"]), str(data.get("validity_date", "")),
        data["transport"], float(data.get("transport_amount", 0) or 0),
        json.dumps(data["items"]), json.dumps(data["terms"]),
        data.get("created_at", datetime.now().isoformat()),
        token, "", "", "", data.get("notes", ""), data.get("doc_code", ""),
    ]
    if edit_id:
        for i, r in enumerate(records):
            if r["doc_id"] == edit_id:
                ws.update(f"A{i+2}:T{i+2}", [row])
                _bust()
                return data["doc_id"]
    ws.append_row(row)
    _bust()
    return data["doc_id"]

@st.cache_data(ttl=60)
def _fetch_documents():
    return get_sheet().worksheet("Documents").get_all_records(expected_headers=DOC_HEADERS)

@st.cache_data(ttl=60)
def _fetch_templates():
    return get_sheet().worksheet("Terms_Templates").get_all_records(expected_headers=TEMPLATE_HEADERS)

@st.cache_data(ttl=60)
def _fetch_managers():
    return get_sheet().worksheet("Managers").get_all_records(expected_headers=MANAGER_HEADERS)

@st.cache_data(ttl=60)
def _fetch_clients():
    return get_sheet().worksheet("Clients").get_all_records(expected_headers=CLIENT_HEADERS)

@st.cache_data(ttl=60)
def _fetch_items():
    return get_sheet().worksheet("Items").get_all_records(expected_headers=ITEM_HEADERS)

@st.cache_data(ttl=60)
def _fetch_work_orders():
    return get_sheet().worksheet("Work_Orders").get_all_records(expected_headers=WO_HEADERS)

def _bust():
    """Clear all data caches after any write."""
    _fetch_documents.clear()
    _fetch_templates.clear()
    _fetch_managers.clear()
    _fetch_clients.clear()
    _fetch_items.clear()
    _fetch_work_orders.clear()

def get_document(doc_id):
    for r in _fetch_documents():
        if r["doc_id"] == doc_id:
            r = dict(r)
            try:
                r["items"] = json.loads(r["items_json"])
            except Exception:
                r["items"] = []
            try:
                r["terms"] = json.loads(r["terms_json"])
            except Exception:
                r["terms"] = []
            return r
    return None

def all_documents():
    return _fetch_documents()

def get_templates():
    return {r["name"]: json.loads(r["terms_json"]) for r in _fetch_templates() if r.get("name")}

def save_template(name, terms):
    ws = get_sheet().worksheet("Terms_Templates")
    for i, r in enumerate(_fetch_templates()):
        if r["name"] == name:
            ws.update(f"A{i+2}:B{i+2}", [[name, json.dumps(terms)]])
            _bust()
            return
    ws.append_row([name, json.dumps(terms)])
    _bust()

def get_managers():
    return _fetch_managers()

def get_clients():
    return _fetch_clients()

def save_client(data, edit_idx=None):
    ws = get_sheet().worksheet("Clients")
    row = [data["name"], data["billing_address"], data["delivery_address"],
           data["gst_number"], data["payment_terms"], data["notes"]]
    if edit_idx is not None:
        ws.update(f"A{edit_idx+2}:F{edit_idx+2}", [row])
    else:
        ws.append_row(row)
    _bust()

def get_items():
    return _fetch_items()

def save_item(data, edit_idx=None):
    ws = get_sheet().worksheet("Items")
    row = [data["item_code"], data["description"], data["unit"],
           float(data.get("base_rate", 0)), data["category"],
           json.dumps(data.get("sale_types", ["Supply", "Installation"])),
           float(data.get("supply_rate", data.get("base_rate", 0))),
           float(data.get("installation_rate", 0))]
    if edit_idx is not None:
        ws.update(f"A{edit_idx+2}:H{edit_idx+2}", [row])
    else:
        ws.append_row(row)
    _bust()

# ── Work Orders ────────────────────────────────────────────────────────────────

WO_HEADERS = ["wo_id", "client_name", "project_name", "scope",
              "items_json", "milestones_json", "terms_json", "created_at", "status"]

def get_work_orders():
    records = _fetch_work_orders()
    result  = []
    for r in records:
        r = dict(r)
        try:
            r["items"]      = json.loads(r["items_json"])
        except Exception:
            r["items"]      = []
        try:
            r["milestones"] = json.loads(r["milestones_json"])
        except Exception:
            r["milestones"] = []
        try:
            r["terms"] = json.loads(r.get("terms_json") or "[]")
        except Exception:
            r["terms"] = []
        result.append(r)
    return result

def generate_wo_id():
    year  = datetime.now().strftime("%Y")
    count = sum(1 for r in _fetch_work_orders() if str(r.get("wo_id","")).startswith(f"WO-{year}-")) + 1
    return f"WO-{year}-{count:03d}"

def save_work_order(data, edit_id=None):
    ws  = get_sheet().worksheet("Work_Orders")
    row = [
        data["wo_id"], data["client_name"], data["project_name"], data["scope"],
        json.dumps(data["items"]), json.dumps(data["milestones"]),
        json.dumps(data.get("terms", [])),
        data.get("created_at", datetime.now().isoformat()), data.get("status", "Active"),
    ]
    if edit_id:
        for i, r in enumerate(_fetch_work_orders()):
            if r["wo_id"] == edit_id:
                ws.update(f"A{i+2}:I{i+2}", [row])
                _bust()
                return
    ws.append_row(row)
    _bust()

def update_wo_milestone(wo_id, milestone_idx, new_status):
    ws = get_sheet().worksheet("Work_Orders")
    for i, r in enumerate(_fetch_work_orders()):
        if r["wo_id"] == wo_id:
            milestones = json.loads(r["milestones_json"])
            milestones[milestone_idx]["status"] = new_status
            ws.update(f"F{i+2}", [[json.dumps(milestones)]])
            _bust()
            return

def approve_doc(doc_id, manager_name, signature_b64):
    ws = get_sheet().worksheet("Documents")
    for i, r in enumerate(_fetch_documents()):
        if r["doc_id"] == doc_id:
            n = i + 2
            ws.update(f"C{n}", [["Approved"]])
            ws.update(f"O{n}", [[manager_name]])
            ws.update(f"P{n}", [[datetime.now().isoformat()]])
            if signature_b64:
                ws.update(f"Q{n}", [[signature_b64]])
            _bust()
            return True
    return False

def update_status(doc_id, status):
    ws = get_sheet().worksheet("Documents")
    for i, r in enumerate(_fetch_documents()):
        if r["doc_id"] == doc_id:
            ws.update(f"C{i+2}", [[status]])
            _bust()
            return

# ── PDF builder ────────────────────────────────────────────────────────────────

def build_html(data, signature_b64=None, watermark=False):
    logo_b64 = img_b64("MIRU GRC _INDIAS FASTEST GROWING BRAND_Black.png")
    logo_html = (f"<img src='data:image/png;base64,{logo_b64}' style='height:50px;'>"
                 if logo_b64 else "<strong>MIXD STUDIO BY RMT</strong>")

    items = data.get("items") or json.loads(data.get("items_json", "[]"))
    terms = data.get("terms") or json.loads(data.get("terms_json", "[]"))

    def _item_rows(items):
        html = ""
        for it in items:
            qty = float(it.get("qty", 0))
            if it.get("sale_type") == "Supply & Installation" and (it.get("supply_rate") or it.get("install_rate")):
                sr = float(it.get("supply_rate") or 0)
                ir = float(it.get("install_rate") or 0)
                if sr:
                    html += (
                        f"<tr><td>Supply</td><td>{it.get('hsn','')}</td><td>Supply of {it['desc']}</td>"
                        f"<td>{qty}</td><td>{it.get('unit','')}</td><td>₹{format_inr(sr)}</td>"
                        f"<td>₹{format_inr(qty * sr)}</td></tr>"
                    )
                if ir:
                    html += (
                        f"<tr><td>Installation</td><td>{it.get('hsn','')}</td><td>Installation of {it['desc']}</td>"
                        f"<td>{qty}</td><td>{it.get('unit','')}</td><td>₹{format_inr(ir)}</td>"
                        f"<td>₹{format_inr(qty * ir)}</td></tr>"
                    )
            else:
                rate = float(it.get("rate", 0))
                html += (
                    f"<tr><td>{it.get('sale_type','')}</td><td>{it.get('hsn','')}</td><td>{it['desc']}</td>"
                    f"<td>{qty}</td><td>{it.get('unit','')}</td><td>₹{format_inr(rate)}</td>"
                    f"<td>₹{format_inr(qty * rate)}</td></tr>"
                )
        return html

    rows = _item_rows(items)

    def _item_subtotal(items):
        total = 0.0
        for it in items:
            qty = float(it.get("qty", 0))
            if it.get("sale_type") == "Supply & Installation" and (it.get("supply_rate") or it.get("install_rate")):
                total += qty * float(it.get("supply_rate") or 0)
                total += qty * float(it.get("install_rate") or 0)
            else:
                total += qty * float(it.get("rate", 0))
        return total

    subtotal = _item_subtotal(items)
    transport_amount = float(data.get("transport_amount", 0) or 0)
    grand = round(subtotal * 1.18 + transport_amount)

    validity_html = ""
    if data.get("validity_date") and data.get("doc_type") == "Quotation":
        validity_html = f"<p style='font-size:11px;margin:2px 0'><b>Valid Until:</b> {data['validity_date']}</p>"

    sig_html = ""
    if signature_b64:
        sig_html = f"""
        <div style='margin-top:50px;text-align:right;'>
            <img src='data:image/png;base64,{signature_b64}' style='height:60px;'><br>
            <small style='font-size:10px;'>Authorised Signatory: {data.get('approved_by','')}</small>
        </div>"""

    terms_html = "".join(f"<p style='margin:3px 0'>{i+1}. {t}</p>" for i, t in enumerate(terms))

    return f"""<!DOCTYPE html>
<html><head>
<link href='https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600&display=swap' rel='stylesheet'>
<meta charset='UTF-8'>
<style>
@page {{margin:10mm 20mm 20mm 10mm;}}
body{{font-family:'Poppins',sans-serif;background:#fff;font-size:13px;}}
table{{width:100%;border-collapse:collapse;margin-bottom:16px;border:1px solid #ccc;}}
th,td{{border:1px solid #ccc;padding:8px;text-align:left;}}
th{{font-size:12px;background:#f5f5f5;}}
td{{font-size:11px;}}
.tot th,.tot td{{border:1px solid #ccc;padding:8px;text-align:right;font-size:11px;}}
.watermark{{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%) rotate(-45deg);
  font-size:90px;font-weight:900;color:rgba(255,0,0,0.12);white-space:nowrap;
  pointer-events:none;z-index:9999;letter-spacing:8px;}}
</style>
</head><body>
<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:30px;'>
  <div>{logo_html}</div>
  <div style='text-align:right;'>
    <p style='margin:0;font-size:20px;font-weight:600;'>MIXD STUDIO BY RMT</p>
    <p style='margin:2px 0;font-size:11px;'>GST: 07ACDFM6440P1ZS</p>
    <p style='margin:2px 0;font-size:11px;'>Phone: +91 9310519154</p>
    <p style='margin:2px 0;font-size:11px;'>Mail: contact@mirugrc.com</p>
  </div>
</div>

<div style='display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:24px;'>
  <div>
    <p style='font-size:22px;font-weight:700;margin:0;'>{data['doc_type']}</p>
    <p style='font-size:11px;color:#666;margin:2px 0;'>Doc No: {data['doc_id']}</p>
  </div>
  <div style='text-align:right;'>
    <p style='margin:2px 0;'><b>Date:</b> {data['doc_date']}</p>
    {validity_html}
  </div>
</div>

<div style='display:flex;justify-content:space-between;margin-bottom:30px;'>
  <div>
    <p style='margin:0;font-weight:600;'>RECIPIENT</p>
    <p style='margin:4px 0;white-space:pre-wrap;max-width:280px;font-size:12px;'>{data['project_name']}
{data['client_name']}
{data['billing_address']}</p>
  </div>
  <div style='text-align:right;'>
    <p style='margin:0;font-weight:600;'>DELIVERY ADDRESS</p>
    <p style='margin:4px 0;white-space:pre-wrap;max-width:280px;font-size:12px;'>{data['delivery_address']}</p>
  </div>
</div>

<table>
  <thead><tr><th>TYPE</th><th>HSN</th><th>DESCRIPTION</th><th>QTY</th><th>UNIT</th><th>RATE</th><th>AMOUNT</th></tr></thead>
  <tbody>{rows}</tbody>
</table>

<div style='display:flex;justify-content:flex-end;'>
  <table class='tot' style='width:45%;'>
    <tr><th>Subtotal:</th><td>₹{format_inr(subtotal)}</td></tr>
    <tr><th>CGST (9%):</th><td>₹{format_inr(subtotal*0.09)}</td></tr>
    <tr><th>SGST (9%):</th><td>₹{format_inr(subtotal*0.09)}</td></tr>
    <tr><th>Transportation:</th><td>{"₹" + format_inr(transport_amount) if transport_amount else "Included"}</td></tr>
    <tr><th><b>Total (Rounded):</b></th><td><b>₹{format_inr(grand)}</b></td></tr>
  </table>
</div>

<p style='font-size:11px;font-style:italic;margin-top:8px;'>
  <b>Amount in Words:</b> Rupees {amount_in_words(grand)}
</p>

<div style='margin-top:30px;font-size:11px;'>
  <p style='font-weight:600;margin-bottom:4px;'>TERMS &amp; CONDITIONS</p>
  {terms_html}
</div>

{sig_html}

<p style='margin-top:30px;font-size:9px;color:#aaa;text-align:center;'>
  Generated by MIRU Document Generator · {datetime.now().strftime('%d %b %Y %H:%M')}
</p>
{"<div class='watermark'>NOT APPROVED</div>" if watermark else ""}
</body></html>"""

def make_pdf(html):
    resp = requests.post(
        "https://api.pdfshift.io/v3/convert/pdf",
        headers={"X-API-Key": st.secrets["pdfshift"]["api_key"],
                 "Content-Type": "application/json"},
        json={"source": html, "sandbox": False},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content

# ── Approval page ──────────────────────────────────────────────────────────────

def approval_page(doc_id, token):
    doc = get_document(doc_id)
    if not doc:
        st.error("Document not found.")
        return
    if token != make_token(doc_id):
        st.error("Invalid or expired approval link.")
        return

    st.title(f"Approval Request — {doc_id}")

    if doc["status"] == "Approved":
        st.success(f"✅ Already approved by **{doc['approved_by']}** on {str(doc['approved_at'])[:10]}.")
        html = build_html(doc, doc.get("signature_b64") or None)
        pdf = make_pdf(html)
        st.download_button("📥 Download Approved PDF", pdf,
                           file_name=f"{doc_id}_{doc['client_name'].replace(' ','_')}.pdf")
        st.components.v1.html(html, height=800, scrolling=True)
        return

    st.info(f"**{doc['doc_type']}** for **{doc['client_name']}** — {doc['project_name']}")
    st.components.v1.html(build_html(doc), height=800, scrolling=True)

    st.markdown("---")
    st.subheader("Approve")
    managers = get_managers()
    if not managers:
        st.error("No managers found. Add them in the Managers sheet first.")
        return

    mgr_name = st.selectbox("Your name", [m["name"] for m in managers])
    pin = st.text_input("Your PIN", type="password")

    if st.button("✅ Approve Document", type="primary"):
        mgr = next((m for m in managers if m["name"] == mgr_name), None)
        if mgr and str(mgr["pin"]) == str(pin):
            sig_b64 = str(mgr.get("signature_b64", ""))
            approve_doc(doc_id, mgr_name, sig_b64)
            st.success("Approved! PDF is ready.")
            st.balloons()
            doc["approved_by"] = mgr_name
            html = build_html(doc, sig_b64 or None)
            pdf = make_pdf(html)
            st.download_button("📥 Download Approved PDF", pdf,
                               file_name=f"{doc_id}_{doc['client_name'].replace(' ','_')}.pdf")
        else:
            st.error("Incorrect PIN.")

# ── Create / Edit form ─────────────────────────────────────────────────────────

def doc_form(prefill=None):
    p = prefill or {}
    qp = st.query_params
    # Unique suffix per document so widgets reset when switching docs
    uid = p.get("doc_id", "new")

    client_q   = p.get("client_name")     or unquote(qp.get("client",  ""))
    billing_q  = p.get("billing_address") or unquote(qp.get("billing", ""))
    delivery_q = p.get("delivery_address") or unquote(qp.get("address", ""))
    project_q  = p.get("project_name")    or unquote(qp.get("project", ""))

    # ── Type of Sale (document-level — drives milestone filter & rate logic) ──
    ALL_SALE_TYPES = ["Supply", "Installation", "Supply & Installation"]
    first_item_stype = (p.get("items") or [{}])[0].get("sale_type", ALL_SALE_TYPES[0])
    prev_stype = first_item_stype if first_item_stype in ALL_SALE_TYPES else ALL_SALE_TYPES[0]
    doc_sale_type = st.radio(
        "Type of Sale", ALL_SALE_TYPES,
        index=ALL_SALE_TYPES.index(prev_stype),
        horizontal=True, key=f"doc_stype_{uid}"
    )

    # ── Work Order quick-load ──
    wos = get_work_orders()
    active_wos = [w for w in wos if w.get("status") == "Active"]
    wo_loaded = None
    selected_milestone = None
    billing_override = None

    if active_wos:
        st.markdown("### Load from Work Order")
        wo_options = ["— create manually —"] + [f"{w['wo_id']} — {w['project_name']} ({w['client_name']})" for w in active_wos]
        wo_sel = st.selectbox("Work Order", wo_options, key=f"wo_sel_{uid}")
        wo_loaded = next((w for w in active_wos if f"{w['wo_id']} — {w['project_name']} ({w['client_name']})" == wo_sel), None)

        if wo_loaded:
            # ── Auto-load client details from WO ──
            clients_all = get_clients()
            wo_client_data = next((c for c in clients_all if c["name"] == wo_loaded["client_name"]), None)
            if wo_client_data:
                client_q   = wo_client_data["name"]
                billing_q  = wo_client_data["billing_address"]
                delivery_q = wo_client_data["delivery_address"]
            else:
                client_q = wo_loaded["client_name"]

            # ── Auto-load terms from WO (only if not already set for this doc) ──
            terms_key = f"active_terms_{uid}"
            wo_terms  = wo_loaded.get("terms") or []
            if wo_terms and terms_key not in st.session_state:
                st.session_state[terms_key] = wo_terms

            total_value = sum(float(it["qty"]) * float(it["rate"]) for it in wo_loaded["items"])
            all_ms      = wo_loaded["milestones"]

            # Look up the 3 fixed milestones by name
            def _ms(name):
                return next((m for m in all_ms if m.get("name","").lower() == name.lower()),
                            {"name": name, "percent": 0, "status": "Pending"})

            adv_m = _ms("Advance")
            sup_m = _ms("Supply")
            ins_m = _ms("Installation")

            adv_pct = float(adv_m["percent"])
            sup_pct = float(sup_m["percent"])
            ins_pct = float(ins_m["percent"])

            if doc_sale_type == "Supply":
                # Advance already received — bill Advance + Supply cumulative
                rate_pct       = (adv_pct + sup_pct) / 100
                billing_amount = total_value * rate_pct
                st.info(
                    f"💰 **Billing Amount:** ₹{format_inr(billing_amount)}\n\n"
                    f"Advance {adv_pct:.0f}% + Supply {sup_pct:.0f}% = **{adv_pct+sup_pct:.0f}%** of ₹{format_inr(total_value)}"
                )
            elif doc_sale_type == "Installation":
                rate_pct       = ins_pct / 100
                billing_amount = total_value * rate_pct
                st.info(
                    f"💰 **Billing Amount:** ₹{format_inr(billing_amount)}\n\n"
                    f"Installation {ins_pct:.0f}% of ₹{format_inr(total_value)}"
                )
            else:  # Supply & Installation — 100%
                rate_pct       = 1.0
                billing_amount = total_value
                st.info(f"💰 **Billing Amount:** ₹{format_inr(billing_amount)} (100% — Supply & Installation)")

            billing_override   = billing_amount
            selected_milestone = None   # no longer used for selection

        st.markdown("---")

    # ── Client / project pulled from WO (read-only display) ──
    client   = wo_loaded["client_name"]   if wo_loaded else p.get("client_name", client_q)
    project  = wo_loaded["project_name"]  if wo_loaded else p.get("project_name", project_q)
    billing  = billing_q   # already set from WO client lookup above
    delivery = delivery_q

    if wo_loaded:
        st.info(f"📋 **{wo_loaded['wo_id']}** — {project} | Client: **{client}**")
    else:
        # No WO: allow manual entry
        client  = st.text_input("Client Name",  value=client_q)
        project = st.text_input("Project Name", value=project_q)
        c3, c4 = st.columns(2)
        billing  = c3.text_area("Billing Address",  value=billing_q,  height=80)
        delivery = c4.text_area("Delivery Address", value=delivery_q, height=80)

    # ── Doc type, date, transport ──
    col1, col2 = st.columns(2)
    with col1:
        types    = ["Quotation", "Proforma Invoice"]
        doc_type = st.selectbox("Document Type", types,
                                index=types.index(p["doc_type"]) if p.get("doc_type") in types else 0)
        def _auto_code(wo):
            """Derive a short doc code from WO client/project name."""
            name  = (wo.get("client_name") or wo.get("project_name") or "").upper()
            words = name.split()
            if len(words) >= 2:
                return "".join(w[:3] for w in words[:3])
            return words[0][:8] if words else ""
        auto_code = _auto_code(wo_loaded) if wo_loaded else p.get("doc_code", "")
        doc_code = st.text_input("Document Code (e.g. SHARMA)",
                                 value=auto_code,
                                 help="Used in doc ID: PI-2026-SHARMA-001")
        notes    = st.text_input("Internal Notes (not on PDF)", value=p.get("notes", ""))
    with col2:
        try:
            default_date = date.fromisoformat(str(p["doc_date"])) if p.get("doc_date") else date.today()
        except Exception:
            default_date = date.today()
        doc_date = st.date_input("Date", value=default_date)

        validity_date = None
        if doc_type == "Quotation":
            try:
                default_v = date.fromisoformat(str(p["validity_date"])) if p.get("validity_date") else date.today() + timedelta(days=30)
            except Exception:
                default_v = date.today() + timedelta(days=30)
            validity_date = st.date_input("Valid Until", value=default_v)

        transport = st.radio("Transport Charges", ["Included", "Extra"],
                             index=["Included", "Extra"].index(p.get("transport", "Included")))
        transport_amount = 0.0
        if transport == "Extra":
            transport_amount = st.number_input("Transport Amount (₹)", min_value=0.0,
                                               value=float(p.get("transport_amount", 0)))

    # ── Terms: auto-loaded from WO; shown read-only ──
    terms_key = f"active_terms_{uid}"
    # Seed from WO on first load (WO block already sets this if WO has terms)
    if terms_key not in st.session_state:
        st.session_state[terms_key] = p.get("terms") or []

    terms = st.session_state[terms_key]
    if terms:
        with st.expander(f"📜 Terms & Conditions ({len(terms)} lines — from WO)", expanded=False):
            for t in terms:
                st.markdown(f"• {t}")
    else:
        st.caption("No terms set. Add them in the Work Order to auto-load here.")

    # ── Line Items ──
    st.markdown("---")
    st.subheader("Line Items")
    catalog = get_items()
    catalog_map = {f"{it['item_code']} — {it['description']}": it for it in catalog}

    # ── Build existing items from WO or saved doc ──
    if wo_loaded and wo_loaded["items"]:
        # rate_pct already computed above in the WO milestone block
        # (billing_override / total_value gives the same ratio)
        ms_sfx = doc_sale_type.replace(" ", "").replace("&", "")
        if doc_sale_type == "Supply":
            caption_text = f"Items from {wo_loaded['wo_id']} — {rate_pct*100:.0f}% (Advance + Supply)"
        elif doc_sale_type == "Installation":
            caption_text = f"Items from {wo_loaded['wo_id']} — {rate_pct*100:.0f}% (Installation)"
        else:
            caption_text = f"Items from {wo_loaded['wo_id']} — 100% (Supply & Installation)"

        existing = []
        for it in wo_loaded["items"]:
            base        = float(it.get("rate", 0))
            s_rate      = float(it.get("supply_rate", base))
            i_rate      = float(it.get("installation_rate", 0))
            row = {
                "hsn":           "",
                "desc":          it.get("description", ""),
                "qty":           float(it.get("qty", 0)),
                "area_per_piece": float(it.get("area_per_piece", 0)),
                "pieces":        float(it.get("pieces", 0)),
                "unit":          it.get("unit", ""),
                "rate":          round(base * rate_pct, 2),
                "supply_rate":   round(s_rate * rate_pct, 2),
                "install_rate":  round(i_rate * rate_pct, 2),
                "sale_type":     doc_sale_type,
            }
            existing.append(row)
        if caption_text:
            st.caption(caption_text)
    else:
        existing = p.get("items", [])
        ms_sfx   = ""

    # Widget key suffix: changes when WO or milestone changes → forces Streamlit
    # to treat them as new widgets and use the freshly computed default values.
    wo_key  = wo_loaded["wo_id"].replace("-", "") if wo_loaded else "manual"
    st_key  = doc_sale_type.replace(" ", "")       # "Supply" / "Installation" / "Supply&Installation"
    wk      = f"{wo_key}_{st_key}_{ms_sfx}"        # combined suffix used on every item widget

    item_count = st.number_input("Number of items", 1, 20, value=max(1, len(existing)),
                                 step=1, key=f"ic_{uid}_{wk}")
    items = []
    hsn_options = ["68109990", "68109100", "69072100", "Other"]

    # Track removed items in session state (reset when WO/milestone changes)
    skip_key = f"skip_{uid}_{wk}"
    if skip_key not in st.session_state:
        st.session_state[skip_key] = set()

    for i in range(int(item_count)):
        ei      = existing[i] if i < len(existing) else {}
        skipped = i in st.session_state[skip_key]
        label   = ei.get("desc", f"Item {i+1}")

        c_btn, c_exp = st.columns([1, 15])

        # Delete / restore button outside the expander so user never has to open it to remove
        with c_btn:
            st.write("")   # vertical alignment nudge
            if skipped:
                if st.button("↩️", key=f"restore_{uid}_{wk}_{i}", help="Restore item"):
                    st.session_state[skip_key].discard(i)
                    st.rerun()
            else:
                if st.button("🗑️", key=f"del_{uid}_{wk}_{i}", help="Remove from this bill"):
                    st.session_state[skip_key].add(i)
                    st.rerun()

        with c_exp:
            exp_title = f"~~{label}~~ *(removed)*" if skipped else label
            with st.expander(exp_title, expanded=False):
                if skipped:
                    st.caption("This item will NOT be included in the document. Click ↩️ to restore.")
                    continue   # skip rendering all the widgets

                # ── Catalog picker (optional override) ──
                if catalog:
                    cat_sel  = st.selectbox("Override from catalog (optional)", ["— use WO / manual —"] + list(catalog_map.keys()), key=f"cat_{uid}_{wk}_{i}")
                    cat_item = catalog_map.get(cat_sel)
                else:
                    cat_item = None

                # ── Description ──
                if cat_item:
                    item_name = cat_item["description"]
                    if doc_sale_type == "Supply":
                        auto_desc = f"Supply of {item_name}"
                    elif doc_sale_type == "Installation":
                        auto_desc = f"Installation of {item_name}"
                    else:
                        auto_desc = f"Supply & Installation of {item_name}"
                else:
                    auto_desc = ei.get("desc", "")

                ca, cb = st.columns(2)
                with ca:
                    ei_hsn  = str(ei.get("hsn", ""))
                    hi      = hsn_options.index(ei_hsn) if ei_hsn in hsn_options else 3
                    hchoice = st.selectbox("HSN", hsn_options, index=hi, key=f"hc_{uid}_{wk}_{i}")
                    hsn     = st.text_input("HSN Code", value=ei_hsn if hchoice == "Other" else hchoice, key=f"hsn_{uid}_{wk}_{i}")
                    desc    = st.text_input("Description", value=auto_desc, key=f"desc_{uid}_{wk}_{i}")
                with cb:
                    unit_opts = ["SQFT", "RFT", "SQM", "PC", "KG"]
                    def_unit  = (cat_item["unit"] if cat_item else None) or ei.get("unit", "SQFT")
                    ui        = unit_opts.index(def_unit) if def_unit in unit_opts else 0
                    unit      = st.selectbox("Unit", unit_opts, index=ui, key=f"unit_{uid}_{wk}_{i}")

                    # Area per piece × pieces = total qty
                    qr1, qr2 = st.columns(2)
                    area_per_pc = qr1.number_input("Area per piece", value=float(ei.get("area_per_piece", 0.0)),
                                                   min_value=0.0, key=f"app_{uid}_{wk}_{i}",
                                                   help="e.g. sqft per panel")
                    pieces      = qr2.number_input("No. of pieces", value=float(ei.get("pieces", 0.0)),
                                                   min_value=0.0, key=f"pcs_{uid}_{wk}_{i}",
                                                   help="Number of panels / units")
                    if area_per_pc > 0 and pieces > 0:
                        qty = round(area_per_pc * pieces, 3)
                        st.caption(f"Total qty: {area_per_pc} × {pieces:.0f} = **{format_inr(qty)} {unit}**")
                    else:
                        qty = st.number_input("Total Qty (manual)", value=float(ei.get("qty", 0)),
                                              key=f"qty_{uid}_{wk}_{i}", min_value=0.0)

                    if doc_sale_type == "Supply & Installation":
                        sr_default   = float(cat_item.get("supply_rate", cat_item.get("base_rate", 0))) if cat_item else float(ei.get("supply_rate", ei.get("rate", 0)))
                        ir_default   = float(cat_item.get("installation_rate", 0)) if cat_item else float(ei.get("install_rate", 0))
                        supply_rate  = st.number_input("Supply Rate (₹)", value=float(ei.get("supply_rate", sr_default)), key=f"sr_{uid}_{wk}_{i}", min_value=0.0)
                        install_rate = st.number_input("Installation Rate (₹)", value=float(ei.get("install_rate", ir_default)), key=f"ir_{uid}_{wk}_{i}", min_value=0.0)
                        rate         = supply_rate
                        st.caption(f"Supply: ₹{format_inr(qty*supply_rate)} | Install: ₹{format_inr(qty*install_rate)} | Total: ₹{format_inr(qty*(supply_rate+install_rate))}")
                    else:
                        supply_rate  = None
                        install_rate = None
                        if cat_item:
                            cat_rate_default = float(
                                cat_item.get("supply_rate", cat_item["base_rate"]) if doc_sale_type == "Supply"
                                else cat_item.get("installation_rate", cat_item["base_rate"])
                            )
                        else:
                            cat_rate_default = 0.0
                        rate_default = float(ei.get("rate", 0)) if ei.get("rate") else cat_rate_default
                        rate = st.number_input("Rate (₹)", value=rate_default, key=f"rate_{uid}_{wk}_{i}", min_value=0.0)
                        st.caption(f"Amount: ₹{format_inr(qty * rate)}")

        if not skipped:
            items.append({"hsn": hsn, "desc": desc, "qty": qty, "unit": unit,
                          "rate": rate, "sale_type": doc_sale_type,
                          "supply_rate": supply_rate, "install_rate": install_rate,
                          "area_per_piece": area_per_pc, "pieces": pieces})

    # ── Totals preview ──
    subtotal = sum(float(it["qty"]) * float(it["rate"]) for it in items)
    grand    = round(subtotal * 1.18 + transport_amount)
    st.markdown("---")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Subtotal",       f"₹{format_inr(subtotal)}")
    m2.metric("GST (18%)",      f"₹{format_inr(subtotal * 0.18)}")
    m3.metric("Transport",      f"₹{format_inr(transport_amount)}" if transport == "Extra" else "Included")
    m4.metric("Grand Total",    f"₹{format_inr(grand)}")
    st.caption(f"Amount in Words: Rupees {amount_in_words(grand)}")

    return {
        "doc_type":          doc_type,
        "project_name":      project,
        "client_name":       client,
        "billing_address":   billing,
        "delivery_address":  delivery,
        "doc_date":          str(doc_date),
        "validity_date":     str(validity_date) if validity_date else "",
        "transport":         transport,
        "transport_amount":  transport_amount,
        "doc_code":          doc_code,
        "items":             items,
        "terms":             [t for t in terms if t.strip()],
        "notes":             notes,
        "wo_id":             wo_loaded["wo_id"] if wo_loaded else "",
        "wo_milestone_idx":  selected_milestone[0] if selected_milestone else None,
    }

# ── Documents list ─────────────────────────────────────────────────────────────

def documents_tab():
    st.subheader("All Documents")
    docs = all_documents()
    if not docs:
        st.info("No documents saved yet.")
        return

    c1, c2 = st.columns(2)
    ftype   = c1.selectbox("Type",   ["All", "Quotation", "Proforma Invoice"])
    fstatus = c2.selectbox("Status", ["All", "Draft", "Pending Approval", "Approved"])

    filtered = [
        d for d in reversed(docs)
        if (ftype   == "All" or d.get("doc_type") == ftype)
        and (fstatus == "All" or d.get("status")   == fstatus)
    ]

    icons   = {"Draft": "🟡", "Pending Approval": "🟠", "Approved": "🟢"}
    app_url = st.secrets["app"]["app_url"]

    for doc in filtered:
        icon  = icons.get(doc.get("status", ""), "⚪")
        label = f"{icon} {doc['doc_id']} — {doc.get('client_name','')} ({doc.get('doc_type','')})"
        with st.expander(label):
            ca, cb, cc = st.columns(3)
            ca.write(f"**Date:** {doc.get('doc_date','')}")
            cb.write(f"**Status:** {doc.get('status','')}")
            cc.write(f"**Project:** {doc.get('project_name','')}")
            if doc.get("notes"):
                st.caption(f"Notes: {doc['notes']}")

            act1, act2, act3 = st.columns(3)

            if doc["status"] in ("Draft", "Approved"):
                btn_label = "✏️ Edit" if doc["status"] == "Draft" else "✏️ Edit (resets to Draft)"
                if act1.button(btn_label, key=f"edit_{doc['doc_id']}"):
                    if doc["status"] == "Approved":
                        update_status(doc["doc_id"], "Draft")
                    st.query_params["edit"] = doc["doc_id"]
                    st.rerun()

            if doc["status"] in ("Draft", "Pending Approval"):
                managers = get_managers()
                if managers:
                    with act2:
                        mgr_choice = st.selectbox(
                            "Send to", [m["name"] for m in managers],
                            key=f"mgr_{doc['doc_id']}",
                        )
                        mgr = next(m for m in managers if m["name"] == mgr_choice)
                        approval_url = f"{app_url}?approve={doc['doc_id']}&token={doc['approval_token']}"
                        wa_msg = quote(
                            f"Approval needed for {doc['doc_id']} ({doc.get('client_name','')}):\n{approval_url}"
                        )
                        st.markdown(
                            f"[📲 Send WhatsApp](<https://wa.me/{mgr['whatsapp']}?text={wa_msg}>)",
                            unsafe_allow_html=True,
                        )
                        if st.button("Mark as Sent", key=f"sent_{doc['doc_id']}"):
                            update_status(doc["doc_id"], "Pending Approval")
                            st.success("Status updated to Pending Approval.")
                            st.rerun()

                    with act3:
                        st.write("**Approve here**")
                        pin_key = f"pin_verified_{doc['doc_id']}"
                        if not st.session_state.get(pin_key):
                            # Step 1 — enter PIN to view document
                            mgr_sel = st.selectbox("Manager", [m["name"] for m in managers], key=f"mgr_sel_{doc['doc_id']}")
                            pin = st.text_input("Enter PIN to view", type="password", key=f"pin_{doc['doc_id']}")
                            if st.button("🔓 View Document", key=f"view_{doc['doc_id']}"):
                                mgr2 = next((m for m in managers if m["name"] == mgr_sel), None)
                                if mgr2 and str(mgr2["pin"]) == str(pin):
                                    st.session_state[pin_key] = mgr_sel
                                    st.rerun()
                                else:
                                    st.error("Incorrect PIN.")
                        else:
                            # Step 2 — show document then approve button
                            verified_mgr = st.session_state[pin_key]
                            st.success(f"Viewing as {verified_mgr}")
                            d = get_document(doc["doc_id"])
                            st.components.v1.html(build_html(d, watermark=True), height=700, scrolling=True)
                            if st.button("✅ Approve Document", key=f"approve_{doc['doc_id']}", type="primary"):
                                mgr2 = next((m for m in managers if m["name"] == verified_mgr), None)
                                sig_b64 = str(mgr2.get("signature_b64", "")) if mgr2 else ""
                                approve_doc(doc["doc_id"], verified_mgr, sig_b64)
                                st.session_state.pop(pin_key, None)
                                st.success("✅ Approved!")
                                st.rerun()
                            if st.button("✖ Cancel", key=f"cancel_{doc['doc_id']}"):
                                st.session_state.pop(pin_key, None)
                                st.rerun()

            if doc["status"] == "Approved":
                if act3.button("📥 Generate PDF", key=f"pdf_{doc['doc_id']}"):
                    d = get_document(doc["doc_id"])
                    html = build_html(d, d.get("signature_b64") or None)
                    pdf  = make_pdf(html)
                    st.download_button(
                        "⬇️ Save PDF", pdf,
                        file_name=f"{doc['doc_id']}_{doc.get('client_name','').replace(' ','_')}.pdf",
                        key=f"save_{doc['doc_id']}",
                    )

# ── Settings tab ───────────────────────────────────────────────────────────────

def work_orders_tab():
    st.subheader("Work Orders")
    wos = get_work_orders()

    with st.expander("➕ Create / Edit Work Order", expanded=not bool(wos)):
        edit_wo = st.selectbox("Edit existing", ["— New Work Order —"] + [f"{w['wo_id']} — {w['project_name']}" for w in wos], key="edit_wo_sel")
        ew = next((w for w in wos if f"{w['wo_id']} — {w['project_name']}" == edit_wo), None) if edit_wo != "— New Work Order —" else None

        clients = get_clients()
        wo_id      = st.text_input("Work Order ID", value=ew["wo_id"] if ew else generate_wo_id())
        wo_client  = st.selectbox("Client", ["— select —"] + [c["name"] for c in clients],
                                  index=([c["name"] for c in clients].index(ew["client_name"]) + 1) if ew and ew.get("client_name") in [c["name"] for c in clients] else 0)
        wo_project = st.text_input("Project Name", value=ew["project_name"] if ew else "")
        wo_scope   = st.text_area("Scope of Work", value=ew["scope"] if ew else "", height=80)
        wo_status  = st.selectbox("Status", ["Active", "Completed", "On Hold"],
                                  index=["Active","Completed","On Hold"].index(ew["status"]) if ew and ew.get("status") in ["Active","Completed","On Hold"] else 0)

        # Work order items — enter directly, auto-sync to Items catalog on save
        st.markdown("**Items & Rates**")
        st.caption("Enter items directly here — they will be automatically added to the Items catalog when you save.")
        ex_items = ew["items"] if ew else []

        wo_item_count = st.number_input("Number of items", 1, 30, value=max(1, len(ex_items)), step=1, key="wo_ic")
        wo_items = []

        # Column headers
        h0, h1, h2, h3, h4, h5, h6, h7, h8 = st.columns([3, 1, 1, 1, 1, 1, 1, 1, 1])
        h0.markdown("**Description**")
        h1.markdown("**Unit**")
        h2.markdown("**Area/pc**")
        h3.markdown("**Pieces**")
        h4.markdown("**Total Qty**")
        h5.markdown("**Supply ₹**")
        h6.markdown("**Install ₹**")
        h7.markdown("**Rate ₹**")
        h8.markdown("**Contract Value**")

        for i in range(int(wo_item_count)):
            ei = ex_items[i] if i < len(ex_items) else {}
            c0, c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([3, 1, 1, 1, 1, 1, 1, 1, 1])

            w_desc = c0.text_input("", value=ei.get("description", ""),
                                   key=f"wo_desc_{i}", label_visibility="collapsed",
                                   placeholder="e.g. GFRC RB-COL-2524")
            w_unit_opts = ["SQFT", "RFT", "SQM", "PC", "KG"]
            def_unit = ei.get("unit", "SQFT")
            w_unit = c1.selectbox("", w_unit_opts,
                                  index=w_unit_opts.index(def_unit) if def_unit in w_unit_opts else 0,
                                  key=f"wo_unit_{i}", label_visibility="collapsed")

            w_area_per_pc  = c2.number_input("", value=float(ei.get("area_per_piece", 0.0)),
                                             min_value=0.0, key=f"wo_app_{i}", label_visibility="collapsed",
                                             help="Area per piece (sqft/pc)")
            w_pieces       = c3.number_input("", value=float(ei.get("pieces", 0.0)),
                                             min_value=0.0, key=f"wo_pcs_{i}", label_visibility="collapsed",
                                             help="Number of pieces")
            if w_area_per_pc > 0 and w_pieces > 0:
                w_qty = round(w_area_per_pc * w_pieces, 3)
                c4.markdown(f"**{format_inr(w_qty)}**")
            else:
                w_qty = c4.number_input("", value=float(ei.get("qty", 0)), min_value=0.0,
                                        key=f"wo_qty_{i}", label_visibility="collapsed")

            w_supply_rate  = c5.number_input("", value=float(ei.get("supply_rate", ei.get("rate", 0))),
                                             min_value=0.0, key=f"wo_srate_{i}", label_visibility="collapsed")
            w_install_rate = c6.number_input("", value=float(ei.get("installation_rate", 0)),
                                             min_value=0.0, key=f"wo_irate_{i}", label_visibility="collapsed")
            w_total_rate = w_supply_rate + w_install_rate
            c7.markdown(f"**₹{format_inr(w_total_rate)}**")
            c8.markdown(f"₹{format_inr(w_qty * w_total_rate)}")

            wo_items.append({
                "description":      w_desc,
                "unit":             w_unit,
                "qty":              w_qty,
                "area_per_piece":   w_area_per_pc,
                "pieces":           w_pieces,
                "rate":             w_total_rate,
                "supply_rate":      w_supply_rate,
                "installation_rate": w_install_rate,
            })

        # Payment milestones — fixed 3: Advance, Supply, Installation
        st.markdown("**Payment Milestones**")
        ex_ms = ew["milestones"] if ew else []

        def _ms_val(name, field, default):
            m = next((m for m in ex_ms if m.get("name","").lower() == name.lower()), {})
            return m.get(field, default)

        STATUSES = ["Pending", "Billed", "Received"]
        mc1, mc2, mc3 = st.columns([3, 1, 1])
        mc1.markdown("**Milestone**"); mc2.markdown("**%**"); mc3.markdown("**Status**")

        adv_pct  = mc2.number_input("", value=float(_ms_val("Advance","percent",10)), min_value=0.0, max_value=100.0, key="ms_adv_pct", label_visibility="collapsed")
        adv_st   = mc3.selectbox("", STATUSES, index=STATUSES.index(_ms_val("Advance","status","Pending")), key="ms_adv_st", label_visibility="collapsed")
        mc1.markdown("Advance")

        sup_pct  = mc2.number_input("", value=float(_ms_val("Supply","percent",75)), min_value=0.0, max_value=100.0, key="ms_sup_pct", label_visibility="collapsed")
        sup_st   = mc3.selectbox("", STATUSES, index=STATUSES.index(_ms_val("Supply","status","Pending")), key="ms_sup_st", label_visibility="collapsed")
        mc1.markdown("Supply")

        ins_pct  = mc2.number_input("", value=float(_ms_val("Installation","percent",15)), min_value=0.0, max_value=100.0, key="ms_ins_pct", label_visibility="collapsed")
        ins_st   = mc3.selectbox("", STATUSES, index=STATUSES.index(_ms_val("Installation","status","Pending")), key="ms_ins_st", label_visibility="collapsed")
        mc1.markdown("Installation")

        milestones = [
            {"name": "Advance",      "percent": adv_pct, "status": adv_st},
            {"name": "Supply",       "percent": sup_pct, "status": sup_st},
            {"name": "Installation", "percent": ins_pct, "status": ins_st},
        ]
        total_pct = adv_pct + sup_pct + ins_pct
        if round(total_pct, 2) != 100:
            st.warning(f"Milestones total: {total_pct:.0f}% (should be 100%)")
        else:
            st.success(f"✅ Advance {adv_pct:.0f}% + Supply {sup_pct:.0f}% + Installation {ins_pct:.0f}% = 100%")

        # Terms & Conditions for this WO (auto-loaded into PIs)
        st.markdown("**Terms & Conditions** *(auto-loaded when this WO is selected in a PI)*")
        ex_wo_terms = ew["terms"] if ew else []
        wo_term_count = st.number_input("Number of terms", 0, 15,
                                        value=max(1, len(ex_wo_terms)), step=1, key="wo_tc")
        wo_terms = [
            st.text_input(f"Term {j+1}", value=ex_wo_terms[j] if j < len(ex_wo_terms) else "",
                          key=f"wo_term_{j}")
            for j in range(int(wo_term_count))
        ]
        wo_terms = [t for t in wo_terms if t.strip()]

        total_contract = sum(it["qty"] * it["rate"] for it in wo_items)
        st.metric("Total Contract Value", f"₹{format_inr(total_contract)}")

        if st.button("💾 Save Work Order", type="primary"):
            # Save the work order
            save_work_order({"wo_id": wo_id, "client_name": wo_client, "project_name": wo_project,
                             "scope": wo_scope, "items": wo_items, "milestones": milestones,
                             "terms": wo_terms, "status": wo_status,
                             "created_at": ew["created_at"] if ew else datetime.now().isoformat()},
                            edit_id=ew["wo_id"] if ew else None)

            # Auto-sync items to the Items catalog (upsert by description)
            existing_catalog = get_items()
            existing_descs   = {it["description"]: (i, it) for i, it in enumerate(existing_catalog)}
            synced = 0
            for it in wo_items:
                if not it["description"].strip():
                    continue
                # Auto item_code: first 3 chars of desc (uppercase, no spaces) + sequential
                code_base = it["description"].replace(" ", "")[:6].upper()
                item_data = {
                    "item_code":        code_base,
                    "description":      it["description"],
                    "unit":             it["unit"],
                    "base_rate":        it["rate"],
                    "supply_rate":      it["supply_rate"],
                    "installation_rate": it["installation_rate"],
                    "category":         wo_project,
                    "sale_types":       json.dumps(["Supply", "Installation", "Supply & Installation"]),
                }
                if it["description"] in existing_descs:
                    idx, _ = existing_descs[it["description"]]
                    save_item(item_data, edit_idx=idx)
                else:
                    save_item(item_data)
                synced += 1

            st.success(f"Work Order **{wo_id}** saved. {synced} item(s) synced to catalog.")
            st.rerun()

    # List all WOs
    st.markdown("---")
    for wo in wos:
        total_value = sum(float(it["qty"]) * float(it["rate"]) for it in wo["items"])
        billed_pct  = sum(m["percent"] for m in wo["milestones"] if m["status"] in ("Billed","Received"))
        with st.expander(f"📋 {wo['wo_id']} — {wo['project_name']} | {wo['client_name']} | ₹{format_inr(total_value)}"):
            st.write(f"**Scope:** {wo['scope']}")
            st.write(f"**Contract Value:** ₹{format_inr(total_value)} | **Billed so far:** {billed_pct}%")
            st.markdown("**Milestones:**")
            for idx, m in enumerate(wo["milestones"]):
                amt = total_value * m["percent"] / 100
                icon = {"Pending": "⬜", "Billed": "🟡", "Received": "🟢"}.get(m["status"], "⬜")
                col_a, col_b = st.columns([4, 1])
                col_a.write(f"{icon} **{m['name']}** — {m['percent']}% = ₹{format_inr(amt)} ({m['status']})")
                if m["status"] == "Billed" and col_b.button("✅ Mark Received", key=f"rcv_{wo['wo_id']}_{idx}"):
                    update_wo_milestone(wo["wo_id"], idx, "Received")
                    st.rerun()


def clients_items_tab():
    st.subheader("Clients & Items Catalog")
    sub = st.radio("", ["👤 Clients", "📦 Items"], horizontal=True, label_visibility="collapsed")

    if sub == "👤 Clients":
        clients = get_clients()
        st.write(f"**{len(clients)} client(s) saved**")
        with st.expander("➕ Add / Edit Client"):
            edit_c = st.selectbox("Edit existing", ["— New Client —"] + [c["name"] for c in clients], key="edit_client_sel")
            ec = next((c for c in clients if c["name"] == edit_c), {}) if edit_c != "— New Client —" else {}
            cn = st.text_input("Client Name", value=ec.get("name", ""))
            cb = st.text_area("Billing Address", value=ec.get("billing_address", ""), height=80)
            cd = st.text_area("Delivery Address", value=ec.get("delivery_address", ""), height=80)
            cg = st.text_input("GST Number", value=ec.get("gst_number", ""))
            cp = st.text_input("Payment Terms", value=ec.get("payment_terms", ""), placeholder="e.g. 10% Advance, 75% Before Dispatch")
            cno = st.text_input("Notes", value=ec.get("notes", ""))
            if st.button("💾 Save Client"):
                idx = next((i for i, c in enumerate(clients) if c["name"] == edit_c), None)
                save_client({"name": cn, "billing_address": cb, "delivery_address": cd,
                             "gst_number": cg, "payment_terms": cp, "notes": cno}, edit_idx=idx)
                st.success(f"Client '{cn}' saved.")
                st.rerun()

        if clients:
            st.markdown("---")
            for c in clients:
                with st.expander(f"👤 {c['name']}"):
                    st.write(f"**GST:** {c.get('gst_number','—')}")
                    st.write(f"**Payment Terms:** {c.get('payment_terms','—')}")
                    st.write(f"**Billing:** {c.get('billing_address','—')}")
                    st.write(f"**Delivery:** {c.get('delivery_address','—')}")

    else:  # Items
        items = get_items()
        st.write(f"**{len(items)} item(s) in catalog**")
        with st.expander("➕ Add / Edit Item"):
            edit_i = st.selectbox("Edit existing", ["— New Item —"] + [f"{it['item_code']} — {it['description']}" for it in items], key="edit_item_sel")
            ei = next((it for it in items if f"{it['item_code']} — {it['description']}" == edit_i), {}) if edit_i != "— New Item —" else {}
            ic   = st.text_input("Item Code", value=ei.get("item_code", ""), placeholder="e.g. GFRC-RB-COL-2524")
            iname = st.text_input("Item Name (short)", value=ei.get("description", ""), placeholder="e.g. GFRC RB-COL-2524")
            ALL_SALE_TYPES = ["Supply", "Installation", "Supply & Installation"]
            try:
                existing_st = json.loads(ei.get("sale_types", "[]")) if ei.get("sale_types") else ALL_SALE_TYPES
            except Exception:
                existing_st = ALL_SALE_TYPES
            isal = st.multiselect("Applicable Sale Types", ALL_SALE_TYPES, default=existing_st)
            iu   = st.selectbox("Unit", ["RFT", "SQFT", "SQM", "PC", "KG"],
                                index=["RFT","SQFT","SQM","PC","KG"].index(ei.get("unit","SQFT")) if ei.get("unit") in ["RFT","SQFT","SQM","PC","KG"] else 1)
            icat = st.text_input("Category", value=ei.get("category", ""), placeholder="e.g. GFRC, Cladding")
            st.markdown("**Rates**")
            rc1, rc2, rc3 = st.columns(3)
            ir_supply = rc1.number_input("Supply Rate (₹)", value=float(ei.get("supply_rate", ei.get("base_rate", 0))), min_value=0.0)
            ir_install = rc2.number_input("Installation Rate (₹)", value=float(ei.get("installation_rate", 0)), min_value=0.0)
            rc3.metric("Combined Rate", f"₹{format_inr(ir_supply + ir_install)}")
            if st.button("💾 Save Item"):
                idx = next((i for i, it in enumerate(items) if it["item_code"] == ei.get("item_code")), None)
                save_item({"item_code": ic, "description": iname, "unit": iu,
                           "base_rate": ir_supply + ir_install,
                           "supply_rate": ir_supply, "installation_rate": ir_install,
                           "category": icat, "sale_types": isal}, edit_idx=idx)
                st.success(f"Item '{ic}' saved.")
                st.rerun()

        if items:
            st.markdown("---")
            for it in items:
                try:
                    stypes = json.loads(it.get("sale_types", "[]"))
                except Exception:
                    stypes = []
                sr = float(it.get("supply_rate", it.get("base_rate", 0)))
                ir = float(it.get("installation_rate", 0))
                rate_str = f"Supply ₹{format_inr(sr)}"
                if ir:
                    rate_str += f" | Installation ₹{format_inr(ir)}"
                st.write(f"**{it['item_code']}** — {it['description']} | {it['unit']} | {rate_str} | {', '.join(stypes)}")


def settings_tab():
    st.subheader("Manager Setup")
    managers = get_managers()

    if managers:
        st.write("**Current managers:**")
        for m in managers:
            has_sig = "✅" if m.get("signature_b64") else "❌ missing"
            st.write(f"- **{m['name']}** | WA: {m['whatsapp']} | Signature: {has_sig}")
    else:
        st.warning("No managers yet. Open Google Sheets → **Managers** tab and add rows: name, whatsapp (no +), pin, leave signature_b64 blank.")

    st.markdown("---")
    st.subheader("Upload Signature")
    st.caption("Upload a PNG signature for a manager (transparent background preferred, keep file small).")

    mgr_names = [m["name"] for m in managers] if managers else []
    if mgr_names:
        chosen  = st.selectbox("Manager", mgr_names)
        sig_file = st.file_uploader("Signature image", type=["png", "jpg", "jpeg"])
        if sig_file and st.button("Save Signature"):
            from PIL import Image
            from io import BytesIO
            img = Image.open(sig_file).convert("RGBA")
            img.thumbnail((300, 120), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="PNG", optimize=True)
            sig_b64 = base64.b64encode(buf.getvalue()).decode()
            ws = get_sheet().worksheet("Managers")
            for i, r in enumerate(_fetch_managers()):
                if r["name"] == chosen:
                    ws.update(f"D{i+2}", [[sig_b64]])
                    _bust()
                    st.success(f"Signature saved for {chosen}.")
                    break

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    st.markdown("""
        <h2 style='text-align:center;margin-bottom:0;'>MIRU Document Generator</h2>
        <hr style='margin-top:8px;'>
    """, unsafe_allow_html=True)

    ensure_sheets()  # cached — only runs once per session
    qp = st.query_params

    # Approval mode — full screen, no tabs
    if "approve" in qp and "token" in qp:
        approval_page(qp["approve"], qp["token"])
        return

    # Radio-based navigation persists across reruns (unlike st.tabs)
    if "nav" not in st.session_state:
        st.session_state["nav"] = "📄 New Document"
    # If coming from edit link, go to New Document
    if qp.get("edit"):
        st.session_state["nav"] = "📄 New Document"

    nav = st.radio("", ["📄 New Document", "📂 All Documents", "📋 Work Orders", "🗂️ Clients & Items", "⚙️ Settings"],
                   horizontal=True, label_visibility="collapsed", key="nav")

    if nav == "📄 New Document":
        edit_id = qp.get("edit")
        prefill = None
        if edit_id:
            prefill = get_document(edit_id)
            if prefill:
                st.info(f"Editing **{edit_id}** — save will overwrite the draft.")
            else:
                st.error(f"Document {edit_id} not found.")
                edit_id = None

        form_data = doc_form(prefill)

        b1, b2, b3, b4 = st.columns(4)
        with b1:
            if st.button("💾 Save Draft", type="primary"):
                doc_id = edit_id or generate_doc_id(form_data["doc_type"], form_data.get("doc_code", ""))
                form_data["doc_id"] = doc_id
                if prefill:
                    form_data["created_at"] = prefill.get("created_at", "")
                save_document(form_data, edit_id=edit_id)
                # Mark work order milestone as Billed
                if form_data.get("wo_id") and form_data.get("wo_milestone_idx") is not None:
                    update_wo_milestone(form_data["wo_id"], form_data["wo_milestone_idx"], "Billed")
                st.success(f"Saved: **{doc_id}**")
                if edit_id:
                    st.query_params.clear()

        with b2:
            auto_new_id = generate_doc_id(form_data["doc_type"], form_data.get("doc_code", ""))
            new_name = st.text_input("Save as new doc ID", value=auto_new_id, key="new_doc_id", label_visibility="collapsed")
            if st.button("📋 Save as New"):
                form_data["doc_id"] = new_name.strip() or auto_new_id
                form_data.pop("created_at", None)
                save_document(form_data)
                st.success(f"Saved as new: **{form_data['doc_id']}**")
                st.query_params.clear()

        with b3:
            if st.button("👁️ Preview"):
                form_data["doc_id"] = edit_id or "PREVIEW"
                html = build_html(form_data, watermark=True)
                st.components.v1.html(html, height=900, scrolling=True)

        with b4:
            if st.button("🔄 Clear Form"):
                for k in list(st.session_state.keys()):
                    if k.startswith("active_terms_"):
                        del st.session_state[k]
                st.query_params.clear()
                st.rerun()

    elif nav == "📂 All Documents":
        documents_tab()

    elif nav == "📋 Work Orders":
        work_orders_tab()

    elif nav == "🗂️ Clients & Items":
        clients_items_tab()

    elif nav == "⚙️ Settings":
        settings_tab()


if __name__ == "__main__":
    main()
