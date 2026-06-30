"""
Syncs locally-saved vendor records to your own VPS (nulm-susv.in) instead
of Google Drive. No third-party account needed at all -- just your server.

SETUP
-----
1. Deploy server_app/receiver.py on the VPS (see server_app/DEPLOY.md).
2. On the field device, set the same API key + URL the server uses:
     export SURVEY_VPS_URL="https://nulm-susv.in/api/survey/upload"
     export SURVEY_API_KEY="same-key-as-server"
   (or just edit the defaults below)
"""
import os
import json
import requests

VPS_UPLOAD_URL = os.environ.get("SURVEY_VPS_URL", "https://nulm-susv.in/api/survey/upload")
API_KEY = os.environ.get("SURVEY_API_KEY", "change-this-key")


def upload_vendor_record(vendor_id, trader_name, record_data, files):
    """
    POSTs vendor_id, trader_name, record_data (as JSON), and every file in
    `files` (dict of field_name -> local path) to the VPS receiver.
    Returns the official vendor_code assigned by the VPS (e.g. "TVM0001"),
    or the raw upload URL as a fallback if the server didn't return one.
    """
    data = {
        "vendor_id": vendor_id,
        "trader_name": trader_name,
        "record_json": json.dumps(record_data, ensure_ascii=False),
    }

    open_files = []
    file_payload = {}
    try:
        for field_name, local_path in files.items():
            if local_path and os.path.exists(local_path):
                fh = open(local_path, "rb")
                open_files.append(fh)
                file_payload[field_name] = (os.path.basename(local_path), fh)

        resp = requests.post(
            VPS_UPLOAD_URL,
            data=data,
            files=file_payload,
            headers={"X-API-Key": API_KEY},
            timeout=60,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") != "ok":
            raise RuntimeError(f"VPS rejected upload: {result}")
        return result.get("vendor_code") or VPS_UPLOAD_URL
    finally:
        for fh in open_files:
            fh.close()
