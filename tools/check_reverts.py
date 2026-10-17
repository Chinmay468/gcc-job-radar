import argparse
from datetime import datetime, timedelta
import email
import imaplib
import os
from pathlib import Path
import sqlite3
import sys
from typing import Optional

from dotenv import load_dotenv

# Ensure project root is in sys.path when invoked directly
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Load environment variables
load_dotenv(dotenv_path=ROOT_DIR / ".env")

from gcc_job_radar.db import get_db_path, init_db, mark_job_status, record_dispatched_alerts
from gcc_job_radar.email_revert_detector import (
    RevertMatch,
    RevertType,
    extract_clean_email_body,
    process_email_record,
    scan_email_reverts,
)
from gcc_job_radar.notifier import send_telegram_notification


def _parse_email_message(msg_bytes: bytes) -> dict:
    """Extract useful fields from a raw email message.

    Returns a dict with keys: ``subject``, ``sender``, ``date``, ``body``.
    """
    msg = email.message_from_bytes(msg_bytes)
    subject = msg.get("Subject", "")
    sender = msg.get("From", "")
    date = msg.get("Date", "")
    body = extract_clean_email_body(msg)
    return {"subject": subject, "sender": sender, "date": date, "body": body}


def _store_seen_email(uid: str, db_path: Optional[Path] = None) -> None:
    """Record a processed email UID in the ``seen_emails`` table."""
    init_db(db_path)
    target = get_db_path(db_path)
    with sqlite3.connect(target) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_emails (uid) VALUES (?)",
            (uid,),
        )
        conn.commit()


def _has_seen_email(uid: str, db_path: Optional[Path] = None) -> bool:
    """Check whether an email UID has already been processed."""
    init_db(db_path)
    target = get_db_path(db_path)
    with sqlite3.connect(target) as conn:
        cur = conn.execute("SELECT 1 FROM seen_emails WHERE uid = ?", (uid,))
        return cur.fetchone() is not None


def _send_revert_alert(match: RevertMatch) -> None:
    """Send a Telegram alert for important revert types."""
    if match.revert_type in {RevertType.INTERVIEW, RevertType.OA_INVITE}:
        message = (
            f"*{match.revert_type.value}* detected from *{match.sender}* for *{match.company}*\n"
            f"Subject: {match.subject}\nSnippet: {match.snippet}"
        )
        send_telegram_notification(message)
        record_dispatched_alerts(
            job_id=str(match.matched_job_id) if match.matched_job_id else None,
            platform="telegram",
        )


def _update_job_status(match: RevertMatch, db_path: Optional[Path] = None) -> None:
    """Update the status of the correlated job based on the revert type."""
    if not match.matched_job_id:
        return
    if match.revert_type == RevertType.INTERVIEW:
        status, notes = "INTERVIEWING", "Interview invitation received via email."
    elif match.revert_type == RevertType.OA_INVITE:
        status, notes = "INTERVIEWING", "Online assessment invitation received via email."
    elif match.revert_type == RevertType.REJECTED:
        status, notes = "REJECTED", "Rejection email received."
    else:
        return
    mark_job_status(job_id=match.matched_job_id, status=status, notes=notes, db_path=db_path)


def run_check_reverts(
    days: int = 90,
    dry_run: bool = False,
    notify: bool = True,
    db_path: Optional[Path] = None,
) -> dict:
    """Scan configured email accounts for recruiter replies and rejections."""
    res = scan_email_reverts(days=days, update_db=not dry_run, db_path=db_path)
    print(
        f"[*] Scanned {len(res['accounts_checked'])} account(s) over last {days} days: "
        f"{res['total_rejections']} rejection(s) across {res['unique_rejected_companies_count']} company/companies."
    )
    for idx, r in enumerate(res["rejections"], 1):
        print(f"  {idx:2d}. [{r['date']}] {r['company']} — {r['role']} ({r['account']})")
    return res


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Detect recruiter email reverts and sync job status")
    parser.add_argument("--days", type=int, default=90, help="Number of days back to scan (default: 90)")
    parser.add_argument("--dry-run", action="store_true", help="Do not modify DB or send alerts")
    parser.add_argument("--no-notify", dest="notify", action="store_false", help="Suppress Telegram alerts")
    parser.add_argument("--db-path", type=str, default=None, help="Custom SQLite DB path")
    args = parser.parse_args()
    run_check_reverts(
        days=args.days,
        dry_run=args.dry_run,
        notify=args.notify,
        db_path=Path(args.db_path) if args.db_path else None,
    )
