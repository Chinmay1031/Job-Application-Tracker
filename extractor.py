import os
import json
import re
import unicodedata
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv
from openai import OpenAI

from gmail_service import get_gmail_service, build_query, search_emails, get_email_details

load_dotenv()
client = OpenAI()

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
CACHE_FILE = "applications.json"
MAX_BODY_CHARS = 5000

STATUS_MAP = {
    "rejection": "Rejected",
    "interview_invite": "Interview",
    "assessment": "Online Test",
    "offer": "Offer",
}

TERMINAL_STATUSES = {"Rejected", "Withdrawn"}

SYSTEM_PROMPT = """You read a single email related to a job application and classify it.

Return ONLY a JSON object with these keys:

- "email_type": one of
   "new_application"   -> confirms the user just submitted an application
   "rejection"         -> the user was rejected / not moving forward
   "interview_invite"  -> inviting the user to an interview / call
   "assessment"        -> asking the user to complete an online test, assessment or case study
   "offer"             -> extending a job offer
   "irrelevant"        -> newsletter, job alert, recruiter spam, unrelated marketing

- "company": the REAL company the user applied to or is interacting with.
   IMPORTANT: many emails are sent through a recruiting platform or ATS
   (e.g. Eligo, SuccessFactors, Greenhouse, Lever, Workday, Recruitee, Avature,
   SmartRecruiters, StepStone, Personio, Ashby). These are NOT the company — look
   at the email body/subject for the actual employer's name. null if truly unclear.

- "job_title": the position involved, if mentioned anywhere in the subject or body.
   Keep suffixes like (m/w/d) or (f/m/x). null if not mentioned.

- "location": job location if mentioned, else null.

- "platform": the recruiting platform/ATS used to send this email (e.g. "Eligo",
   "Workday", "SuccessFactors", "Ashby"), if identifiable. null if unknown.
"""


# ---------- Helpers ----------

def _parse_date(date_header):
    try:
        return parsedate_to_datetime(date_header).strftime("%Y-%m-%d")
    except Exception:
        return ""


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def _normalize_company(name):
    if not name:
        return ""
    name = unicodedata.normalize("NFKC", str(name)).casefold()
    name = re.sub(r"[^\w\s]", " ", name)
    words = name.split()

    # Remove common legal suffixes without making punctuation or spacing
    # differences prevent a match (e.g. "BCG Platinion GmbH" vs "BCG Platinion").
    suffixes = {
        "gmbh", "ag", "group", "inc", "ltd", "limited", "se", "kg",
        "co", "llc", "plc", "corp", "corporation",
    }
    while words and words[-1] in suffixes:
        words.pop()
    return " ".join(words)


# ---------- Ask OpenAI about one email ----------

def classify_email(email):
    email_text = (
        f"From: {email['from']}\n"
        f"Subject: {email['subject']}\n"
        f"Date: {email['date']}\n\n"
        f"Body:\n{email['body'][:MAX_BODY_CHARS]}"
    )

    response = client.chat.completions.create(
        model=MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": email_text},
        ],
    )

    data = json.loads(response.choices[0].message.content)

    return {
        "email_id": email["id"],
        "email_type": data.get("email_type") or "irrelevant",
        "company": data.get("company") or "Unknown",
        "job_title": data.get("job_title") or "Unknown",
        "location": data.get("location") or "",
        "platform": data.get("platform") or "",
        "date_applied": _parse_date(email["date"]),
        "subject": email["subject"],
        "from": email["from"],
    }


# ---------- Find the existing application row this update belongs to ----------

def find_matching_application(cache, update):
    target_company = _normalize_company(update["company"])
    if not target_company or target_company == "unknown":
        return None

    applications = [
        (eid, entry)
        for eid, entry in cache.items()
        if entry.get("is_application")
        and not entry.get("deleted")
    ]
    if not applications:
        return None

    # Prefer exact normalized names. If the email classifier returns a short
    # form such as "BCG" while the saved row says "BCG Platinion", allow a
    # token-subset match only when it identifies exactly one existing row.
    exact_candidates = [
        (eid, entry)
        for eid, entry in applications
        if _normalize_company(entry.get("company", "")) == target_company
    ]
    candidates = exact_candidates
    if not candidates:
        target_words = set(target_company.split())
        related_candidates = []
        if target_words:
            for eid, entry in applications:
                company_words = set(_normalize_company(entry.get("company", "")).split())
                if company_words and (target_words <= company_words or company_words <= target_words):
                    related_candidates.append((eid, entry))
        if len(related_candidates) != 1:
            return None
        candidates = related_candidates

    update_title = (update.get("job_title") or "").lower()
    if update_title and update_title != "unknown":
        title_matches = [
            (eid, e) for eid, e in candidates
            if update_title in (e.get("job_title") or "").lower()
            or (e.get("job_title") or "").lower() in update_title
        ]
        if title_matches:
            candidates = title_matches

    candidates.sort(key=lambda pair: pair[1].get("date_applied") or "", reverse=True)
    return candidates[0]


def apply_update(cache, update):
    """
    Applies a rejection/interview/assessment/offer email.
    This function is responsible for EVERY cache write related to this email —
    process_messages must not write to cache[update['email_id']] afterward.
    """
    new_status = STATUS_MAP.get(update["email_type"])
    if not new_status:
        cache[update["email_id"]] = {
            "email_id": update["email_id"], "is_application": False, "email_type": update["email_type"],
        }
        return "no status change"

    match = find_matching_application(cache, update)

    if match is None:
        # No existing row for this company — this update becomes the row itself
        cache[update["email_id"]] = {
            "email_id": update["email_id"],
            "is_application": True,
            "company": update["company"],
            "job_title": update["job_title"],
            "location": update["location"],
            "platform": update["platform"],
            "date_applied": update["date_applied"],
            "status": new_status,
            "subject": update["subject"],
            "from": update["from"],
            "notes": "",
        }
        return f"new row created directly as {new_status}"

    target_id, target = match

    if target.get("status") in TERMINAL_STATUSES:
        cache[update["email_id"]] = {
            "email_id": update["email_id"], "is_application": False,
            "email_type": update["email_type"], "matched_to": target_id,
        }
        return f"skipped (already {target['status']})"

    target["status"] = new_status
    if target.get("job_title", "Unknown") == "Unknown" and update["job_title"] != "Unknown":
        target["job_title"] = update["job_title"]
    if not target.get("platform") and update["platform"]:
        target["platform"] = update["platform"]

    # Mark this update email as processed, pointing at the row it updated
    cache[update["email_id"]] = {
        "email_id": update["email_id"], "is_application": False,
        "email_type": update["email_type"], "matched_to": target_id,
    }

    return f"updated {target.get('company')} -> {new_status}"


# ---------- Process many emails (skips ones already done) ----------

def process_messages(service, message_ids):
    cache = load_cache()
    # Older versions could mark a status email as processed without creating
    # an application row. Retry those orphaned status emails so they can be
    # matched to an application or become a standalone row.
    recoverable_ids = [
        i for i in message_ids
        if i in cache
        and not cache[i].get("is_application")
        and cache[i].get("email_type") in STATUS_MAP
        and not cache[i].get("matched_to")
    ]
    new_ids = [i for i in message_ids if i not in cache] + recoverable_ids
    print(
        f"{len(message_ids)} emails total, "
        f"{len(new_ids) - len(recoverable_ids)} new to process, "
        f"{len(recoverable_ids)} old status emails to recover"
    )

    for n, msg_id in enumerate(new_ids, start=1):
        try:
            email = get_email_details(service, msg_id)
            data = classify_email(email)

            if data["email_type"] == "new_application":
                cache[msg_id] = {
                    "email_id": msg_id,
                    "is_application": True,
                    "company": data["company"],
                    "job_title": data["job_title"],
                    "location": data["location"],
                    "platform": data["platform"],
                    "date_applied": data["date_applied"],
                    "status": "Applied",
                    "subject": data["subject"],
                    "from": data["from"],
                    "notes": "",
                }
                print(f"[{n}/{len(new_ids)}] ✅ new application: {data['company']} - {data['job_title']}")

            elif data["email_type"] == "irrelevant":
                cache[msg_id] = {"email_id": msg_id, "is_application": False, "email_type": "irrelevant"}
                print(f"[{n}/{len(new_ids)}] ⏭️  skipped (irrelevant)")

            else:
                result = apply_update(cache, data)
                print(f"[{n}/{len(new_ids)}] 🔄 {data['email_type']} for {data['company']}: {result}")

        except Exception as e:
            print(f"[{n}/{len(new_ids)}] ❌ error on {msg_id}: {e}")

        if n % 10 == 0:
            save_cache(cache)

    save_cache(cache)
    return [a for a in cache.values() if a.get("is_application") and not a.get("deleted")]


# ---------- Quick test: run this file directly ----------

if __name__ == "__main__":
    service = get_gmail_service()
    ids = search_emails(service, build_query())

    applications = process_messages(service, ids)

    print(f"\nReal applications found: {len(applications)}\n")
    for app in applications:
        print(f"{app.get('date_applied')} | {app.get('company')} | {app.get('job_title')} | {app.get('status')}")
