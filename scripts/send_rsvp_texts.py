#!/usr/bin/env python3
"""One-time RSVP text blast for the Fornoff wedding (send date: 2026-10-01).

Reads parties + guests + rsvps from Supabase, sorts each party into one of
three buckets, and sends a bucket-appropriate SMS to the party's phone number
via Twilio. One message per party, because `phone` lives on `parties`.

Deliberately dependency-free (stdlib only) so there is nothing to install or
break on send day. Run it from anywhere with Python 3.9+.

    python scripts/send_rsvp_texts.py                 # dry run, prints plan
    python scripts/send_rsvp_texts.py --send          # actually sends
    python scripts/send_rsvp_texts.py --send --limit 2  # test on 2 parties

Config comes from environment variables, or a .env file beside the repo root:

    SUPABASE_URL=https://xxxx.supabase.co
    SUPABASE_SERVICE_ROLE_KEY=...      # service role, NOT the anon key
    TWILIO_ACCOUNT_SID=AC...
    TWILIO_AUTH_TOKEN=...
    TWILIO_FROM=+16025551234           # your registered 10DLC / toll-free number

Never commit the .env — .gitignore already covers it.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE_URL = "https://fornoffwedding.com/rsvp.html"  # TODO: confirm the real host
LOG_PATH = REPO_ROOT / "scripts" / "send_log.csv"

# Buckets. "no_response" is the default target: per the couple, a party gets
# the nudge if *any* guest on the invitation has not responded at all.
BUCKET_NO_RESPONSE = "no_response"
BUCKET_ATTENDING = "attending"
BUCKET_NOT_ATTENDING = "not_attending"
ALL_BUCKETS = (BUCKET_NO_RESPONSE, BUCKET_ATTENDING, BUCKET_NOT_ATTENDING)


def message_for(bucket: str, party_name: str) -> str:
    """Message copy per bucket. Keep these under ~320 chars (2 SMS segments)."""
    if bucket == BUCKET_NO_RESPONSE:
        return (
            f"Taylor & Connor here! We haven't heard back from {party_name} about "
            f"our October 21 wedding, and we'd really love to have you there. "
            f"Could you R.S.V.P. at {SITE_URL}? Thank you!"
        )
    if bucket == BUCKET_ATTENDING:
        return (
            f"Taylor & Connor here! We're so excited to celebrate with you on "
            f"October 21. Ceremony begins at 4:00 PM and the reception wraps up "
            f"at 10:00 PM. Dress code, directions, and FAQs: {SITE_URL}"
        )
    if bucket == BUCKET_NOT_ATTENDING:
        return (
            "Taylor & Connor here - thank you for letting us know you can't make "
            "it on October 21. We'll miss you, and we're grateful you took the "
            "time to respond. No reply needed!"
        )
    raise ValueError(f"unknown bucket: {bucket}")


# ---------------------------------------------------------------- config ----


def load_env() -> None:
    """Merge a .env file into os.environ without clobbering real env vars."""
    for candidate in (REPO_ROOT / ".env", REPO_ROOT.parent / ".env"):
        if not candidate.exists():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Missing required config: {name}. See the docstring in this file.")
    return value


# -------------------------------------------------------------- supabase ----


def supabase_get(base_url: str, key: str, path: str) -> list[dict]:
    url = f"{base_url.rstrip('/')}/rest/v1/{path}"
    req = urllib.request.Request(
        url,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        sys.exit(f"Supabase request failed ({exc.code}) for {path}:\n{body}")


def normalize_phone(raw: str | None) -> str | None:
    """Best-effort E.164 for US numbers. Returns None if it can't be trusted."""
    if not raw:
        return None
    text = str(raw).strip()
    if text.startswith("+"):
        digits = re.sub(r"\D", "", text)
        return f"+{digits}" if 11 <= len(digits) <= 15 else None
    digits = re.sub(r"\D", "", text)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def classify(guests: list[dict], rsvp_by_guest: dict[str, dict]) -> str:
    """Bucket a party from its guests' RSVP rows.

    Any guest with no row at all -> no_response (the couple's stated rule).
    Otherwise attending if at least one guest said yes, else not_attending.
    """
    if not guests:
        return BUCKET_NO_RESPONSE
    rows = [rsvp_by_guest.get(g["id"]) for g in guests]
    if any(row is None for row in rows):
        return BUCKET_NO_RESPONSE
    if any(row.get("attending") for row in rows if row):
        return BUCKET_ATTENDING
    return BUCKET_NOT_ATTENDING


def build_plan(base_url: str, key: str) -> tuple[list[dict], list[dict]]:
    parties = supabase_get(
        base_url, key, "parties?select=id,party_name,phone,guests(id,first_name,last_name)"
    )
    rsvps = supabase_get(base_url, key, "rsvps?select=guest_id,attending")
    rsvp_by_guest = {r["guest_id"]: r for r in rsvps}

    sendable: list[dict] = []
    skipped: list[dict] = []
    for party in parties:
        bucket = classify(party.get("guests") or [], rsvp_by_guest)
        phone = normalize_phone(party.get("phone"))
        record = {
            "party_id": party["id"],
            "party_name": party.get("party_name") or "your party",
            "bucket": bucket,
            "phone": phone,
            "raw_phone": party.get("phone"),
            "guest_count": len(party.get("guests") or []),
        }
        (sendable if phone else skipped).append(record)
    return sendable, skipped


# ---------------------------------------------------------------- twilio ----


def send_sms(sid: str, token: str, from_number: str, to: str, body: str) -> tuple[bool, str]:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    payload = urllib.parse.urlencode({"To": to, "From": from_number, "Body": body}).encode()
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True, json.loads(resp.read()).get("sid", "")
    except urllib.error.HTTPError as exc:
        return False, exc.read().decode("utf-8", "replace")[:300]
    except Exception as exc:  # network flake, DNS, timeout
        return False, f"{type(exc).__name__}: {exc}"


def already_sent() -> set[str]:
    """Party ids with a successful send in the log, so reruns don't double-text."""
    if not LOG_PATH.exists():
        return set()
    with LOG_PATH.open(newline="", encoding="utf-8") as fh:
        return {
            row["party_id"] for row in csv.DictReader(fh) if row.get("status") == "sent"
        }


def log_result(record: dict, status: str, detail: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LOG_PATH.exists()
    with LOG_PATH.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if is_new:
            writer.writerow(
                ["timestamp", "party_id", "party_name", "bucket", "phone", "status", "detail"]
            )
        writer.writerow(
            [
                time.strftime("%Y-%m-%dT%H:%M:%S"),
                record["party_id"],
                record["party_name"],
                record["bucket"],
                record["phone"],
                status,
                detail,
            ]
        )


# ------------------------------------------------------------------ main ----


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send", action="store_true", help="actually send (default is a dry run)"
    )
    parser.add_argument(
        "--bucket",
        action="append",
        choices=ALL_BUCKETS,
        help=f"buckets to target, repeatable (default: {BUCKET_NO_RESPONSE})",
    )
    parser.add_argument("--limit", type=int, help="cap the number of messages")
    parser.add_argument(
        "--resend",
        action="store_true",
        help="ignore send_log.csv and re-text parties already messaged",
    )
    args = parser.parse_args()

    buckets = set(args.bucket or [BUCKET_NO_RESPONSE])

    load_env()
    base_url = require("SUPABASE_URL")
    service_key = require("SUPABASE_SERVICE_ROLE_KEY")

    sendable, skipped = build_plan(base_url, service_key)
    targets = [r for r in sendable if r["bucket"] in buckets]

    if not args.resend:
        seen = already_sent()
        before = len(targets)
        targets = [r for r in targets if r["party_id"] not in seen]
        if before != len(targets):
            print(f"Skipping {before - len(targets)} party(ies) already sent per {LOG_PATH.name}.")

    if args.limit:
        targets = targets[: args.limit]

    counts: dict[str, int] = {}
    for record in sendable:
        counts[record["bucket"]] = counts.get(record["bucket"], 0) + 1
    print("\nParties with a usable phone number, by bucket:")
    for bucket in ALL_BUCKETS:
        print(f"  {bucket:<15} {counts.get(bucket, 0)}")
    if skipped:
        print(f"\n{len(skipped)} party(ies) have no usable phone number and will be skipped:")
        for record in skipped[:20]:
            print(f"  - {record['party_name']} (raw: {record['raw_phone']!r})")
        if len(skipped) > 20:
            print(f"  ... and {len(skipped) - 20} more")

    print(f"\nTargeting buckets: {', '.join(sorted(buckets))}")
    print(f"Messages to send:  {len(targets)}\n")

    if not targets:
        print("Nothing to do.")
        return

    if not args.send:
        print("--- DRY RUN (no messages sent; pass --send to really send) ---\n")
        for record in targets:
            body = message_for(record["bucket"], record["party_name"])
            print(f"[{record['bucket']}] {record['party_name']} -> {record['phone']}")
            print(f"  {body}\n")
        return

    sid = require("TWILIO_ACCOUNT_SID")
    token = require("TWILIO_AUTH_TOKEN")
    from_number = require("TWILIO_FROM")

    confirm = input(f"Send {len(targets)} real text messages? Type SEND to confirm: ")
    if confirm.strip() != "SEND":
        print("Aborted.")
        return

    sent = failed = 0
    for index, record in enumerate(targets, start=1):
        body = message_for(record["bucket"], record["party_name"])
        ok, detail = send_sms(sid, token, from_number, record["phone"], body)
        log_result(record, "sent" if ok else "failed", detail)
        status = "OK " if ok else "ERR"
        print(f"[{index}/{len(targets)}] {status} {record['party_name']} -> {record['phone']}")
        if not ok:
            print(f"        {detail}")
        sent, failed = (sent + 1, failed) if ok else (sent, failed + 1)
        time.sleep(1)  # stay well under Twilio's per-number throughput

    print(f"\nDone. Sent {sent}, failed {failed}. Full log: {LOG_PATH}")


if __name__ == "__main__":
    main()
