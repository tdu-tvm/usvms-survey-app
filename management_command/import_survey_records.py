"""
Management command: import_survey_records
=========================================
Reads every vendor folder under SURVEY_UPLOAD_ROOT (default
/root/usvms_survey/uploads/) and creates or updates Vendor records
in USVMS's PostgreSQL database.

Usage (from /root/usvms, with venv active):
    python manage.py import_survey_records
    python manage.py import_survey_records --dry-run
    python manage.py import_survey_records --folder TVM0001_Raja
    python manage.py import_survey_records --ulb "Tiruvannamalai"

Deploy:
    Copy this file to:
    /root/usvms/apps/vendors/management/commands/import_survey_records.py

    Make sure __init__.py files exist:
    /root/usvms/apps/vendors/management/__init__.py
    /root/usvms/apps/vendors/management/commands/__init__.py

Idempotent: re-running will update existing records (matched by
survey_serial_no = vendor_code from the survey app) rather than
creating duplicates.

Field mapping:
    Survey app field          → USVMS Vendor field
    ─────────────────────────────────────────────
    vendor_code               → survey_serial_no (unique match key)
    trader_name               → name
    father_husband_name       → father_or_spouse_name
    age                       → age
    caste                     → category (mapped to TextChoices)
    mobile_no                 → mobile_number
    marital_status            → marital_status (mapped)
    differently_abled         → is_disabled
    permanent_address         → address
    goods_sold                → business_description
    business_type             → vending_mode (mapped) + business_type=OTHER
    business_hours            → time_of_business
    aadhar_no                 → aadhaar_number
    voter_id_no               → voter_id_number
    ration_card_no            → ration_card_number
    bank_account_no           → bank_account_number
    ifsc_code                 → bank_ifsc
    bank_name_branch          → bank_name
    pmsvanidhi_loan (Yes/No)  → is_pm_svanidhi_beneficiary
    upi_usage (Yes/No)        → uses_upi_for_business
    food_license (Yes/No)     → has_food_safety_license
    latitude + longitude      → location (PointField)
    ulb_name                  → ulb (FK lookup by name icontains)
    district                  → ulb__district lookup fallback
    surveyor_name             → surveyed_by
    vendor_photo              → photo (copied to media/vendors/photo/)
    aadhar_doc                → VendorDocument(AADHAAR)
    voter_id_doc              → VendorDocument(OTHER)
    bank_passbook_doc         → VendorDocument(BANK_PASSBOOK)
    pds_card_doc              → VendorDocument(OTHER)
    vending_photo             → VendorDocument(OTHER)
"""
import json
import os
import shutil
from datetime import date

from django.contrib.gis.geos import Point
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

SURVEY_UPLOAD_ROOT = os.environ.get(
    "SURVEY_UPLOAD_ROOT", "/root/usvms_survey/uploads"
)
USVMS_MEDIA_ROOT = os.environ.get(
    "DJANGO_MEDIA_ROOT",
    "/root/usvms/media",
)

# ---------------------------------------------------------------------------
# Field mapping helpers
# ---------------------------------------------------------------------------

CASTE_MAP = {
    "sc": "SC",
    "st": "ST",
    "mbc": "MBC",
    "obc": "OBC",
    "bc": "OBC",   # BC maps closest to OBC in USVMS choices
    "others": "GENERAL",
    "general": "GENERAL",
}

GENDER_MAP = {
    "male": "MALE",
    "female": "FEMALE",
    "other": "OTHER",
    "others": "OTHER",
}

MARITAL_MAP = {
    "married": "MARRIED",
    "unmarried": "UNMARRIED",
    "widow": "WIDOWED",
    "widower": "WIDOWED",
    "widowed": "WIDOWED",
}

BUSINESS_HOURS_MAP = {
    "morning": "Morning",
    "evening": "Evening",
    "night": "Night",
    "24 hours": "Whole Day",
    "whole day": "Whole Day",
}

VENDING_MODE_MAP = {
    "permanent shop": "STATIONARY",
    "pushcart": "MOBILE",
    "mobile": "MOBILE",
    "stationary": "STATIONARY",
    "seasonal": "SEASONAL",
}

DOCUMENT_TYPE_MAP = {
    "aadhar_doc": "AADHAAR",
    "bank_passbook_doc": "BANK_PASSBOOK",
    "voter_id_doc": "OTHER",
    "pds_card_doc": "OTHER",
    "vending_photo": "OTHER",
}


def yn(value):
    return str(value).strip().lower() in ("yes", "y", "true", "1")


def safe_strip(value, max_len=None):
    v = str(value or "").strip()
    if max_len:
        v = v[:max_len]
    return v


class Command(BaseCommand):
    help = "Import synced survey app records into USVMS vendor database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print what would happen without writing to the database.",
        )
        parser.add_argument(
            "--folder", type=str, default=None,
            help="Import only a single folder by name (e.g. TVM0001_Raja).",
        )
        parser.add_argument(
            "--ulb", type=str, default=None,
            help="Filter import to records whose ulb_name matches this string.",
        )

    def handle(self, *args, **options):
        from apps.locations.models import ULB, District
        from apps.vendors.models import Vendor, VendorDocument

        dry_run = options["dry_run"]
        single_folder = options["folder"]
        ulb_filter = (options["ulb"] or "").strip().lower()

        if not os.path.isdir(SURVEY_UPLOAD_ROOT):
            raise CommandError(
                f"SURVEY_UPLOAD_ROOT not found: {SURVEY_UPLOAD_ROOT}"
            )

        folders = (
            [single_folder]
            if single_folder
            else sorted(os.listdir(SURVEY_UPLOAD_ROOT))
        )

        created = updated = skipped = errors = 0

        for folder_name in folders:
            folder_path = os.path.join(SURVEY_UPLOAD_ROOT, folder_name)
            data_path = os.path.join(folder_path, "data.json")

            if not os.path.isfile(data_path):
                self.stdout.write(f"  SKIP {folder_name}: no data.json")
                skipped += 1
                continue

            try:
                with open(data_path, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
            except json.JSONDecodeError as e:
                self.stderr.write(f"  ERROR {folder_name}: bad JSON — {e}")
                errors += 1
                continue

            # apply ulb filter if requested
            ulb_name_raw = safe_strip(d.get("ulb_name", ""))
            if ulb_filter and ulb_filter not in ulb_name_raw.lower():
                skipped += 1
                continue

            vendor_code = safe_strip(d.get("vendor_code") or folder_name.split("_")[0])

            # --- resolve ULB FK -------------------------------------------
            district_raw = safe_strip(d.get("district", "")).split("/")[0].strip()
            ulb_qs = ULB.objects.filter(name__icontains=ulb_name_raw.split("/")[0].strip())
            if not ulb_qs.exists() and district_raw:
                # fallback: any ULB in that district
                ulb_qs = ULB.objects.filter(
                    district__name__icontains=district_raw
                )
            if not ulb_qs.exists():
                self.stderr.write(
                    f"  ERROR {folder_name}: ULB not found for "
                    f"'{ulb_name_raw}' / district '{district_raw}' — "
                    f"create it in USVMS admin first."
                )
                errors += 1
                continue
            ulb = ulb_qs.first()

            # --- build field values ----------------------------------------
            lat = d.get("latitude", "").strip()
            lon = d.get("longitude", "").strip()
            location = None
            if lat and lon:
                try:
                    location = Point(float(lon), float(lat), srid=4326)
                except ValueError:
                    pass

            family_json = d.get("family_members_json", "[]")
            try:
                family_list = json.loads(family_json)
                family_count = len(family_list)
            except (json.JSONDecodeError, TypeError):
                family_count = None

            fields = dict(
                name=safe_strip(d.get("trader_name", "Unknown"), 200),
                father_or_spouse_name=safe_strip(d.get("father_husband_name", ""), 200),
                age=int(d["age"]) if str(d.get("age", "")).isdigit() else None,
                gender=GENDER_MAP.get(safe_strip(d.get("gender", "")).lower(), "FEMALE"),
                category=CASTE_MAP.get(safe_strip(d.get("caste", "")).lower(), "GENERAL"),
                mobile_number=safe_strip(d.get("mobile_no", ""), 10),
                marital_status=MARITAL_MAP.get(safe_strip(d.get("marital_status", "")).lower(), ""),
                is_disabled=yn(d.get("differently_abled", "No")),
                address=safe_strip(d.get("permanent_address", "")),
                family_members_count=family_count,
                business_type="OTHER",
                business_description=safe_strip(d.get("goods_sold", ""), 255),
                vending_mode=VENDING_MODE_MAP.get(
                    safe_strip(d.get("business_type", "")).lower(), "STATIONARY"
                ),
                nature_of_business=safe_strip(d.get("nature_of_business", ""), 50),
                time_of_business=BUSINESS_HOURS_MAP.get(
                    safe_strip(d.get("business_hours", "")).lower(),
                    safe_strip(d.get("business_hours", ""), 50),
                ),
                aadhaar_number=safe_strip(d.get("aadhar_no", ""), 12) or None,
                voter_id_number=safe_strip(d.get("voter_id_no", ""), 20),
                ration_card_number=safe_strip(d.get("ration_card_no", ""), 30),
                bank_account_number=safe_strip(d.get("bank_account_no", ""), 30),
                bank_ifsc=safe_strip(d.get("ifsc_code", ""), 11),
                bank_name=safe_strip(d.get("bank_name_branch", ""), 200),
                is_pm_svanidhi_beneficiary=yn(d.get("pmsvanidhi_loan", "No")),
                uses_upi_for_business=yn(d.get("upi_usage", "No")),
                has_food_safety_license=yn(d.get("food_license", "No")),
                location=location,
                ulb=ulb,
                survey_status="SURVEYED",
                survey_date=date.today(),
                surveyed_by=safe_strip(d.get("surveyor_name", ""), 200),
                survey_serial_no=vendor_code,
                status="PENDING",
                vending_location_name=safe_strip(d.get("permanent_address", ""), 255),
            )

            self.stdout.write(
                f"  {'[DRY RUN] ' if dry_run else ''}"
                f"{'CREATE' if not Vendor.objects.filter(survey_serial_no=vendor_code).exists() else 'UPDATE'} "
                f"{vendor_code} — {fields['name']} — {ulb.name}"
            )

            if dry_run:
                skipped += 1
                continue

            # --- create or update Vendor -----------------------------------
            vendor, is_new = Vendor.objects.update_or_create(
                survey_serial_no=vendor_code,
                defaults=fields,
            )

            if is_new:
                created += 1
            else:
                updated += 1

            # --- vendor photo ----------------------------------------------
            vendor_photo_src = d.get("vendor_photo", "")
            if vendor_photo_src and os.path.isfile(vendor_photo_src) and not vendor.photo:
                photo_dest_dir = os.path.join(USVMS_MEDIA_ROOT, "vendors", "photo")
                os.makedirs(photo_dest_dir, exist_ok=True)
                photo_fname = os.path.basename(vendor_photo_src)
                photo_dest = os.path.join(photo_dest_dir, photo_fname)
                shutil.copy2(vendor_photo_src, photo_dest)
                vendor.photo = f"vendors/photo/{photo_fname}"
                vendor.save(update_fields=["photo"])

            # --- supporting documents -------------------------------------
            for field_name, doc_type in DOCUMENT_TYPE_MAP.items():
                src_path = d.get(field_name, "")
                if not src_path or not os.path.isfile(src_path):
                    continue
                # avoid duplicate document uploads on re-runs
                if vendor.documents.filter(document_type=doc_type).exists():
                    continue
                with open(src_path, "rb") as f:
                    VendorDocument.objects.create(
                        vendor=vendor,
                        document_type=doc_type,
                        file=File(f, name=os.path.basename(src_path)),
                    )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone — created: {created}, updated: {updated}, "
            f"skipped: {skipped}, errors: {errors}"
        ))
