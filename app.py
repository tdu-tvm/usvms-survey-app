"""
Street Vendor Survey - Offline-first Flask app
================================================
Run this on a laptop/phone (Termux) in the field with NO internet.
All data + photos + documents are saved to local disk/SQLite immediately.
A background thread checks for internet every 60s and, when online,
auto-uploads any un-synced records (form data + all files) into a
per-vendor folder on Google Drive.

Start:
    python app.py
Then open http://127.0.0.1:5000 on the device's browser (works fully
offline, including camera + GPS, because it's served from localhost).
"""
import os
import io
import json
import socket
import sqlite3
import threading
import time
import uuid
from datetime import datetime

from flask import Flask, request, render_template, redirect, url_for, jsonify, send_from_directory

import drive_sync

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PHOTO_DIR = os.path.join(DATA_DIR, "photos")
DOC_DIR = os.path.join(DATA_DIR, "docs")
DB_PATH = os.path.join(DATA_DIR, "survey.db")

for d in (DATA_DIR, PHOTO_DIR, DOC_DIR):
    os.makedirs(d, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB per request

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

FIELDS = [
    "surveyor_name", "survey_no", "district", "ulb_name", "latitude", "longitude", "ward_no", "zone_no",
    "trader_name", "father_husband_name", "age", "gender", "caste", "house_type",
    "house_ownership", "marital_status", "family_members_json",
    "goods_sold", "business_type", "nature_of_business", "business_hours", "mobile_no",
    "differently_abled", "permanent_address", "association_member",
    "food_license", "voter_id_no", "ration_card_no", "aadhar_no",
    "bank_account_no", "ifsc_code", "bank_name_branch",
    "pmsvanidhi_loan", "upi_usage",
]

FILE_FIELDS = [
    "vendor_photo", "vending_photo", "aadhar_doc", "voter_id_doc",
    "bank_passbook_doc", "pds_card_doc",
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cols = ", ".join([f"{f} TEXT" for f in FIELDS])
    file_cols = ", ".join([f"{f} TEXT" for f in FILE_FIELDS])
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS vendors (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            synced INTEGER DEFAULT 0,
            drive_folder_id TEXT,
            vendor_code TEXT,
            {cols},
            {file_cols}
        )
    """)
    # safe migration for databases created before vendor_code existed
    existing_cols = [row["name"] for row in conn.execute("PRAGMA table_info(vendors)").fetchall()]
    if "vendor_code" not in existing_cols:
        conn.execute("ALTER TABLE vendors ADD COLUMN vendor_code TEXT")
    conn.commit()
    conn.close()


init_db()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_online(host="8.8.8.8", port=53, timeout=3):
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except OSError:
        return False


def save_upload(file_storage, dest_dir, vendor_id, label):
    if not file_storage or file_storage.filename == "":
        return None
    ext = os.path.splitext(file_storage.filename)[1].lower() or ".jpg"
    fname = f"{vendor_id}_{label}{ext}"
    path = os.path.join(dest_dir, fname)
    file_storage.save(path)
    return path


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/submit", methods=["POST"])
def submit():
    vendor_id = uuid.uuid4().hex[:10]
    data = {f: request.form.get(f, "") for f in FIELDS}

    # family members come in as repeated name[]/relation[] inputs
    names = request.form.getlist("family_name[]")
    relations = request.form.getlist("family_relation[]")
    data["family_members_json"] = json.dumps(
        [{"name": n, "relation": r} for n, r in zip(names, relations) if n.strip()]
    )

    file_paths = {
        "vendor_photo": save_upload(request.files.get("vendor_photo"), PHOTO_DIR, vendor_id, "vendor"),
        "vending_photo": save_upload(request.files.get("vending_photo"), PHOTO_DIR, vendor_id, "vending"),
        "aadhar_doc": save_upload(request.files.get("aadhar_doc"), DOC_DIR, vendor_id, "aadhar"),
        "voter_id_doc": save_upload(request.files.get("voter_id_doc"), DOC_DIR, vendor_id, "voterid"),
        "bank_passbook_doc": save_upload(request.files.get("bank_passbook_doc"), DOC_DIR, vendor_id, "passbook"),
        "pds_card_doc": save_upload(request.files.get("pds_card_doc"), DOC_DIR, vendor_id, "pdscard"),
    }

    conn = get_db()
    cols = ["id", "created_at"] + FIELDS + FILE_FIELDS
    placeholders = ", ".join(["?"] * len(cols))
    values = [vendor_id, datetime.now().isoformat()] + [data[f] for f in FIELDS] + [file_paths[f] for f in FILE_FIELDS]
    conn.execute(f"INSERT INTO vendors ({', '.join(cols)}) VALUES ({placeholders})", values)
    conn.commit()
    conn.close()

    return redirect(url_for("saved", vendor_id=vendor_id))


@app.route("/saved/<vendor_id>")
def saved(vendor_id):
    return render_template("saved.html", vendor_id=vendor_id)


@app.route("/records")
def records():
    conn = get_db()
    rows = conn.execute("SELECT * FROM vendors ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template("list.html", rows=rows)


@app.route("/files/<kind>/<fname>")
def serve_file(kind, fname):
    folder = PHOTO_DIR if kind == "photos" else DOC_DIR
    return send_from_directory(folder, fname)


@app.route("/api/status")
def status():
    online = is_online()
    conn = get_db()
    pending = conn.execute("SELECT COUNT(*) c FROM vendors WHERE synced=0").fetchone()["c"]
    total = conn.execute("SELECT COUNT(*) c FROM vendors").fetchone()["c"]
    conn.close()
    return jsonify({"online": online, "pending_sync": pending, "total_records": total})


@app.route("/api/sync_now", methods=["POST"])
def sync_now():
    result = run_sync_pass()
    return jsonify(result)


# ---------------------------------------------------------------------------
# Background auto-sync to Google Drive
# ---------------------------------------------------------------------------


def run_sync_pass():
    if not is_online():
        return {"online": False, "synced": 0}

    conn = get_db()
    rows = conn.execute("SELECT * FROM vendors WHERE synced=0").fetchall()
    synced_count = 0
    for row in rows:
        try:
            record = dict(row)
            files_to_upload = {}
            for f in FILE_FIELDS:
                if record.get(f) and os.path.exists(record[f]):
                    files_to_upload[f] = record[f]

            vendor_code = drive_sync.upload_vendor_record(
                vendor_id=record["id"],
                trader_name=record.get("trader_name") or "Unknown",
                record_data={k: record.get(k) for k in FIELDS},
                files=files_to_upload,
            )
            conn.execute(
                "UPDATE vendors SET synced=1, drive_folder_id=?, vendor_code=? WHERE id=?",
                (vendor_code, vendor_code, record["id"]),
            )
            conn.commit()
            synced_count += 1
        except Exception as e:
            print(f"[sync] failed for {row['id']}: {e}")
    conn.close()
    return {"online": True, "synced": synced_count}


def background_sync_loop(interval_sec=60):
    while True:
        try:
            result = run_sync_pass()
            if result.get("synced"):
                print(f"[sync] uploaded {result['synced']} record(s) to Google Drive")
        except Exception as e:
            print(f"[sync] loop error: {e}")
        time.sleep(interval_sec)


if __name__ == "__main__":
    t = threading.Thread(target=background_sync_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=False)
