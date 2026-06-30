# Deploying the Survey Sync Receiver on nulm-susv.in

This is a tiny separate Flask service (`receiver.py`) — it does NOT touch
your USVMS app or database. It just accepts uploads from field devices and
writes them to disk on the VPS, in its own folder.

## 1. Copy files to the VPS

```bash
scp -r server_app root@187.127.185.88:/root/usvms_survey
```

## 2. Install deps in a venv (reuse Python 3.12 like USVMS)

```bash
ssh root@187.127.185.88
cd /root/usvms_survey
python3.12 -m venv venv312
source venv312/bin/activate
pip install flask gunicorn
```

## 3. Set the API key and run with gunicorn

```bash
export SURVEY_API_KEY="pick-a-long-random-key-here"
# persist it: echo 'export SURVEY_API_KEY="..."' >> /root/.bashrc

gunicorn -w 2 -b 127.0.0.1:6000 receiver:app
```

Better: run it as a systemd service so it survives reboots.

`/etc/systemd/system/usvms-survey.service`:
```ini
[Unit]
Description=USVMS Survey Sync Receiver
After=network.target

[Service]
WorkingDirectory=/root/usvms_survey
Environment="SURVEY_API_KEY=pick-a-long-random-key-here"
Environment="SURVEY_UPLOAD_ROOT=/root/usvms_survey/uploads"
ExecStart=/root/usvms_survey/venv312/bin/gunicorn -w 2 -b 127.0.0.1:6000 receiver:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now usvms-survey
```

## 4. Add an nginx location block

You already have `ulb_nginx` / nginx fronting `nulm-susv.in`. Add this
inside the existing `server { ... }` block for that domain (alongside
your USVMS `location /` block):

```nginx
location /api/survey/ {
    proxy_pass http://127.0.0.1:6000/api/survey/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    client_max_body_size 50M;   # allow photo/document uploads
}
```

Reload nginx:
```bash
nginx -t && systemctl reload nginx
```

Since SSL is already issued for `nulm-susv.in` (Certbot), this endpoint is
automatically served over HTTPS at:

```
https://nulm-susv.in/api/survey/upload
```

## 5. Point the field app at it

On every surveyor's device, before running `python app.py`:

```bash
export SURVEY_VPS_URL="https://nulm-susv.in/api/survey/upload"
export SURVEY_API_KEY="pick-a-long-random-key-here"   # same key as the VPS
```

(or just hardcode both directly in `drive_sync.py` defaults if it's
easier than setting env vars on each device).

## 6. Verify

```bash
curl https://nulm-susv.in/api/survey/health
# {"status": "up"}
```

Submit a test record from the field app, then check:
```bash
ls /root/usvms_survey/uploads/
```

## Where uploads land
```
/root/usvms_survey/uploads/<vendor_id>_<trader_name>/
    data.json
    vendor_photo.jpg
    vending_photo.jpg
    aadhar_doc.jpg
    voter_id_doc.jpg
    bank_passbook_doc.jpg
    pds_card_doc.jpg
```

You can later write a one-off import script to load `data.json` files from
here into your USVMS Postgres `vendors` table if you want these survey
submissions to flow straight into the production system.
