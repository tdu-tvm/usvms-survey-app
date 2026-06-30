# Street Vendor Survey — Offline-first App

Matches the Tiruvannamalai street vendor census form (USVMS / PM SVANidhi).

## How it works
- Runs as a local Flask server on the surveyor's laptop/phone (Termux).
- The form, camera capture, and GPS capture all work **with zero internet**
  because the page is served from `127.0.0.1` and the browser's
  `<input capture>` / `navigator.geolocation` APIs are local device features.
- Every submission is written **immediately** to local SQLite
  (`data/survey.db`) and local folders (`data/photos`, `data/docs`) —
  nothing is lost if there's no signal.
- A background thread checks for internet every 60 seconds. The moment the
  device gets online, it automatically uploads every un-synced record
  (form fields as `data.json` + all photos/documents) into a per-vendor
  folder on Google Drive, then marks it as synced.
- You can also force an immediate sync from the **Records** page
  ("🔄 Sync Now" button), or via `POST /api/sync_now`.

## Setup

```bash
pip install -r requirements.txt
```

### Sync target: your own VPS (nulm-susv.in) — no third-party account needed

Records sync to a small receiver API you deploy on your existing Hostinger
VPS, alongside USVMS. Full setup steps are in `server_app/DEPLOY.md`.

Quick summary:
1. Deploy `server_app/receiver.py` on the VPS via gunicorn + systemd,
   reverse-proxied through nginx at `https://nulm-susv.in/api/survey/upload`.
2. Pick an API key, set it on both the VPS and the field device.
3. On the field device:
   ```bash
   export SURVEY_VPS_URL="https://nulm-susv.in/api/survey/upload"
   export SURVEY_API_KEY="your-shared-key"
   ```
4. Run `python app.py` as usual — once the device is online, pending
   records auto-POST to your VPS and land in
   `/root/usvms_survey/uploads/<vendor_id>_<trader_name>/`.

(An rclone-based Google Drive option is no longer needed since you have
your own VPS storage, but if you ever want it back it's a 1-file swap.)


### Run
```bash
python app.py
```
Open `http://127.0.0.1:5000` on the same device's browser. On Android,
run it in **Termux** (`pip install flask ...`, same steps) and open Chrome
to the same URL — camera + GPS permission prompts will appear normally.

## Folder layout in Drive (per vendor)
```
<vendor_id>_<trader_name>/
  data.json              -> all 23 form fields + lat/lon/survey no/ward etc.
  vendor_photo.jpg
  vending_photo.jpg
  aadhar_doc.jpg / .pdf
  voter_id_doc.jpg / .pdf
  bank_passbook_doc.jpg / .pdf
  pds_card_doc.jpg / .pdf
```

## Notes / things to adapt for production
- Add a login/PIN screen if multiple surveyors share one device.
- `is_online()` checks DNS reachability to 8.8.8.8 — fine for most networks;
  swap for a lighter check if that port is blocked on a specific carrier.
- For a true installable Android app (not just a browser tab), wrap this
  same Flask backend with **Kivy/BeeWare**, or package the page as a PWA;
  the storage/sync logic doesn't need to change.
- Field labels mirror the uploaded survey form (Tamil + English); adjust
  wording in `templates/index.html` if the official form changes.
