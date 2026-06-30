"""
Survey Sync Receiver — deploy this on your nulm-susv.in VPS.

Field devices (running the offline survey app) POST their records here
once they have internet. Each vendor record lands in its own folder:

    /root/usvms_survey/uploads/<vendor_id>_<trader_name>/
        data.json
        vendor_photo.jpg
        vending_photo.jpg
        aadhar_doc.jpg
        voter_id_doc.jpg
        bank_passbook_doc.jpg
        pds_card_doc.jpg

Run with gunicorn behind nginx, same pattern as your USVMS deployment.
Auth: simple shared API key via header `X-API-Key` (set SURVEY_API_KEY env
var on the VPS, and the same value in the field app's vps_sync.py).
"""
import os
import json
import csv
import io
import zipfile
import re
import fcntl
from functools import wraps
from flask import Flask, request, jsonify, render_template_string, Response, send_file

app = Flask(__name__)

UPLOAD_ROOT = os.environ.get("SURVEY_UPLOAD_ROOT", "/root/usvms_survey/uploads")
API_KEY = os.environ.get("SURVEY_API_KEY", "change-this-key")
DASHBOARD_USER = os.environ.get("SURVEY_DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.environ.get("SURVEY_DASHBOARD_PASS", "change-this-password")

os.makedirs(UPLOAD_ROOT, exist_ok=True)

COUNTER_FILE = os.path.join(os.path.dirname(UPLOAD_ROOT.rstrip("/")) or "/root/usvms_survey", "vendor_id_counters.json")

# Known ULB -> 3-4 letter short code. Extend this as you cover more ULBs.
ULB_SHORT_CODES = {
    "tiruvannamalai": "TVM",
    "sankarnagar": "SKN",
    "tirunelveli municipal corporation": "TVL",
    "ambasamudram": "AMB",
    "vickramasingapuram": "VKS",
    "kalakad": "KLK",
    "tambaram municipal corporation": "TMB",
    "chengalpattu": "CGP",
    "madurantakam": "MDK",
    "maraimalai nagar": "MMN",
    "nandivaram-guduvancheri": "NGV",
    "kancheepuram municipal corporation": "KPM",
    "kundrathur": "KND",
    "mangadu": "MGD",
}


def get_ulb_short_code(ulb_name):
    key = (ulb_name or "").strip().lower()
    if key in ULB_SHORT_CODES:
        return ULB_SHORT_CODES[key]
    # fallback: first 3-4 alphabetic characters, uppercased
    letters = re.sub(r"[^A-Za-z]", "", ulb_name or "")
    return (letters[:4] or "ULB").upper()


def get_next_vendor_code(ulb_name):
    """
    File-locked sequential counter per ULB short code, e.g. TVM0001, TVM0002.
    Safe across concurrent gunicorn workers via fcntl exclusive lock.
    """
    short_code = get_ulb_short_code(ulb_name)
    os.makedirs(os.path.dirname(COUNTER_FILE), exist_ok=True)

    with open(COUNTER_FILE, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            content = fh.read()
            counters = json.loads(content) if content.strip() else {}

            next_num = counters.get(short_code, 0) + 1
            counters[short_code] = next_num

            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(counters, indent=2))
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)

    return f"{short_code}{next_num:04d}"


def check_auth():
    return request.headers.get("X-API-Key") == API_KEY


def check_dashboard_auth(username, password):
    return username == DASHBOARD_USER and password == DASHBOARD_PASS


def requires_dashboard_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_dashboard_auth(auth.username, auth.password):
            return Response(
                "Login required", 401,
                {"WWW-Authenticate": 'Basic realm="Survey Dashboard"'},
            )
        return f(*args, **kwargs)
    return decorated


@app.route("/api/survey/upload", methods=["POST"])
def upload():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401

    device_vendor_id = request.form.get("vendor_id")
    trader_name = request.form.get("trader_name", "vendor")
    record_json = request.form.get("record_json", "{}")

    if not device_vendor_id:
        return jsonify({"error": "vendor_id required"}), 400

    try:
        parsed = json.loads(record_json)
    except json.JSONDecodeError:
        parsed = {"raw": record_json}

    ulb_name = parsed.get("ulb_name", "")
    vendor_code = get_next_vendor_code(ulb_name)
    parsed["vendor_code"] = vendor_code
    parsed["device_vendor_id"] = device_vendor_id

    safe_name = "".join(c for c in trader_name if c.isalnum() or c in " _-").strip() or "vendor"
    folder = os.path.join(UPLOAD_ROOT, f"{vendor_code}_{safe_name}")
    os.makedirs(folder, exist_ok=True)

    with open(os.path.join(folder, "data.json"), "w", encoding="utf-8") as fh:
        json.dump(parsed, fh, ensure_ascii=False, indent=2)

    saved_files = []
    for field_name, file_storage in request.files.items():
        if file_storage and file_storage.filename:
            ext = os.path.splitext(file_storage.filename)[1] or ".jpg"
            dest = os.path.join(folder, f"{field_name}{ext}")
            file_storage.save(dest)
            saved_files.append(field_name)

    return jsonify({"status": "ok", "vendor_id": device_vendor_id, "vendor_code": vendor_code, "files_received": saved_files})


@app.route("/api/survey/health")
def health():
    return jsonify({"status": "up"})


DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>USVMS Survey Dashboard</title>
<style>
body { font-family: -apple-system, Roboto, sans-serif; margin:0; background:#f5f6f8; color:#222; }
.topbar { background:#1f6f43; color:#fff; padding:14px 16px; }
.topbar h1 { margin:0; font-size:1.2em; }
.filters { padding:12px 16px; background:#fff; border-bottom:1px solid #ddd; display:flex; gap:10px; flex-wrap:wrap; }
.filters input, .filters select { padding:6px; border:1px solid #ccc; border-radius:5px; }
table { width:100%; border-collapse:collapse; background:#fff; }
th, td { border:1px solid #ddd; padding:8px; text-align:left; font-size:0.9em; }
th { background:#eee; position:sticky; top:0; }
a { color:#1f6f43; }
.count { padding:8px 16px; color:#555; font-size:0.9em; }
</style></head>
<body>
<div class="topbar"><h1>USVMS Street Vendor Survey — Synced Records</h1></div>
<form class="filters" method="get">
  <input type="text" name="surveyor" placeholder="Surveyor name" value="{{ filters.surveyor }}">
  <input type="text" name="district" placeholder="District" value="{{ filters.district }}">
  <input type="text" name="ulb" placeholder="ULB name" value="{{ filters.ulb }}">
  <input type="text" name="q" placeholder="Search trader/mobile" value="{{ filters.q }}">
  <button type="submit">Filter</button>
  <a href="/api/survey/dashboard">Clear</a>
  <a href="/api/survey/export.csv?{{ request.query_string.decode() }}">⬇ Export CSV</a>
  <a href="/api/survey/export.zip?{{ request.query_string.decode() }}">⬇ Download All (ZIP)</a>
</form>
<div class="count">{{ records|length }} record(s)</div>
<table>
<tr><th>Vendor Code</th><th>Surveyor</th><th>Trader Name</th><th>District</th><th>ULB</th><th>Mobile</th><th>Files</th></tr>
{% for r in records %}
<tr>
  <td>{{ r.data.get('vendor_code', r.folder) }}</td>
  <td>{{ r.data.get('surveyor_name','') }}</td>
  <td>{{ r.data.get('trader_name','') }}</td>
  <td>{{ r.data.get('district','') }}</td>
  <td>{{ r.data.get('ulb_name','') }}</td>
  <td>{{ r.data.get('mobile_no','') }}</td>
  <td><a href="/api/survey/record/{{ r.folder }}">view</a></td>
</tr>
{% endfor %}
</table>
</body></html>
"""

RECORD_TEMPLATE = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Record {{ folder }}</title>
<style>
body { font-family: -apple-system, Roboto, sans-serif; margin:0; background:#f5f6f8; color:#222; }
.topbar { background:#1f6f43; color:#fff; padding:14px 16px; }
.content { max-width:700px; margin:16px auto; background:#fff; padding:16px; border-radius:8px; }
table { width:100%; border-collapse:collapse; }
td { padding:6px; border-bottom:1px solid #eee; font-size:0.9em; }
td.k { font-weight:600; width:200px; color:#555; }
img { max-width:200px; margin:6px; border-radius:6px; border:1px solid #ddd; }
a.back { color:#fff; }
</style></head>
<body>
<div class="topbar"><a class="back" href="/api/survey/dashboard">&larr; Back to all records</a></div>
<div class="content">
<h2>{{ folder }}</h2>
<table>
{% for k, v in data.items() %}
<tr><td class="k">{{ k }}</td><td>{{ v }}</td></tr>
{% endfor %}
</table>
<h3>Files</h3>
{% for f in files %}
  {% if f.endswith('.jpg') or f.endswith('.jpeg') or f.endswith('.png') %}
    <a href="/api/survey/file/{{ folder }}/{{ f }}"><img src="/api/survey/file/{{ folder }}/{{ f }}"></a>
  {% else %}
    <p><a href="/api/survey/file/{{ folder }}/{{ f }}">{{ f }}</a></p>
  {% endif %}
{% endfor %}
</div>
</body></html>
"""


def load_all_records():
    records = []
    if not os.path.isdir(UPLOAD_ROOT):
        return records
    for folder in sorted(os.listdir(UPLOAD_ROOT), reverse=True):
        folder_path = os.path.join(UPLOAD_ROOT, folder)
        data_path = os.path.join(folder_path, "data.json")
        if os.path.isfile(data_path):
            try:
                with open(data_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (json.JSONDecodeError, OSError):
                data = {}
            records.append({"folder": folder, "data": data})
    return records


def filter_records(records, surveyor, district, ulb, q):
    def matches(r):
        d = r["data"]
        if surveyor and surveyor not in str(d.get("surveyor_name", "")).lower():
            return False
        if district and district not in str(d.get("district", "")).lower():
            return False
        if ulb and ulb not in str(d.get("ulb_name", "")).lower():
            return False
        if q and q not in str(d.get("trader_name", "")).lower() and q not in str(d.get("mobile_no", "")).lower():
            return False
        return True
    return [r for r in records if matches(r)]


@app.route("/api/survey/dashboard")
@requires_dashboard_auth
def dashboard():
    surveyor = request.args.get("surveyor", "").strip().lower()
    district = request.args.get("district", "").strip().lower()
    ulb = request.args.get("ulb", "").strip().lower()
    q = request.args.get("q", "").strip().lower()

    records = load_all_records()
    filtered = filter_records(records, surveyor, district, ulb, q)

    return render_template_string(
        DASHBOARD_TEMPLATE,
        records=filtered,
        filters={"surveyor": surveyor, "district": district, "ulb": ulb, "q": q},
    )


@app.route("/api/survey/export.csv")
@requires_dashboard_auth
def export_csv():
    surveyor = request.args.get("surveyor", "").strip().lower()
    district = request.args.get("district", "").strip().lower()
    ulb = request.args.get("ulb", "").strip().lower()
    q = request.args.get("q", "").strip().lower()

    records = load_all_records()
    filtered = filter_records(records, surveyor, district, ulb, q)

    # collect every field name seen across all matched records, in first-seen order
    fieldnames = ["vendor_id"]
    seen = set(fieldnames)
    for r in filtered:
        for k in r["data"].keys():
            if k not in seen:
                fieldnames.append(k)
                seen.add(k)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in filtered:
        row = dict(r["data"])
        row["vendor_id"] = r["folder"]
        writer.writerow(row)

    csv_bytes = buf.getvalue().encode("utf-8-sig")  # BOM so Excel renders Tamil correctly
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=survey_records.csv"},
    )


@app.route("/api/survey/export.zip")
@requires_dashboard_auth
def export_zip():
    surveyor = request.args.get("surveyor", "").strip().lower()
    district = request.args.get("district", "").strip().lower()
    ulb = request.args.get("ulb", "").strip().lower()
    q = request.args.get("q", "").strip().lower()

    records = load_all_records()
    filtered = filter_records(records, surveyor, district, ulb, q)

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in filtered:
            folder_path = os.path.join(UPLOAD_ROOT, r["folder"])
            if not os.path.isdir(folder_path):
                continue
            for fname in os.listdir(folder_path):
                full_path = os.path.join(folder_path, fname)
                if os.path.isfile(full_path):
                    zf.write(full_path, arcname=f"{r['folder']}/{fname}")
    mem.seek(0)

    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name="survey_records.zip",
    )


@app.route("/api/survey/record/<folder>")
@requires_dashboard_auth
def record_detail(folder):
    folder_path = os.path.join(UPLOAD_ROOT, folder)
    data_path = os.path.join(folder_path, "data.json")
    if not os.path.isfile(data_path):
        return "Record not found", 404
    with open(data_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    files = [f for f in os.listdir(folder_path) if f != "data.json"]
    return render_template_string(RECORD_TEMPLATE, folder=folder, data=data, files=files)


@app.route("/api/survey/file/<folder>/<filename>")
@requires_dashboard_auth
def serve_record_file(folder, filename):
    from flask import send_from_directory
    folder_path = os.path.join(UPLOAD_ROOT, folder)
    return send_from_directory(folder_path, filename)


if __name__ == "__main__":
    # dev only; in production run via gunicorn (see deploy instructions)
    app.run(host="0.0.0.0", port=6000)
