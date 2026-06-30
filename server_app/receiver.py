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
from flask import Flask, request, jsonify

app = Flask(__name__)

UPLOAD_ROOT = os.environ.get("SURVEY_UPLOAD_ROOT", "/root/usvms_survey/uploads")
API_KEY = os.environ.get("SURVEY_API_KEY", "change-this-key")

os.makedirs(UPLOAD_ROOT, exist_ok=True)


def check_auth():
    return request.headers.get("X-API-Key") == API_KEY


@app.route("/api/survey/upload", methods=["POST"])
def upload():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401

    vendor_id = request.form.get("vendor_id")
    trader_name = request.form.get("trader_name", "vendor")
    record_json = request.form.get("record_json", "{}")

    if not vendor_id:
        return jsonify({"error": "vendor_id required"}), 400

    safe_name = "".join(c for c in trader_name if c.isalnum() or c in " _-").strip() or "vendor"
    folder = os.path.join(UPLOAD_ROOT, f"{vendor_id}_{safe_name}")
    os.makedirs(folder, exist_ok=True)

    with open(os.path.join(folder, "data.json"), "w", encoding="utf-8") as fh:
        try:
            parsed = json.loads(record_json)
        except json.JSONDecodeError:
            parsed = {"raw": record_json}
        json.dump(parsed, fh, ensure_ascii=False, indent=2)

    saved_files = []
    for field_name, file_storage in request.files.items():
        if file_storage and file_storage.filename:
            ext = os.path.splitext(file_storage.filename)[1] or ".jpg"
            dest = os.path.join(folder, f"{field_name}{ext}")
            file_storage.save(dest)
            saved_files.append(field_name)

    return jsonify({"status": "ok", "vendor_id": vendor_id, "files_received": saved_files})


@app.route("/api/survey/health")
def health():
    return jsonify({"status": "up"})


if __name__ == "__main__":
    # dev only; in production run via gunicorn (see deploy instructions)
    app.run(host="0.0.0.0", port=6000)
