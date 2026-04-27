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
import anthropic
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

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
    "vehicle_no", "transporter_name", "distance_km", "transport_mode",
]
DISPATCH_HEADERS  = [
    "dispatch_id", "source_doc_id", "status", "client_name", "project_name",
    "billing_address", "delivery_address", "vehicle_no", "transporter_name",
    "transport_mode", "purpose", "items_json", "created_at", "finalized_at",
]
TEMPLATE_HEADERS  = ["name", "terms_json"]
MANAGER_HEADERS   = ["name", "whatsapp", "pin", "signature_b64"]
CLIENT_HEADERS    = ["name", "billing_address", "delivery_address", "gst_number", "payment_terms", "notes", "email", "contact_name"]
ITEM_HEADERS      = ["item_code", "description", "unit", "base_rate", "category", "sale_types", "supply_rate", "installation_rate"]
SETTINGS_HEADERS  = ["key", "value"]

DEFAULT_BANK = {
    "bank_name":    "Bank Name",
    "account_name": "MIRU GRC",
    "account_no":   "Account Number",
    "ifsc":         "IFSC Code",
    "branch":       "Branch",
    "account_type": "Current",
}

STATE_NAMES = {
    "01":"Jammu & Kashmir","02":"Himachal Pradesh","03":"Punjab","04":"Chandigarh",
    "05":"Uttarakhand","06":"Haryana","07":"Delhi","08":"Rajasthan",
    "09":"Uttar Pradesh","10":"Bihar","11":"Sikkim","12":"Arunachal Pradesh",
    "13":"Nagaland","14":"Manipur","15":"Mizoram","16":"Tripura",
    "17":"Meghalaya","18":"Assam","19":"West Bengal","20":"Jharkhand",
    "21":"Odisha","22":"Chhattisgarh","23":"Madhya Pradesh","24":"Gujarat",
    "26":"Dadra & Nagar Haveli","27":"Maharashtra","28":"Andhra Pradesh",
    "29":"Karnataka","30":"Goa","32":"Kerala","33":"Tamil Nadu",
    "34":"Puducherry","36":"Telangana","37":"Andhra Pradesh (New)",
}

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

    if "Settings" not in existing:
        ws = sh.add_worksheet("Settings", 50, 2)
        ws.update("A1", [SETTINGS_HEADERS])
        # Seed default bank detail rows
        for k, v in DEFAULT_BANK.items():
            ws.append_row([k, v])
    else:
        sh.worksheet("Settings").update("A1", [SETTINGS_HEADERS])

    if "Dispatches" not in existing:
        ws = sh.add_worksheet("Dispatches", 500, len(DISPATCH_HEADERS))
        ws.update("A1", [DISPATCH_HEADERS])
    else:
        ws = sh.worksheet("Dispatches")
        if ws.col_count < len(DISPATCH_HEADERS):
            ws.resize(cols=len(DISPATCH_HEADERS))
        ws.update("A1", [DISPATCH_HEADERS])

# ── Helpers ────────────────────────────────────────────────────────────────────

def generate_doc_id(doc_type, code=""):
    records = _fetch_documents()
    prefix = {"Quotation": "QT", "Proforma Invoice": "PI", "Tax Invoice": "TI", "Challan": "DC"}.get(doc_type, "DOC")
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

# ── AI Extraction ──────────────────────────────────────────────────────────────

def extract_boq_from_file(uploaded_file):
    """Send image or PDF to Claude and extract structured BOQ / work order data."""
    client = anthropic.Anthropic(api_key=st.secrets["anthropic"]["api_key"])

    file_bytes = uploaded_file.read()
    file_b64   = base64.b64encode(file_bytes).decode()
    mime       = uploaded_file.type  # e.g. image/jpeg, image/png, application/pdf

    # Build the content block
    if mime == "application/pdf":
        source_block = {"type": "base64", "media_type": "application/pdf", "data": file_b64}
        content_type = "document"
    else:
        source_block = {"type": "base64", "media_type": mime, "data": file_b64}
        content_type = "image"

    prompt = """Extract all BOQ / work order data from this document and return ONLY a JSON object with this exact structure:
{
  "project_name": "...",
  "client_name": "...",
  "scope": "...",
  "items": [
    {
      "description": "...",
      "unit": "SQFT",
      "qty": 0.0,
      "area_per_piece": 0.0,
      "pieces": 0,
      "supply_rate": 0.0,
      "installation_rate": 0.0,
      "rate": 0.0
    }
  ]
}

Rules:
- For qty: use the TOTAL AREA / total quantity column (not per-piece area)
- For area_per_piece: use the per-piece area if visible, else 0
- For pieces: use total piece count if visible, else 0
- For rate: if only one rate visible, put it in supply_rate and rate
- If project/client not visible, use empty string
- Return ONLY the JSON, no explanation"""

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {"type": content_type, "source": source_block},
                {"type": "text", "text": prompt},
            ],
        }],
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

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
        data.get("vehicle_no", ""), data.get("transporter_name", ""),
        data.get("distance_km", ""), data.get("transport_mode", "Road"),
    ]
    if edit_id:
        for i, r in enumerate(records):
            if r["doc_id"] == edit_id:
                ws.update(f"A{i+2}:X{i+2}", [row])
                _bust()
                return data["doc_id"]
    ws.append_row(row)
    _bust()
    return data["doc_id"]

@st.cache_data(ttl=60)
def _fetch_documents():
    return get_sheet().worksheet("Documents").get_all_records()

@st.cache_data(ttl=60)
def _fetch_templates():
    return get_sheet().worksheet("Terms_Templates").get_all_records()

@st.cache_data(ttl=60)
def _fetch_managers():
    return get_sheet().worksheet("Managers").get_all_records()

@st.cache_data(ttl=60)
def _fetch_clients():
    return get_sheet().worksheet("Clients").get_all_records()

@st.cache_data(ttl=60)
def _fetch_items():
    return get_sheet().worksheet("Items").get_all_records()

@st.cache_data(ttl=60)
def _fetch_work_orders():
    return get_sheet().worksheet("Work_Orders").get_all_records()

@st.cache_data(ttl=300)
def _fetch_settings():
    rows = get_sheet().worksheet("Settings").get_all_records()
    return {r["key"]: r["value"] for r in rows if r.get("key")}

def get_settings():
    return _fetch_settings()

def save_settings(kv_dict):
    ws   = get_sheet().worksheet("Settings")
    rows = ws.get_all_records()
    key_to_row = {r["key"]: i + 2 for i, r in enumerate(rows)}
    for k, v in kv_dict.items():
        if k in key_to_row:
            ws.update(f"B{key_to_row[k]}", [[v]])
        else:
            ws.append_row([k, v])
    _fetch_settings.clear()

def _bust():
    """Clear all data caches after any write."""
    _fetch_documents.clear()
    _fetch_templates.clear()
    _fetch_managers.clear()
    _fetch_clients.clear()
    _fetch_items.clear()
    _fetch_work_orders.clear()
    _fetch_dispatches.clear()

# ── Dispatch CRUD ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def _fetch_dispatches():
    try:
        return get_sheet().worksheet("Dispatches").get_all_records()
    except Exception:
        return []

def generate_dispatch_id():
    year  = datetime.now().strftime("%Y")
    count = sum(1 for r in _fetch_dispatches()
                if str(r.get("dispatch_id","")).startswith(f"DISP-{year}-")) + 1
    return f"DISP-{year}-{count:03d}"

def save_dispatch(data, edit_id=None):
    sh = get_sheet()
    ws = sh.worksheet("Dispatches")
    records = _fetch_dispatches()
    row = [
        data["dispatch_id"],
        data.get("source_doc_id", ""),
        data.get("status", "Draft"),
        data.get("client_name", ""),
        data.get("project_name", ""),
        data.get("billing_address", ""),
        data.get("delivery_address", ""),
        data.get("vehicle_no", ""),
        data.get("transporter_name", ""),
        data.get("transport_mode", "Road"),
        data.get("purpose", "Supply"),
        json.dumps(data.get("items", [])),
        data.get("created_at", datetime.now().isoformat()),
        data.get("finalized_at", ""),
    ]
    if edit_id:
        for i, r in enumerate(records):
            if r["dispatch_id"] == edit_id:
                ws.update(f"A{i+2}:N{i+2}", [row])
                _bust()
                return data["dispatch_id"]
    ws.append_row(row)
    _bust()
    return data["dispatch_id"]

def get_dispatch(dispatch_id):
    for r in _fetch_dispatches():
        if r["dispatch_id"] == dispatch_id:
            r = dict(r)
            try:
                r["items"] = json.loads(r["items_json"])
            except Exception:
                r["items"] = []
            return r
    return None

def get_dispatches():
    result = []
    for r in _fetch_dispatches():
        r = dict(r)
        try:
            r["items"] = json.loads(r["items_json"])
        except Exception:
            r["items"] = []
        result.append(r)
    return result

def delete_dispatch(dispatch_id):
    ws = get_sheet().worksheet("Dispatches")
    records = _fetch_dispatches()
    for i, r in enumerate(records):
        if r["dispatch_id"] == dispatch_id:
            ws.delete_rows(i + 2)
            _bust()
            return True
    return False

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

def send_invoice_email(to_email, cc_emails, subject, body, pdf_bytes, pdf_filename):
    """Send approved-invoice email with PDF attachment via SMTP.
    SMTP creds read from st.secrets['smtp']: host, port, user, password, from_name (optional)."""
    smtp_cfg = dict(st.secrets.get("smtp", {}))
    if not smtp_cfg:
        raise RuntimeError("SMTP not configured. Add [smtp] block to Streamlit secrets.")

    host = smtp_cfg["host"]
    port = int(smtp_cfg.get("port", 587))
    user = smtp_cfg["user"]
    password = smtp_cfg["password"]
    from_name = smtp_cfg.get("from_name", "MIRU GRC")
    from_addr = smtp_cfg.get("from_addr", user)

    msg = MIMEMultipart()
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = to_email
    if cc_emails:
        msg["Cc"] = ", ".join(cc_emails)
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain"))

    if pdf_bytes:
        part = MIMEApplication(pdf_bytes, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=pdf_filename)
        msg.attach(part)

    recipients = [to_email] + list(cc_emails or [])
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(user, password)
        s.sendmail(from_addr, recipients, msg.as_string())
    return True


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

def build_html_tax_invoice(data, signature_b64=None):
    """GST Tax Invoice — matches the standard Indian format."""
    items = data.get("items") or json.loads(data.get("items_json", "[]"))

    # Client GSTIN lookup
    try:
        client_data = next((c for c in get_clients() if c["name"] == data.get("client_name", "")), {})
        client_gstin = client_data.get("gst_number", "")
    except Exception:
        client_gstin = ""

    seller_code = "08"
    client_code = client_gstin[:2] if len(client_gstin) >= 2 else ""
    client_state = STATE_NAMES.get(client_code, "")
    is_igst = client_code and client_code != seller_code

    # Build item rows + compute totals in one pass
    rows_html = ""
    sl = 1
    subtotal = 0.0
    total_qty = 0.0
    total_unit = "Sqft"
    hsn_main = "68109990"

    for it in items:
        qty   = float(it.get("qty", 0))
        total_qty += qty
        total_unit = it.get("unit", "Sqft")
        hsn   = it.get("hsn", "") or "68109990"
        hsn_main = hsn
        desc  = it.get("desc", "")
        app   = float(it.get("area_per_piece", 0))
        pcs   = float(it.get("pieces", 0))
        stype = it.get("sale_type", "Supply")

        sub = f"<br><small style='font-size:8.5pt;'>Area of One Piece {app} {total_unit} ({int(pcs)} Pcs)</small>" if app > 0 and pcs > 0 else ""

        if stype == "Supply & Installation" and (it.get("supply_rate") or it.get("install_rate")):
            sr = float(it.get("supply_rate") or 0)
            ir = float(it.get("install_rate") or 0)
            if sr:
                subtotal += qty * sr
                rows_html += f"<tr><td align='right'>{sl}</td><td><b>Supply of {desc}</b>{sub}</td><td align='center'>{hsn}</td><td align='right'>{format_inr(qty)} {total_unit}</td><td align='right'>{format_inr(sr)}</td><td align='center'>{total_unit}</td><td align='right'>{format_inr(qty*sr)}</td></tr>"
                sl += 1
            if ir:
                subtotal += qty * ir
                rows_html += f"<tr><td align='right'>{sl}</td><td><b>Installation of {desc}</b></td><td align='center'>{hsn}</td><td align='right'>{format_inr(qty)} {total_unit}</td><td align='right'>{format_inr(ir)}</td><td align='center'>{total_unit}</td><td align='right'>{format_inr(qty*ir)}</td></tr>"
                sl += 1
        else:
            rate = float(it.get("rate", 0))
            subtotal += qty * rate
            rows_html += f"<tr><td align='right'>{sl}</td><td><b>{desc}</b>{sub}</td><td align='center'>{hsn}</td><td align='right'>{format_inr(qty)} {total_unit}</td><td align='right'>{format_inr(rate)}</td><td align='center'>{total_unit}</td><td align='right'>{format_inr(qty*rate)}</td></tr>"
            sl += 1

    transport_amount = float(data.get("transport_amount", 0) or 0)
    tax_amount  = subtotal * 0.18
    grand_pre   = subtotal + tax_amount + transport_amount
    grand       = round(grand_pre)
    round_off   = grand - grand_pre
    round_str   = f"{round_off:+.2f}" if abs(round_off) > 0.001 else "0.00"

    # Tax rows inside items table
    if is_igst:
        tax_rows_html = f"<tr><td colspan='5' style='font-style:italic'>Out Put IGST @ 18%</td><td align='center'>18 %</td><td align='right'>{format_inr(tax_amount)}</td></tr>"
        tax_table_html = f"""<table style='margin-top:4px;'>
          <thead><tr><th>HSN/SAC</th><th>Taxable Value</th><th>Rate</th><th>IGST Amount</th><th>Tax Amount</th></tr></thead>
          <tbody>
            <tr><td align='center'>{hsn_main}</td><td align='right'>₹{format_inr(subtotal)}</td><td align='center'>18%</td><td align='right'>₹{format_inr(tax_amount)}</td><td align='right'>₹{format_inr(tax_amount)}</td></tr>
            <tr><td><b>Total</b></td><td align='right'><b>₹{format_inr(subtotal)}</b></td><td></td><td align='right'><b>₹{format_inr(tax_amount)}</b></td><td align='right'><b>₹{format_inr(tax_amount)}</b></td></tr>
          </tbody></table>"""
    else:
        cgst = subtotal * 0.09
        tax_rows_html = (f"<tr><td colspan='5' style='font-style:italic'>Out Put CGST @ 9%</td><td align='center'>9 %</td><td align='right'>{format_inr(cgst)}</td></tr>"
                       + f"<tr><td colspan='5' style='font-style:italic'>Out Put SGST @ 9%</td><td align='center'>9 %</td><td align='right'>{format_inr(cgst)}</td></tr>")
        tax_table_html = f"""<table style='margin-top:4px;'>
          <thead><tr><th>HSN/SAC</th><th>Taxable Value</th><th>CGST 9%</th><th>CGST Amt</th><th>SGST 9%</th><th>SGST Amt</th><th>Tax Amount</th></tr></thead>
          <tbody>
            <tr><td align='center'>{hsn_main}</td><td align='right'>₹{format_inr(subtotal)}</td><td align='center'>9%</td><td align='right'>₹{format_inr(cgst)}</td><td align='center'>9%</td><td align='right'>₹{format_inr(cgst)}</td><td align='right'>₹{format_inr(tax_amount)}</td></tr>
            <tr><td><b>Total</b></td><td align='right'><b>₹{format_inr(subtotal)}</b></td><td></td><td align='right'><b>₹{format_inr(cgst)}</b></td><td></td><td align='right'><b>₹{format_inr(cgst)}</b></td><td align='right'><b>₹{format_inr(tax_amount)}</b></td></tr>
          </tbody></table>"""

    sig_img = f"<img src='data:image/png;base64,{signature_b64}' style='height:50px;display:block;margin:8px auto;'>" if signature_b64 else "<div style='height:60px;'></div>"
    vehicle_no  = data.get("vehicle_no", "") or ""
    transporter = data.get("transporter_name", "") or ""
    delivery_dest = (data.get("delivery_address", "") or "").split("\n")[0]
    delivery_addr = (data.get("delivery_address", "") or "").replace("\n", "<br>")
    billing_addr  = (data.get("billing_address",  "") or "").replace("\n", "<br>")
    gstin_line = f"GSTIN/UIN: {client_gstin}<br>" if client_gstin else ""
    state_line = f"State Name: {client_state}, Code: {client_code}" if client_state else ""

    return f"""<!DOCTYPE html>
<html><head><meta charset='UTF-8'>
<style>
@page {{margin:8mm 10mm;}}
body{{font-family:Arial,sans-serif;font-size:10pt;color:#000;margin:0;}}
.page{{border:1px solid #000;padding:6px;}}
.title{{text-align:center;font-size:15pt;font-weight:bold;border-bottom:2px solid #000;padding-bottom:4px;margin-bottom:0;}}
table{{border-collapse:collapse;width:100%;}}
td,th{{border:1px solid #000;padding:3px 5px;font-size:9.5pt;vertical-align:top;}}
th{{background:#f0f0f0;font-weight:bold;text-align:center;}}
.grid td{{border:none;border-bottom:1px solid #bbb;border-right:1px solid #bbb;padding:2px 4px;font-size:8.5pt;}}
.grid td:last-child{{border-right:none;}}
.grid tr:last-child td{{border-bottom:none;}}
</style>
</head><body><div class='page'>
<div class='title'>Tax Invoice</div>

<table>
  <tr>
    <td style='width:44%;'>
      <b>MIRU GRC C/O MIXD STUDIO BY RMT</b><br>
      E-1 (A) RIICO Industrial Area<br>Ranpur<br>325003<br>
      GSTIN/UIN: 08ACDFM6440P1ZQ<br>
      State Name: Rajasthan, Code: 08<br>
      E-Mail: contact.mixdstudio@gmail.com
    </td>
    <td style='width:56%;padding:0;'>
      <table class='grid'>
        <tr><td style='width:34%'>Invoice No.</td><td style='width:28%'><b>{data['doc_id']}</b></td><td style='width:18%'>Dated</td><td><b>{data['doc_date']}</b></td></tr>
        <tr><td>Reference</td><td>{data.get('project_name','')}</td><td>Other References</td><td></td></tr>
        <tr><td>Delivery Note</td><td></td><td>Mode/Terms of Payment</td><td></td></tr>
        <tr><td>Buyer's Order No.</td><td></td><td>Dated</td><td></td></tr>
        <tr><td>Dispatch Doc No.</td><td></td><td>Delivery Note Date</td><td></td></tr>
        <tr><td>Dispatched through</td><td>{transporter}</td><td>Destination</td><td>{delivery_dest}</td></tr>
        <tr><td>Motor Vehicle No.</td><td>{vehicle_no}</td><td>Terms of Delivery</td><td></td></tr>
        <tr><td>EWB No. dt. {data['doc_date']}</td><td></td><td>Mode of Transport</td><td>{data.get('transport_mode','Road')}</td></tr>
      </table>
    </td>
  </tr>
</table>

<table>
  <tr>
    <td style='width:50%;'>
      <b>Consignee (Ship to)</b><br>
      {data['client_name']}<br>{delivery_addr}<br>
      {gstin_line}{state_line}
    </td>
    <td style='width:50%;'>
      <b>Buyer (Bill to)</b><br>
      {data['client_name']}<br>{billing_addr}<br>
      {gstin_line}{state_line}
    </td>
  </tr>
</table>

<table>
  <thead>
    <tr><th style='width:4%'>Sl No.</th><th style='width:38%'>Description of Goods</th><th style='width:10%'>HSN/SAC</th><th style='width:12%'>Quantity</th><th style='width:12%'>Rate</th><th style='width:6%'>per</th><th style='width:18%'>Amount</th></tr>
  </thead>
  <tbody>{rows_html}</tbody>
  {tax_rows_html}
  <tr><td colspan='6'><b>Round Off</b></td><td align='right'>{round_str}</td></tr>
  <tr><td colspan='3'><b>Total</b></td><td align='right'><b>{format_inr(total_qty)} {total_unit}</b></td><td></td><td></td><td align='right'><b>₹ {format_inr(grand)}</b></td></tr>
</table>

<table>
  <tr><td style='border-bottom:none;'><b>Amount Chargeable (in words)</b></td><td align='right' style='border-bottom:none;'>E. &amp; O.E</td></tr>
  <tr><td colspan='2'><b>INR {amount_in_words(grand)}</b></td></tr>
</table>

{tax_table_html}
<div style='padding:3px 0;font-size:9pt;'><b>Tax Amount (in words):</b> INR {amount_in_words(int(tax_amount))}</div>

<table style='margin-top:8px;'>
  <tr>
    <td style='width:60%;'>
      <b>Declaration</b><br><br>
      We declare that this invoice shows the actual price of the goods described and that all particulars are true and correct.
    </td>
    <td style='width:40%;text-align:center;'>
      for MIRU GRC C/O MIXD STUDIO BY RMT<br>
      {sig_img}
      <b>Authorised Signatory</b>
    </td>
  </tr>
</table>

<div style='text-align:center;font-size:9pt;margin-top:6px;'>This is a Computer Generated Invoice</div>
</div></body></html>"""

def build_html_challan(data, signature_b64=None):
    """Delivery Challan — dispatch details only, no rates or amounts."""
    items = data.get("items") or json.loads(data.get("items_json", "[]"))

    vehicle_no  = data.get("vehicle_no", "") or ""
    transporter = data.get("transporter_name", "") or ""
    transport_mode = data.get("transport_mode", "Road") or "Road"
    purpose     = data.get("notes", "Supply") or "Supply"
    delivery_addr = (data.get("delivery_address", "") or "").replace("\n", "<br>")
    billing_addr  = (data.get("billing_address",  "") or "").replace("\n", "<br>")

    # Try client GSTIN lookup
    try:
        client_data  = next((c for c in get_clients() if c["name"] == data.get("client_name", "")), {})
        client_gstin = client_data.get("gst_number", "")
    except Exception:
        client_gstin = ""
    client_code  = client_gstin[:2] if len(client_gstin) >= 2 else ""
    client_state = STATE_NAMES.get(client_code, "")
    gstin_line   = f"GSTIN/UIN: {client_gstin}<br>" if client_gstin else ""
    state_line   = f"State Name: {client_state}, Code: {client_code}" if client_state else ""

    # Item rows (no rates)
    rows_html  = ""
    total_qty  = {}   # unit → qty
    for sl, it in enumerate(items, 1):
        qty  = float(it.get("qty", 0))
        unit = it.get("unit", "Sqft")
        hsn  = it.get("hsn", "") or "68109990"
        desc = it.get("desc", "")
        app  = float(it.get("area_per_piece", 0))
        pcs  = float(it.get("pieces", 0))
        rmk  = it.get("remarks", "")
        sub  = f"<br><small style='font-size:8.5pt;'>Area of One Piece: {app} {unit} × {int(pcs)} Pcs</small>" if app > 0 and pcs > 0 else ""
        rows_html += f"<tr><td align='right'>{sl}</td><td><b>{desc}</b>{sub}</td><td align='center'>{hsn}</td><td align='right'>{format_inr(qty)}</td><td align='center'>{unit}</td><td>{rmk}</td></tr>"
        total_qty[unit] = total_qty.get(unit, 0) + qty

    total_str = " | ".join(f"{format_inr(v)} {u}" for u, v in total_qty.items())
    sig_img   = f"<img src='data:image/png;base64,{signature_b64}' style='height:50px;display:block;margin:8px auto;'>" if signature_b64 else "<div style='height:60px;'></div>"

    return f"""<!DOCTYPE html>
<html><head><meta charset='UTF-8'>
<style>
@page {{margin:8mm 10mm;}}
body{{font-family:Arial,sans-serif;font-size:10pt;color:#000;margin:0;}}
.page{{border:1px solid #000;padding:6px;}}
.title{{text-align:center;font-size:15pt;font-weight:bold;border-bottom:2px solid #000;padding-bottom:4px;}}
table{{border-collapse:collapse;width:100%;}}
td,th{{border:1px solid #000;padding:3px 5px;font-size:9.5pt;vertical-align:top;}}
th{{background:#f0f0f0;font-weight:bold;text-align:center;}}
.grid td{{border:none;border-bottom:1px solid #bbb;border-right:1px solid #bbb;padding:2px 4px;font-size:8.5pt;}}
.grid td:last-child{{border-right:none;}}
.grid tr:last-child td{{border-bottom:none;}}
</style>
</head><body><div class='page'>
<div class='title'>Delivery Challan</div>

<table>
  <tr>
    <td style='width:44%;'>
      <b>MIRU GRC C/O MIXD STUDIO BY RMT</b><br>
      E-1 (A) RIICO Industrial Area<br>Ranpur<br>325003<br>
      GSTIN/UIN: 08ACDFM6440P1ZQ<br>
      State Name: Rajasthan, Code: 08<br>
      E-Mail: contact.mixdstudio@gmail.com
    </td>
    <td style='width:56%;padding:0;'>
      <table class='grid'>
        <tr><td style='width:36%'>Challan No.</td><td style='width:28%'><b>{data['doc_id']}</b></td><td style='width:18%'>Date</td><td><b>{data['doc_date']}</b></td></tr>
        <tr><td>Purpose</td><td colspan='3'><b>{purpose}</b></td></tr>
        <tr><td>Vehicle No.</td><td><b>{vehicle_no}</b></td><td>Mode</td><td>{transport_mode}</td></tr>
        <tr><td>Transporter</td><td>{transporter}</td><td>Project</td><td>{data.get('project_name','')}</td></tr>
      </table>
    </td>
  </tr>
</table>

<table>
  <tr>
    <td style='width:50%;'>
      <b>Consignee (Ship to)</b><br>
      {data['client_name']}<br>{delivery_addr}<br>
      {gstin_line}{state_line}
    </td>
    <td style='width:50%;'>
      <b>Buyer (Bill to)</b><br>
      {data['client_name']}<br>{billing_addr}<br>
      {gstin_line}{state_line}
    </td>
  </tr>
</table>

<table style='margin-top:0;'>
  <thead>
    <tr><th style='width:4%'>Sl No.</th><th style='width:42%'>Description of Goods</th><th style='width:10%'>HSN/SAC</th><th style='width:12%'>Quantity</th><th style='width:8%'>Unit</th><th style='width:24%'>Remarks</th></tr>
  </thead>
  <tbody>{rows_html}</tbody>
  <tr><td colspan='3'><b>Total</b></td><td align='right'><b>{total_str}</b></td><td></td><td></td></tr>
</table>

<div style='font-size:9pt;padding:4px 0;font-style:italic;'>
  This is a Delivery Challan only. No commercial value. Goods are being sent for {purpose.lower()}.
</div>

<table style='margin-top:8px;'>
  <tr>
    <td style='width:60%;'>
      <b>Declaration</b><br><br>
      We declare that this challan shows the actual description and quantity of goods dispatched and that all particulars are true and correct.
    </td>
    <td style='width:40%;text-align:center;'>
      for MIRU GRC C/O MIXD STUDIO BY RMT<br>
      {sig_img}
      <b>Authorised Signatory</b>
    </td>
  </tr>
</table>
<div style='text-align:center;font-size:9pt;margin-top:6px;'>This is a Computer Generated Challan</div>
</div></body></html>"""

def build_html(data, signature_b64=None, watermark=False):
    if data.get("doc_type") == "Tax Invoice":
        return build_html_tax_invoice(data, signature_b64)
    if data.get("doc_type") == "Challan":
        return build_html_challan(data, signature_b64)

    logo_b64 = img_b64("MIRU GRC _INDIAS FASTEST GROWING BRAND_Black.png")
    logo_html = (f"<img src='data:image/png;base64,{logo_b64}' style='height:50px;'>"
                 if logo_b64 else "<strong>MIXD STUDIO BY RMT</strong>")

    items = data.get("items") or json.loads(data.get("items_json", "[]"))
    terms = data.get("terms") or json.loads(data.get("terms_json", "[]"))

    def _item_rows(items):
        html = ""
        for it in items:
            qty         = float(it.get("qty", 0))
            app         = float(it.get("area_per_piece", 0))
            pcs         = float(it.get("pieces", 0))
            # Show "X sqft × Y pcs = Z sqft" if area/pieces available, else just qty
            if app > 0 and pcs > 0:
                qty_cell = f"{app} × {pcs:.0f} = {format_inr(qty)}"
            else:
                qty_cell = format_inr(qty) if qty else "—"

            unit = it.get("unit", "")

            if it.get("sale_type") == "Supply & Installation" and (it.get("supply_rate") or it.get("install_rate")):
                sr = float(it.get("supply_rate") or 0)
                ir = float(it.get("install_rate") or 0)
                if sr:
                    html += (
                        f"<tr><td>Supply</td><td>{it.get('hsn','')}</td><td>Supply of {it['desc']}</td>"
                        f"<td>{qty_cell}</td><td>{unit}</td><td>₹{format_inr(sr)}</td>"
                        f"<td>₹{format_inr(qty * sr)}</td></tr>"
                    )
                if ir:
                    html += (
                        f"<tr><td>Installation</td><td>{it.get('hsn','')}</td><td>Installation of {it['desc']}</td>"
                        f"<td>{qty_cell}</td><td>{unit}</td><td>₹{format_inr(ir)}</td>"
                        f"<td>₹{format_inr(qty * ir)}</td></tr>"
                    )
            else:
                rate = float(it.get("rate", 0))
                html += (
                    f"<tr><td>{it.get('sale_type','')}</td><td>{it.get('hsn','')}</td><td>{it['desc']}</td>"
                    f"<td>{qty_cell}</td><td>{unit}</td><td>₹{format_inr(rate)}</td>"
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

    ewb_html = ""
    if data.get("doc_type") == "Tax Invoice" and (data.get("vehicle_no") or data.get("transporter_name")):
        ewb_html = f"""
        <div style='background:#f0f7ff;border:1px solid #c0d8f0;border-radius:4px;padding:8px 12px;margin-bottom:16px;font-size:11px;'>
          <b>🚛 E-Way Bill Details</b>&nbsp;&nbsp;
          Vehicle: <b>{data.get('vehicle_no','—')}</b>&nbsp;&nbsp;|&nbsp;&nbsp;
          Transporter: <b>{data.get('transporter_name','—')}</b>&nbsp;&nbsp;|&nbsp;&nbsp;
          Distance: <b>{data.get('distance_km','—')} km</b>&nbsp;&nbsp;|&nbsp;&nbsp;
          Mode: <b>{data.get('transport_mode','Road')}</b>
        </div>"""

    sig_html = ""
    if signature_b64:
        sig_html = f"""
        <div style='margin-top:50px;text-align:right;'>
            <img src='data:image/png;base64,{signature_b64}' style='height:60px;'><br>
            <small style='font-size:10px;'>Authorised Signatory: {data.get('approved_by','')}</small>
        </div>"""

    terms_html = "".join(f"<p style='margin:3px 0'>{i+1}. {t}</p>" for i, t in enumerate(terms))

    # Bank details
    try:
        bank = get_settings()
    except Exception:
        bank = {}
    bank_html = f"""
<table style='width:60%;border-collapse:collapse;font-size:11px;'>
  <tr><td colspan='2' style='font-weight:700;padding:4px 0;border-bottom:1px solid #ccc;'>BANK DETAILS</td></tr>
  <tr><td style='padding:2px 8px 2px 0;color:#555;width:40%'>Bank Name</td><td><b>{bank.get('bank_name', DEFAULT_BANK['bank_name'])}</b></td></tr>
  <tr><td style='padding:2px 8px 2px 0;color:#555;'>Account Name</td><td><b>{bank.get('account_name', DEFAULT_BANK['account_name'])}</b></td></tr>
  <tr><td style='padding:2px 8px 2px 0;color:#555;'>Account Number</td><td><b>{bank.get('account_no', DEFAULT_BANK['account_no'])}</b></td></tr>
  <tr><td style='padding:2px 8px 2px 0;color:#555;'>IFSC Code</td><td><b>{bank.get('ifsc', DEFAULT_BANK['ifsc'])}</b></td></tr>
  <tr><td style='padding:2px 8px 2px 0;color:#555;'>Branch</td><td>{bank.get('branch', DEFAULT_BANK['branch'])}</td></tr>
  <tr><td style='padding:2px 8px 2px 0;color:#555;'>Account Type</td><td>{bank.get('account_type', DEFAULT_BANK['account_type'])}</td></tr>
</table>"""

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

{ewb_html}
<table>
  <thead><tr><th>TYPE</th><th>HSN</th><th>DESCRIPTION</th><th>AREA/PC × PCS = QTY</th><th>UNIT</th><th>RATE</th><th>AMOUNT</th></tr></thead>
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

<div style='margin-top:24px;display:flex;gap:40px;align-items:flex-start;'>
  <div style='flex:1'>{bank_html}</div>
  <div style='flex:1;font-size:11px;'>
    <p style='font-weight:700;margin-bottom:4px;border-bottom:1px solid #ccc;padding-bottom:4px;'>TERMS &amp; CONDITIONS</p>
    {terms_html}
  </div>
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

    # Find client email from clients sheet
    client_email_default = ""
    try:
        cli = next((c for c in get_clients() if c.get("name") == doc.get("client_name")), {})
        client_email_default = str(cli.get("email", "") or "")
    except Exception:
        pass

    em_col1, em_col2 = st.columns([2, 3])
    send_email = em_col1.checkbox("📧 Email PDF to client on approval", value=bool(client_email_default))
    client_email = em_col2.text_input("Client email", value=client_email_default,
                                      placeholder="client@example.com",
                                      disabled=not send_email)
    cc_input = st.text_input("CC (comma-separated, optional)", value="",
                             placeholder="finance@yourcompany.com, manager@…",
                             disabled=not send_email)

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
            pdf_filename = f"{doc_id}_{doc['client_name'].replace(' ','_')}.pdf"
            st.download_button("📥 Download Approved PDF", pdf, file_name=pdf_filename)

            if send_email and client_email.strip():
                cc_list = [e.strip() for e in cc_input.split(",") if e.strip()]
                subject = f"{doc.get('doc_type','Document')} {doc.get('doc_code') or doc_id} — {doc.get('project_name','')}"
                body = (
                    f"Dear {doc.get('client_name','')},\n\n"
                    f"Please find attached the approved {doc.get('doc_type','document')} "
                    f"({doc.get('doc_code') or doc_id}) for {doc.get('project_name','')}.\n\n"
                    f"Approved by: {mgr_name}\n"
                    f"Date: {datetime.now().strftime('%d %b %Y')}\n\n"
                    f"Regards,\nMIRU GRC"
                )
                try:
                    with st.spinner("Sending email…"):
                        send_invoice_email(client_email.strip(), cc_list, subject, body, pdf, pdf_filename)
                    st.success(f"📧 Email sent to {client_email.strip()}" + (f" (cc: {', '.join(cc_list)})" if cc_list else ""))
                except Exception as e:
                    st.error(f"Email failed: {e}")
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
    items_q    = unquote(qp.get("items",   ""))   # JSON-encoded items (legacy, kept for compat)

    # ?dispatch=UUID  →  fetch client/project/items from MIRU dashboard API
    dispatch_id = qp.get("dispatch", "")
    if dispatch_id and not p.get("items"):
        try:
            import requests as _req
            api_url = f"https://mirugrc-dash.vercel.app/api/invoice-data/{dispatch_id}"
            r = _req.get(api_url, timeout=10)
            if r.ok:
                d = r.json()
                if not client_q:  client_q  = d.get("client", "")
                if not project_q: project_q = d.get("project", "")
                items_q = json.dumps(d.get("items", []))
        except Exception:
            pass

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
        types    = ["Quotation", "Proforma Invoice", "Tax Invoice", "Challan"]
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

    # ── Dispatch / E-Way Bill fields (Tax Invoice + Challan) ──
    vehicle_no = ""; transporter_name = ""; distance_km = ""; transport_mode = "Road"
    challan_purpose = "Supply"
    if doc_type in ("Tax Invoice", "Challan"):
        st.markdown("---")
        if doc_type == "Tax Invoice":
            st.subheader("🚛 E-Way Bill Details")
            st.caption("Required for goods movement > ₹50,000. Will be used to auto-generate e-way bill on portal.")
        else:
            st.subheader("🚚 Dispatch Details")
        ew1, ew2, ew3, ew4 = st.columns(4)
        vehicle_no       = ew1.text_input("Vehicle No.", value=p.get("vehicle_no", ""),
                                          placeholder="e.g. DL01AB1234", key=f"vno_{uid}")
        transporter_name = ew2.text_input("Transporter Name", value=p.get("transporter_name", ""),
                                          placeholder="e.g. DTDC Logistics", key=f"tname_{uid}")
        distance_km      = ew3.text_input("Distance (km)", value=str(p.get("distance_km", "")),
                                          placeholder="e.g. 15", key=f"dist_{uid}")
        mode_opts        = ["Road", "Rail", "Air", "Ship"]
        prev_mode        = p.get("transport_mode", "Road")
        transport_mode   = ew4.selectbox("Mode of Transport", mode_opts,
                                         index=mode_opts.index(prev_mode) if prev_mode in mode_opts else 0,
                                         key=f"tmode_{uid}")
        if doc_type == "Challan":
            purpose_opts  = ["Supply", "Job Work", "Sales Return", "Exhibition / Fairs", "Others"]
            prev_purpose  = p.get("notes", "Supply") if p.get("notes", "") in purpose_opts else "Supply"
            challan_purpose = st.selectbox("Purpose of Challan", purpose_opts,
                                           index=purpose_opts.index(prev_purpose),
                                           key=f"cpurpose_{uid}")

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
    # Helper: parse dispatch items_q into a list of {desc, qty, area_per_piece, pieces, unit}
    def _parse_dispatch_items():
        try:
            raw = json.loads(items_q)
            out = []
            if raw and isinstance(raw[0], list):
                for row in raw:
                    desc    = row[0] if len(row) > 0 else ""
                    pieces  = float(row[1]) if len(row) > 1 else 0
                    area_pc = float(row[2]) if len(row) > 2 else 0
                    qty     = round(area_pc * pieces, 2) if area_pc > 0 else pieces
                    out.append({"desc": desc, "qty": qty, "area_per_piece": area_pc,
                                "pieces": pieces, "unit": "SQFT" if area_pc > 0 else "PC"})
            else:
                out = raw
            return out
        except Exception:
            return []

    dispatch_items_parsed = _parse_dispatch_items() if (items_q and not p.get("items")) else []

    if wo_loaded and wo_loaded["items"]:
        # rate_pct already computed above in the WO milestone block
        ms_sfx = doc_sale_type.replace(" ", "").replace("&", "")
        if doc_sale_type == "Supply":
            caption_text = f"Items from {wo_loaded['wo_id']} — {rate_pct*100:.0f}% (Advance + Supply)"
        elif doc_sale_type == "Installation":
            caption_text = f"Items from {wo_loaded['wo_id']} — {rate_pct*100:.0f}% (Installation)"
        else:
            caption_text = f"Items from {wo_loaded['wo_id']} — 100% (Supply & Installation)"

        # Build a name→rate lookup from WO items (case-insensitive, normalized)
        def _norm(s): return "".join(c.lower() for c in str(s) if c.isalnum())
        wo_rate_map = {}
        for it in wo_loaded["items"]:
            wo_rate_map[_norm(it.get("description", ""))] = it
        # Fallback "base rate" = first WO item's rate (used when dispatch desc doesn't match)
        wo_base = wo_loaded["items"][0]
        base_fallback         = float(wo_base.get("rate", 0))
        supply_fallback       = float(wo_base.get("supply_rate", base_fallback))
        install_fallback      = float(wo_base.get("installation_rate", 0))

        existing = []
        if dispatch_items_parsed:
            # Dispatch dominates description/qty/area; rates come from WO (by name match, else base)
            st.caption(f"📦 Dispatch items + rates from {wo_loaded['wo_id']} (base rate ₹{format_inr(base_fallback)})")
            matched = 0
            for di in dispatch_items_parsed:
                wo_match = wo_rate_map.get(_norm(di.get("desc", "")))
                if wo_match:
                    matched += 1
                    base   = float(wo_match.get("rate", 0))
                    s_rate = float(wo_match.get("supply_rate", base))
                    i_rate = float(wo_match.get("installation_rate", 0))
                else:
                    base, s_rate, i_rate = base_fallback, supply_fallback, install_fallback
                existing.append({
                    "hsn":            "68109990",
                    "desc":           di.get("desc", ""),
                    "qty":            float(di.get("qty", 0)),
                    "area_per_piece": float(di.get("area_per_piece", 0)),
                    "pieces":         float(di.get("pieces", 0)),
                    "unit":           di.get("unit", "SQFT"),
                    "rate":           round(base * rate_pct, 2),
                    "supply_rate":    round(s_rate * rate_pct, 2),
                    "install_rate":   round(i_rate * rate_pct, 2),
                    "sale_type":      doc_sale_type,
                })
            st.caption(f"✓ {matched}/{len(dispatch_items_parsed)} items matched WO by description; rest use base rate.")
        else:
            for it in wo_loaded["items"]:
                base        = float(it.get("rate", 0))
                s_rate      = float(it.get("supply_rate", base))
                i_rate      = float(it.get("installation_rate", 0))
                existing.append({
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
                })
        if caption_text:
            st.caption(caption_text)
    elif items_q and not p.get("items"):
        # Pre-fill from dispatch dashboard URL param (?items=JSON)
        # Accepts compact array format [[desc, pieces, area_per_piece], ...]
        # or full object format [{hsn, desc, qty, ...}, ...]
        try:
            raw = json.loads(items_q)
            if raw and isinstance(raw[0], list):
                existing = []
                for row in raw:
                    desc    = row[0] if len(row) > 0 else ""
                    pieces  = float(row[1]) if len(row) > 1 else 0
                    area_pc = float(row[2]) if len(row) > 2 else 0
                    qty     = round(area_pc * pieces, 2) if area_pc > 0 else pieces
                    existing.append({
                        "hsn":            "68109990",
                        "desc":           desc,
                        "qty":            qty,
                        "area_per_piece": area_pc,
                        "pieces":         pieces,
                        "unit":           "SQFT" if area_pc > 0 else "PC",
                        "rate":           0,
                        "supply_rate":    0,
                        "install_rate":   0,
                        "sale_type":      "Supply",
                    })
            else:
                existing = raw
        except Exception:
            existing = []
        ms_sfx = ""
    else:
        existing = p.get("items", [])
        ms_sfx   = ""

    # Widget key suffix: changes when WO or milestone changes → forces Streamlit
    # to treat them as new widgets and use the freshly computed default values.
    wo_key  = wo_loaded["wo_id"].replace("-", "") if wo_loaded else "manual"
    st_key  = doc_sale_type.replace(" ", "")       # "Supply" / "Installation" / "Supply&Installation"
    wk      = f"{wo_key}_{st_key}_{ms_sfx}"        # combined suffix used on every item widget

    item_count = st.number_input("Number of items", 1, 200, value=max(1, len(existing)),
                                 step=1, key=f"ic_{uid}_{wk}")

    # ── Bulk rate fill ──
    with st.expander("⚡ Bulk fill rate for all items", expanded=False):
        st.caption("Set one rate and apply to every item below. You can still edit individual rates after.")
        if doc_sale_type == "Supply & Installation":
            bcs, bci, bca = st.columns([2, 2, 1])
            bulk_supply = bcs.number_input("Supply Rate (₹)", min_value=0.0, value=0.0, key=f"bulk_sr_{uid}_{wk}")
            bulk_install = bci.number_input("Installation Rate (₹)", min_value=0.0, value=0.0, key=f"bulk_ir_{uid}_{wk}")
            with bca:
                st.write("")
                if st.button("Apply to all", key=f"bulk_apply_{uid}_{wk}", use_container_width=True):
                    for j in range(int(item_count)):
                        if bulk_supply > 0:
                            st.session_state[f"sr_{uid}_{wk}_{j}"] = float(bulk_supply)
                        if bulk_install > 0:
                            st.session_state[f"ir_{uid}_{wk}_{j}"] = float(bulk_install)
                    st.rerun()
        else:
            bcr, bca = st.columns([3, 1])
            bulk_rate = bcr.number_input("Rate (₹)", min_value=0.0, value=0.0, key=f"bulk_rate_{uid}_{wk}")
            with bca:
                st.write("")
                if st.button("Apply to all", key=f"bulk_apply_{uid}_{wk}", use_container_width=True):
                    if bulk_rate > 0:
                        for j in range(int(item_count)):
                            st.session_state[f"rate_{uid}_{wk}_{j}"] = float(bulk_rate)
                        st.rerun()

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

                    if doc_type == "Challan":
                        # No rates on challan — just show remarks field
                        supply_rate  = None
                        install_rate = None
                        rate         = 0.0
                        st.text_input("Remarks", value=ei.get("remarks", ""), key=f"rmk_{uid}_{wk}_{i}",
                                      placeholder="e.g. For job work / sample")
                    elif doc_sale_type == "Supply & Installation":
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
    if doc_type == "Challan":
        total_qty_ch = sum(float(it.get("qty", 0)) for it in items)
        st.metric("Total Quantity", f"{format_inr(total_qty_ch)} units")
    else:
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
        "notes":             challan_purpose if doc_type == "Challan" else notes,
        "wo_id":             wo_loaded["wo_id"] if wo_loaded else "",
        "wo_milestone_idx":  selected_milestone[0] if selected_milestone else None,
        "vehicle_no":        vehicle_no,
        "transporter_name":  transporter_name,
        "distance_km":       distance_km,
        "transport_mode":    transport_mode,
    }

# ── Documents list ─────────────────────────────────────────────────────────────

def documents_tab():
    st.subheader("All Documents")
    docs = all_documents()
    if not docs:
        st.info("No documents saved yet.")
        return

    c1, c2 = st.columns(2)
    ftype   = c1.selectbox("Type",   ["All", "Quotation", "Proforma Invoice", "Tax Invoice", "Challan"])
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
                pdf_col, challan_col = st.columns(2)

                # ── Generate document PDF ──
                if pdf_col.button("📥 Generate PDF", key=f"pdf_{doc['doc_id']}", use_container_width=True):
                    d    = get_document(doc["doc_id"])
                    html = build_html(d, d.get("signature_b64") or None)
                    pdf  = make_pdf(html)
                    pdf_col.download_button(
                        "⬇️ Save PDF", pdf,
                        file_name=f"{doc['doc_id']}_{doc.get('client_name','').replace(' ','_')}.pdf",
                        key=f"save_{doc['doc_id']}",
                    )

                # ── Create Dispatch button → shows inline form before saving ──
                cd_open_key = f"cd_open_{doc['doc_id']}"
                if challan_col.button("🚚 Create Dispatch", key=f"challan_{doc['doc_id']}", use_container_width=True):
                    st.session_state[cd_open_key] = True
                    st.rerun()

                if st.session_state.get(cd_open_key):
                    d = get_document(doc["doc_id"])
                    orig_items = d.get("items") or []

                    st.markdown("---")
                    st.markdown("#### 🚚 New Dispatch")

                    # Header fields
                    dh1, dh2, dh3, dh4 = st.columns(4)
                    cd_vno  = dh1.text_input("Vehicle No.",
                                             value=d.get("vehicle_no",""),
                                             key=f"cd_vno_{doc['doc_id']}")
                    cd_tsp  = dh2.text_input("Transporter",
                                             value=d.get("transporter_name",""),
                                             key=f"cd_tsp_{doc['doc_id']}")
                    p_opts  = ["Supply","Job Work","Sales Return","Exhibition / Fairs","Others"]
                    cd_purp = dh3.selectbox("Purpose", p_opts,
                                            key=f"cd_purp_{doc['doc_id']}")
                    m_opts  = ["Road","Rail","Air","Ship"]
                    cd_mode = dh4.selectbox("Mode", m_opts,
                                            key=f"cd_mode_{doc['doc_id']}")

                    # Per-item qty
                    st.markdown("**Dispatch Quantities**")
                    cd_items = []
                    pending_lines = []

                    hc0,hc1,hc2,hc3,hc4,hc5 = st.columns([4,2,2,2,2,3])
                    hc0.markdown("**Item**"); hc1.markdown("**Unit**")
                    hc2.markdown("**Original Qty**"); hc3.markdown("**Dispatch Qty**")
                    hc4.markdown("**Pending**"); hc5.markdown("**Remarks**")

                    for i, it in enumerate(orig_items):
                        orig_qty = float(it.get("qty", 0))
                        unit     = it.get("unit", "SQFT")
                        desc     = it.get("desc", f"Item {i+1}")
                        ic0,ic1,ic2,ic3,ic4,ic5 = st.columns([4,2,2,2,2,3])
                        ic0.write(desc); ic1.write(unit); ic2.write(format_inr(orig_qty))
                        disp_qty = ic3.number_input(
                            "", min_value=0.0, max_value=orig_qty,
                            value=orig_qty, step=0.01,
                            key=f"cd_qty_{doc['doc_id']}_{i}",
                            label_visibility="collapsed")
                        pending = round(orig_qty - disp_qty, 3)
                        if pending > 0:
                            ic4.markdown(f"⏳ **{format_inr(pending)}**")
                            pending_lines.append(f"{desc}: {format_inr(pending)} {unit}")
                        else:
                            ic4.write("—")
                        rmk = ic5.text_input("", key=f"cd_rmk_{doc['doc_id']}_{i}",
                                             label_visibility="collapsed",
                                             placeholder="Remarks")
                        cd_items.append({
                            "desc": desc, "hsn": it.get("hsn",""),
                            "qty": disp_qty, "unit": unit,
                            "area_per_piece": it.get("area_per_piece",0),
                            "pieces": it.get("pieces",0),
                            "remarks": rmk, "rate": 0,
                        })

                    if pending_lines:
                        st.warning("⏳ **Pending:** " + " | ".join(pending_lines))

                    # Preview
                    if st.button("👁️ Preview Challan", key=f"cd_preview_{doc['doc_id']}"):
                        preview_data = {
                            "doc_id":           f"PREVIEW",
                            "doc_type":         "Challan",
                            "client_name":      d.get("client_name",""),
                            "project_name":     d.get("project_name",""),
                            "billing_address":  d.get("billing_address",""),
                            "delivery_address": d.get("delivery_address",""),
                            "doc_date":         str(date.today()),
                            "vehicle_no":       cd_vno,
                            "transporter_name": cd_tsp,
                            "transport_mode":   cd_mode,
                            "notes":            cd_purp,
                            "items":            [it for it in cd_items if float(it["qty"]) > 0],
                        }
                        st.components.v1.html(build_html_challan(preview_data), height=700, scrolling=True)

                    st.markdown("---")
                    btn_save, btn_cancel = st.columns(2)
                    if btn_save.button("💾 Save as Draft", type="primary",
                                       key=f"cd_save_{doc['doc_id']}", use_container_width=True):
                        dispatch_id = generate_dispatch_id()
                        save_dispatch({
                            "dispatch_id":      dispatch_id,
                            "source_doc_id":    doc["doc_id"],
                            "status":           "Draft",
                            "client_name":      d.get("client_name",""),
                            "project_name":     d.get("project_name",""),
                            "billing_address":  d.get("billing_address",""),
                            "delivery_address": d.get("delivery_address",""),
                            "vehicle_no":       cd_vno,
                            "transporter_name": cd_tsp,
                            "transport_mode":   cd_mode,
                            "purpose":          cd_purp,
                            "items":            [it for it in cd_items if float(it["qty"]) > 0],
                            "created_at":       datetime.now().isoformat(),
                            "finalized_at":     "",
                        })
                        st.session_state.pop(cd_open_key, None)
                        st.success(f"✅ Dispatch **{dispatch_id}** saved as Draft — go to **📦 Dispatches** tab to continue editing & finalize.")
                        st.session_state["nav"] = "📦 Dispatches"
                        st.rerun()

                    if btn_cancel.button("✖ Cancel", key=f"cd_cancel_{doc['doc_id']}",
                                         use_container_width=True):
                        st.session_state.pop(cd_open_key, None)
                        st.rerun()

# ── Dispatches tab ─────────────────────────────────────────────────────────────

def dispatches_tab():
    st.subheader("📦 Dispatches")
    st.caption("Create dispatches from approved documents (All Documents tab). Edit quantities over multiple days, then finalize to generate Challan PDF.")

    dispatches = get_dispatches()
    if not dispatches:
        st.info("No dispatches yet. Open an **Approved** document in **📂 All Documents** and click **🚚 Create Dispatch**.")
        return

    fs = st.selectbox("Filter by Status", ["All", "Draft", "Finalized"], key="disp_filter")
    filtered = [d for d in reversed(dispatches) if fs == "All" or d.get("status") == fs]
    if not filtered:
        st.info(f"No {fs} dispatches.")
        return

    icons = {"Draft": "🟡", "Finalized": "🟢"}

    for disp in filtered:
        icon  = icons.get(disp.get("status",""), "⚪")
        label = (f"{icon} {disp['dispatch_id']}  |  "
                 f"{disp.get('client_name','')}  |  "
                 f"{disp.get('project_name','')}  |  "
                 f"Source: {disp.get('source_doc_id','')}  |  "
                 f"{'Finalized' if disp.get('status')=='Finalized' else 'Draft'}")
        with st.expander(label, expanded=(disp.get("status")=="Draft")):
            i1, i2, i3, i4 = st.columns(4)
            i1.write(f"**Status:** {disp.get('status','')}")
            i2.write(f"**Created:** {str(disp.get('created_at',''))[:10]}")
            i3.write(f"**Vehicle:** {disp.get('vehicle_no','') or '—'}")
            i4.write(f"**Transporter:** {disp.get('transporter_name','') or '—'}")

            # ── FINALIZED: just show re-download button ──────────────────────
            if disp.get("status") == "Finalized":
                if disp.get("finalized_at"):
                    st.caption(f"Finalized: {str(disp['finalized_at'])[:16]}")
                if st.button("📄 Re-download Challan PDF",
                             key=f"redl_{disp['dispatch_id']}", type="primary"):
                    challan_data = {
                        "doc_id":           disp["dispatch_id"],
                        "doc_type":         "Challan",
                        "client_name":      disp.get("client_name",""),
                        "project_name":     disp.get("project_name",""),
                        "billing_address":  disp.get("billing_address",""),
                        "delivery_address": disp.get("delivery_address",""),
                        "doc_date":         str(disp.get("finalized_at","") or disp.get("created_at",""))[:10],
                        "vehicle_no":       disp.get("vehicle_no",""),
                        "transporter_name": disp.get("transporter_name",""),
                        "transport_mode":   disp.get("transport_mode","Road"),
                        "notes":            disp.get("purpose","Supply"),
                        "items":            disp.get("items",[]),
                    }
                    pdf = make_pdf(build_html_challan(challan_data))
                    st.download_button(
                        "⬇️ Save Challan PDF", pdf,
                        file_name=f"{disp['dispatch_id']}_{disp.get('client_name','').replace(' ','_')}.pdf",
                        key=f"save_redl_{disp['dispatch_id']}",
                    )
                continue  # skip edit form for finalized

            # ── DRAFT: full edit form ────────────────────────────────────────
            st.markdown("---")
            st.markdown("##### ✏️ Edit Dispatch Details")

            e1, e2, e3, e4 = st.columns(4)
            e_vno  = e1.text_input("Vehicle No.",
                                   value=disp.get("vehicle_no",""),
                                   key=f"e_vno_{disp['dispatch_id']}")
            e_tsp  = e2.text_input("Transporter",
                                   value=disp.get("transporter_name",""),
                                   key=f"e_tsp_{disp['dispatch_id']}")
            p_opts = ["Supply","Job Work","Sales Return","Exhibition / Fairs","Others"]
            p_idx  = p_opts.index(disp.get("purpose","Supply")) if disp.get("purpose","Supply") in p_opts else 0
            e_purp = e3.selectbox("Purpose", p_opts, index=p_idx,
                                  key=f"e_purp_{disp['dispatch_id']}")
            m_opts = ["Road","Rail","Air","Ship"]
            m_idx  = m_opts.index(disp.get("transport_mode","Road")) if disp.get("transport_mode","Road") in m_opts else 0
            e_mode = e4.selectbox("Mode", m_opts, index=m_idx,
                                  key=f"e_mode_{disp['dispatch_id']}")

            # ── Items from source document ───────────────────────────────────
            st.markdown("---")
            st.markdown("**Dispatch Quantities** — adjust per item; partial dispatch shows pending remainder.")

            src_doc   = get_document(disp.get("source_doc_id","")) if disp.get("source_doc_id") else None
            src_items = src_doc.get("items",[]) if src_doc else []

            # Current saved qtys / remarks keyed by description
            curr_by_desc = {it["desc"]: it for it in disp.get("items",[])}

            hc0,hc1,hc2,hc3,hc4,hc5 = st.columns([4,2,2,2,2,3])
            hc0.markdown("**Item**"); hc1.markdown("**Unit**")
            hc2.markdown("**Original Qty**"); hc3.markdown("**Dispatch Qty**")
            hc4.markdown("**Pending**"); hc5.markdown("**Remarks**")

            new_items      = []
            pending_lines  = []

            for i, it in enumerate(src_items):
                orig_qty = float(it.get("qty", 0))
                unit     = it.get("unit", "SQFT")
                desc     = it.get("desc", f"Item {i+1}")
                curr     = curr_by_desc.get(desc, {})
                saved_q  = float(curr.get("qty", orig_qty))
                saved_r  = curr.get("remarks","")

                ic0,ic1,ic2,ic3,ic4,ic5 = st.columns([4,2,2,2,2,3])
                ic0.write(desc)
                ic1.write(unit)
                ic2.write(format_inr(orig_qty))
                disp_qty = ic3.number_input(
                    "", min_value=0.0, max_value=orig_qty,
                    value=min(saved_q, orig_qty), step=0.01,
                    key=f"e_qty_{disp['dispatch_id']}_{i}",
                    label_visibility="collapsed")
                pending = round(orig_qty - disp_qty, 3)
                if pending > 0:
                    ic4.markdown(f"⏳ **{format_inr(pending)}**")
                    pending_lines.append(f"{desc}: {format_inr(pending)} {unit}")
                else:
                    ic4.write("—")
                rmk = ic5.text_input(
                    "", value=saved_r,
                    key=f"e_rmk_{disp['dispatch_id']}_{i}",
                    label_visibility="collapsed", placeholder="Remarks")

                new_items.append({
                    "desc": desc, "hsn": it.get("hsn",""),
                    "qty": disp_qty, "unit": unit,
                    "area_per_piece": it.get("area_per_piece",0),
                    "pieces": it.get("pieces",0),
                    "remarks": rmk, "rate": 0,
                })

            # Also preserve any manually-added items already in dispatch that are
            # NOT in the source document (custom items added previously)
            src_descs = {it.get("desc","") for it in src_items}
            for it in disp.get("items",[]):
                if it.get("desc","") not in src_descs:
                    new_items.append(it)

            # ── Add custom item ──────────────────────────────────────────────
            with st.expander("➕ Add Custom / Extra Item"):
                ci1, ci2, ci3, ci4 = st.columns([4,2,2,3])
                ci_desc = ci1.text_input("Description", key=f"ci_desc_{disp['dispatch_id']}")
                ci_qty  = ci2.number_input("Qty", min_value=0.0, step=0.01,
                                           key=f"ci_qty_{disp['dispatch_id']}")
                ci_unit = ci3.selectbox("Unit", ["SQFT","RFT","NOS","KG","SET"],
                                        key=f"ci_unit_{disp['dispatch_id']}")
                ci_rmk  = ci4.text_input("Remarks", key=f"ci_rmk_{disp['dispatch_id']}")
                if st.button("Add Item", key=f"ci_add_{disp['dispatch_id']}"):
                    if ci_desc and ci_qty > 0:
                        # Save dispatch immediately with the new item
                        updated = dict(disp)
                        updated["vehicle_no"]       = e_vno
                        updated["transporter_name"] = e_tsp
                        updated["purpose"]          = e_purp
                        updated["transport_mode"]   = e_mode
                        add_items = [it for it in new_items if float(it.get("qty",0)) > 0]
                        add_items.append({
                            "desc": ci_desc, "qty": ci_qty, "unit": ci_unit,
                            "remarks": ci_rmk, "hsn":"","area_per_piece":0,
                            "pieces":0,"rate":0,
                        })
                        updated["items"] = add_items
                        save_dispatch(updated, edit_id=disp["dispatch_id"])
                        st.success(f"Added: {ci_desc}")
                        st.rerun()

            if pending_lines:
                st.warning("⏳ **Pending (still in casting/processing):**\n" +
                           "\n".join(f"• {p}" for p in pending_lines))

            # ── Action buttons ───────────────────────────────────────────────
            st.markdown("---")
            b1, b2, b3 = st.columns(3)

            if b1.button("💾 Save Draft", key=f"save_d_{disp['dispatch_id']}", use_container_width=True):
                updated = dict(disp)
                updated["vehicle_no"]       = e_vno
                updated["transporter_name"] = e_tsp
                updated["purpose"]          = e_purp
                updated["transport_mode"]   = e_mode
                updated["items"]            = [it for it in new_items if float(it.get("qty",0)) > 0]
                save_dispatch(updated, edit_id=disp["dispatch_id"])
                st.success("✅ Draft saved — come back any time to continue editing.")
                st.rerun()

            if b2.button("✅ Finalize & Generate PDF",
                         key=f"fin_d_{disp['dispatch_id']}", type="primary",
                         use_container_width=True):
                finalized_items = [it for it in new_items if float(it.get("qty",0)) > 0]
                if not finalized_items:
                    st.error("No items with qty > 0 to dispatch.")
                else:
                    updated = dict(disp)
                    updated["vehicle_no"]       = e_vno
                    updated["transporter_name"] = e_tsp
                    updated["purpose"]          = e_purp
                    updated["transport_mode"]   = e_mode
                    updated["items"]            = finalized_items
                    updated["status"]           = "Finalized"
                    updated["finalized_at"]     = datetime.now().isoformat()
                    save_dispatch(updated, edit_id=disp["dispatch_id"])

                    challan_data = {
                        "doc_id":           disp["dispatch_id"],
                        "doc_type":         "Challan",
                        "client_name":      disp.get("client_name",""),
                        "project_name":     disp.get("project_name",""),
                        "billing_address":  disp.get("billing_address",""),
                        "delivery_address": disp.get("delivery_address",""),
                        "doc_date":         str(date.today()),
                        "vehicle_no":       e_vno,
                        "transporter_name": e_tsp,
                        "transport_mode":   e_mode,
                        "notes":            e_purp,
                        "items":            finalized_items,
                    }
                    pdf = make_pdf(build_html_challan(challan_data))
                    st.download_button(
                        "⬇️ Download Challan PDF", pdf,
                        file_name=f"{disp['dispatch_id']}_{disp.get('client_name','').replace(' ','_')}.pdf",
                        key=f"dl_fin_{disp['dispatch_id']}",
                        type="primary",
                    )
                    st.success("✅ Finalized! Use the button above to download the Challan PDF.")

            if b3.button("🗑️ Delete Draft",
                         key=f"del_d_{disp['dispatch_id']}", use_container_width=True):
                st.session_state[f"del_confirm_{disp['dispatch_id']}"] = True

            if st.session_state.get(f"del_confirm_{disp['dispatch_id']}"):
                st.warning(f"⚠️ Delete **{disp['dispatch_id']}** permanently?")
                dc1, dc2 = st.columns(2)
                if dc1.button("Yes, Delete", key=f"del_yes_{disp['dispatch_id']}", type="primary"):
                    delete_dispatch(disp["dispatch_id"])
                    st.session_state.pop(f"del_confirm_{disp['dispatch_id']}", None)
                    st.success("Deleted.")
                    st.rerun()
                if dc2.button("Cancel", key=f"del_no_{disp['dispatch_id']}"):
                    st.session_state.pop(f"del_confirm_{disp['dispatch_id']}", None)
                    st.rerun()

# ── Settings tab ───────────────────────────────────────────────────────────────

def work_orders_tab():
    st.subheader("Work Orders")
    wos = get_work_orders()

    # ── AI Extract from BOQ ──
    with st.expander("🤖 Extract from BOQ Image / PDF", expanded=False):
        st.caption("Upload a BOQ screenshot or PDF — Claude will read it and pre-fill the Work Order form.")
        boq_file = st.file_uploader("Upload BOQ", type=["png", "jpg", "jpeg", "pdf"], key="boq_upload")
        if boq_file and st.button("✨ Extract & Pre-fill", type="primary", key="boq_extract"):
            with st.spinner("Reading document with AI..."):
                try:
                    extracted = extract_boq_from_file(boq_file)
                    st.session_state["boq_extracted"] = extracted
                    st.success(f"✅ Extracted {len(extracted.get('items', []))} items — scroll down to the form, fields are pre-filled.")
                    st.json(extracted)
                except Exception as e:
                    st.error(f"Extraction failed: {e}")

    with st.expander("➕ Create / Edit Work Order", expanded=not bool(wos)):
        edit_wo = st.selectbox("Edit existing", ["— New Work Order —"] + [f"{w['wo_id']} — {w['project_name']}" for w in wos], key="edit_wo_sel")
        ew = next((w for w in wos if f"{w['wo_id']} — {w['project_name']}" == edit_wo), None) if edit_wo != "— New Work Order —" else None

        # Include WO ID in widget keys so switching WOs forces fresh values
        wok = ew["wo_id"].replace("-", "") if ew else "new"

        # Pick up AI-extracted data if available
        ai = st.session_state.pop("boq_extracted", None)

        clients = get_clients()
        wo_id      = st.text_input("Work Order ID", value=ew["wo_id"] if ew else generate_wo_id(), key=f"wo_id_{wok}")
        wo_client  = st.selectbox("Client", ["— select —"] + [c["name"] for c in clients],
                                  index=([c["name"] for c in clients].index(ew["client_name"]) + 1) if ew and ew.get("client_name") in [c["name"] for c in clients] else 0,
                                  key=f"wo_client_{wok}")
        default_project = (ai["project_name"] if ai else None) or (ew["project_name"] if ew else "")
        default_scope   = (ai["scope"]        if ai else None) or (ew["scope"]        if ew else "")
        wo_project = st.text_input("Project Name", value=default_project, key=f"wo_project_{wok}")
        wo_scope   = st.text_area("Scope of Work", value=default_scope, height=80, key=f"wo_scope_{wok}")
        wo_status  = st.selectbox("Status", ["Active", "Completed", "On Hold"],
                                  index=["Active","Completed","On Hold"].index(ew["status"]) if ew and ew.get("status") in ["Active","Completed","On Hold"] else 0,
                                  key=f"wo_status_{wok}")

        # Work order items — enter directly, auto-sync to Items catalog on save
        st.markdown("**Items & Rates**")
        st.caption("Enter items directly here — they will be automatically added to the Items catalog when you save.")
        ex_items = ai["items"] if ai else (ew["items"] if ew else [])

        wo_item_count = st.number_input("Number of items", 1, 30, value=max(1, len(ex_items)), step=1, key=f"wo_ic_{wok}")
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
                                   key=f"wo_desc_{wok}_{i}", label_visibility="collapsed",
                                   placeholder="e.g. GFRC RB-COL-2524")
            w_unit_opts = ["SQFT", "RFT", "SQM", "PC", "KG"]
            def_unit = ei.get("unit", "SQFT")
            w_unit = c1.selectbox("", w_unit_opts,
                                  index=w_unit_opts.index(def_unit) if def_unit in w_unit_opts else 0,
                                  key=f"wo_unit_{wok}_{i}", label_visibility="collapsed")

            w_area_per_pc  = c2.number_input("", value=float(ei.get("area_per_piece", 0.0)),
                                             min_value=0.0, key=f"wo_app_{wok}_{i}", label_visibility="collapsed",
                                             help="Area per piece (sqft/pc)")
            w_pieces       = c3.number_input("", value=float(ei.get("pieces", 0.0)),
                                             min_value=0.0, key=f"wo_pcs_{wok}_{i}", label_visibility="collapsed",
                                             help="Number of pieces")
            if w_area_per_pc > 0 and w_pieces > 0:
                w_qty = round(w_area_per_pc * w_pieces, 3)
                c4.markdown(f"**{format_inr(w_qty)}**")
            else:
                w_qty = c4.number_input("", value=float(ei.get("qty", 0)), min_value=0.0,
                                        key=f"wo_qty_{wok}_{i}", label_visibility="collapsed")

            w_supply_rate  = c5.number_input("", value=float(ei.get("supply_rate", ei.get("rate", 0))),
                                             min_value=0.0, key=f"wo_srate_{wok}_{i}", label_visibility="collapsed")
            w_install_rate = c6.number_input("", value=float(ei.get("installation_rate", 0)),
                                             min_value=0.0, key=f"wo_irate_{wok}_{i}", label_visibility="collapsed")
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

        adv_pct  = mc2.number_input("", value=float(_ms_val("Advance","percent",10)), min_value=0.0, max_value=100.0, key=f"ms_adv_pct_{wok}", label_visibility="collapsed")
        adv_st   = mc3.selectbox("", STATUSES, index=STATUSES.index(_ms_val("Advance","status","Pending")), key=f"ms_adv_st_{wok}", label_visibility="collapsed")
        mc1.markdown("Advance")

        sup_pct  = mc2.number_input("", value=float(_ms_val("Supply","percent",75)), min_value=0.0, max_value=100.0, key=f"ms_sup_pct_{wok}", label_visibility="collapsed")
        sup_st   = mc3.selectbox("", STATUSES, index=STATUSES.index(_ms_val("Supply","status","Pending")), key=f"ms_sup_st_{wok}", label_visibility="collapsed")
        mc1.markdown("Supply")

        ins_pct  = mc2.number_input("", value=float(_ms_val("Installation","percent",15)), min_value=0.0, max_value=100.0, key=f"ms_ins_pct_{wok}", label_visibility="collapsed")
        ins_st   = mc3.selectbox("", STATUSES, index=STATUSES.index(_ms_val("Installation","status","Pending")), key=f"ms_ins_st_{wok}", label_visibility="collapsed")
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
                                        value=max(1, len(ex_wo_terms)), step=1, key=f"wo_tc_{wok}")
        wo_terms = [
            st.text_input(f"Term {j+1}", value=ex_wo_terms[j] if j < len(ex_wo_terms) else "",
                          key=f"wo_term_{wok}_{j}")
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
            cce1, cce2 = st.columns(2)
            ccontact = cce1.text_input("Contact Name", value=ec.get("contact_name", ""), placeholder="e.g. Mr. Sharma")
            cemail = cce2.text_input("Email", value=ec.get("email", ""), placeholder="invoices@client.com")
            cb = st.text_area("Billing Address", value=ec.get("billing_address", ""), height=80)
            cd = st.text_area("Delivery Address", value=ec.get("delivery_address", ""), height=80)
            cg = st.text_input("GST Number", value=ec.get("gst_number", ""))
            cp = st.text_input("Payment Terms", value=ec.get("payment_terms", ""), placeholder="e.g. 10% Advance, 75% Before Dispatch")
            cno = st.text_input("Notes", value=ec.get("notes", ""))
            if st.button("💾 Save Client"):
                idx = next((i for i, c in enumerate(clients) if c["name"] == edit_c), None)
                save_client({"name": cn, "billing_address": cb, "delivery_address": cd,
                             "gst_number": cg, "payment_terms": cp, "notes": cno,
                             "email": cemail, "contact_name": ccontact}, edit_idx=idx)
                st.success(f"Client '{cn}' saved.")
                st.rerun()

        if clients:
            st.markdown("---")
            for c in clients:
                with st.expander(f"👤 {c['name']}"):
                    if c.get("contact_name"):
                        st.write(f"**Contact:** {c['contact_name']}")
                    if c.get("email"):
                        st.write(f"**Email:** {c['email']}")
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

    st.markdown("---")
    st.subheader("🏦 Bank Details")
    st.caption("These appear on every PDF. Edit and click Save.")
    cfg = get_settings()
    col_a, col_b = st.columns(2)
    with col_a:
        s_bank   = st.text_input("Bank Name",       value=cfg.get("bank_name",    DEFAULT_BANK["bank_name"]))
        s_acname = st.text_input("Account Name",    value=cfg.get("account_name", DEFAULT_BANK["account_name"]))
        s_acno   = st.text_input("Account Number",  value=cfg.get("account_no",   DEFAULT_BANK["account_no"]))
    with col_b:
        s_ifsc   = st.text_input("IFSC Code",       value=cfg.get("ifsc",         DEFAULT_BANK["ifsc"]))
        s_branch = st.text_input("Branch",          value=cfg.get("branch",       DEFAULT_BANK["branch"]))
        s_type   = st.text_input("Account Type",    value=cfg.get("account_type", DEFAULT_BANK["account_type"]))
    if st.button("💾 Save Bank Details", type="primary"):
        save_settings({
            "bank_name": s_bank, "account_name": s_acname, "account_no": s_acno,
            "ifsc": s_ifsc, "branch": s_branch, "account_type": s_type,
        })
        st.success("Bank details saved — will appear on all future PDFs.")

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

    nav = st.radio("", ["📄 New Document", "📂 All Documents", "📦 Dispatches", "📋 Work Orders", "🗂️ Clients & Items", "⚙️ Settings"],
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

        b1, b2 = st.columns(2)
        with b1:
            if st.button("💾 Save Draft", type="primary", use_container_width=True):
                doc_id = edit_id or generate_doc_id(form_data["doc_type"], form_data.get("doc_code", ""))
                form_data["doc_id"] = doc_id
                if prefill:
                    form_data["created_at"] = prefill.get("created_at", "")
                save_document(form_data, edit_id=edit_id)
                if form_data.get("wo_id") and form_data.get("wo_milestone_idx") is not None:
                    update_wo_milestone(form_data["wo_id"], form_data["wo_milestone_idx"], "Billed")
                st.success(f"Saved: **{doc_id}**")
                if edit_id:
                    st.query_params.clear()

            st.markdown("---")
            auto_new_id = generate_doc_id(form_data["doc_type"], form_data.get("doc_code", ""))
            new_name = st.text_input("New doc ID (editable)", value=auto_new_id, key="new_doc_id")
            if st.button("📋 Save as New", use_container_width=True):
                form_data["doc_id"] = new_name.strip() or auto_new_id
                form_data.pop("created_at", None)
                save_document(form_data)
                st.success(f"Saved as new: **{form_data['doc_id']}**")
                st.query_params.clear()

        with b2:
            if st.button("👁️ Preview", use_container_width=True):
                form_data["doc_id"] = edit_id or "PREVIEW"
                html = build_html(form_data, watermark=True)
                st.components.v1.html(html, height=900, scrolling=True)
            if st.button("🔄 Clear Form", use_container_width=True):
                for k in list(st.session_state.keys()):
                    if k.startswith("active_terms_"):
                        del st.session_state[k]
                st.query_params.clear()
                st.rerun()

    elif nav == "📂 All Documents":
        documents_tab()

    elif nav == "📦 Dispatches":
        dispatches_tab()

    elif nav == "📋 Work Orders":
        work_orders_tab()

    elif nav == "🗂️ Clients & Items":
        clients_items_tab()

    elif nav == "⚙️ Settings":
        settings_tab()


if __name__ == "__main__":
    main()
