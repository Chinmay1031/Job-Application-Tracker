import os
import re
import base64
import html

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Read-only access: the app can never send, delete or change emails
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

SEARCH_PHRASES = [
    # ---------- New application confirmations ----------
    "your application",
    "successfully received your application",
    "received your application",
    "delighted to receive your application",
    "receive your application",
    "pleased with your application",
    "thank you for applying",
    "thanks for applying",
    "thank you for your application",
    "application received",
    "we have received your application",
    "application confirmation",

    # ---------- Rejections ----------
    "unfortunately",
    "we regret to inform",
    "regret to inform you",
    "not moving forward",
    "will not be moving forward",
    "not be progressing",
    "other candidates",
    "decided not to proceed",
    "decided to move forward with other candidates",
    "decided to pursue other candidates",
    "not successful",
    "not selected",
    "unable to move forward",
    "unable to progress your application",
    "wish you every success",
    "wish you all the best",
    "wish you the best of luck",
    "keep your application on file",
    "keep your resume on file",
    "after careful consideration",

    # ---------- Interviews ----------
    "invite you to an interview",
    "interview invitation",
    "schedule your interview",
    "schedule an interview",
    "schedule a call",
    "schedule a conversation",
    "book a time",
    "pick a time that works",
    "phone screen",
    "phone interview",
    "video interview",
    "virtual interview",
    "on-site interview",
    "first interview",
    "next round",
    "final round",
    "next steps in the process",
    "would love to speak with you",
    "would like to meet with you",

    # ---------- Assessments / case studies ----------
    "online assessment",
    "technical assessment",
    "skills assessment",
    "complete the assessment",
    "complete this task",
    "coding challenge",
    "coding test",
    "coding exercise",
    "take-home task",
    "take home assignment",
    "case study",
    "homework assignment",
    "aptitude test",

    # ---------- Offers ----------
    "pleased to offer",
    "job offer",
    "offer letter",
    "we are excited to offer",
    "extend an offer",
    "excited to welcome you",
    "offer of employment",
    "welcome to the team",

    # ---------- German ----------
    "vielen Dank für Ihre Bewerbung",
    "Eingang Ihrer Bewerbung",
    "Ihre Bewerbung",
    "Wir bedauern",
    "Absage",
    "leider",
    "andere Bewerber",
    "laden wir Sie ein",
    "Einladung zum Interview",
    "Vorstellungsgespräch",
    "Gespräch vereinbaren",
    "Online-Test",
    "Eignungstest",
    "wir freuen uns, Ihnen die Stelle anzubieten",
    "Vertragsangebot",

    # ---------- Dutch ----------
    "bedankt voor je sollicitatie",
    "we hebben je sollicitatie ontvangen",
    "je sollicitatie",
    "helaas",
    "we gaan niet verder met je sollicitatie",
    "niet verder in de procedure",
    "andere kandidaat",
    "uitnodigen voor een gesprek",
    "sollicitatiegesprek",
    "gesprek inplannen",
    "we bieden je graag de functie aan",

    # ---------- Swedish ----------
    "tack för din ansökan",
    "vi har tagit emot din ansökan",
    "din ansökan",
    "tyvärr",
    "vi går vidare med andra kandidater",
    "inte gå vidare",
    "intervju",
    "vi vill bjuda in dig till en intervju",
    "boka en intervju",
    "vi är glada att erbjuda dig",
]

# Search settings (spam and trash are always skipped by the Gmail API)
INBOX_ONLY = False         # True = only emails still in your Inbox (skips archived)
SKIP_PROMOTIONS = True     # True = skip the Promotions tab (recruiter mailers, newsletters)
AFTER_DATE = "2026/11/01"  # Only scan emails from this date onward (YYYY/MM/DD), or "" for no limit


def build_query():
    """Builds the Gmail search query from the phrases and settings above."""
    phrases = " OR ".join(f'"{p}"' for p in SEARCH_PHRASES)
    query = f"({phrases})"

    if INBOX_ONLY:
        query += " in:inbox"
    if SKIP_PROMOTIONS:
        query += " -category:promotions"
    if AFTER_DATE:
        query += f" after:{AFTER_DATE}"

    return query
# ---------- 1. Login ----------

def get_gmail_service():
    creds = None

    # Reuse the saved login if we have one
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise RefreshError("No valid token")
        except RefreshError:
            # Opens the browser so you can log in with Google
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)

        # Save the login for next time
        with open("token.json", "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ---------- 2. Search ----------

def search_emails(service, query, max_results=500):
    """Returns a list of message IDs that match the Gmail search query."""
    message_ids = []
    page_token = None

    while True:
        response = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=min(100, max_results - len(message_ids)),
            pageToken=page_token,
        ).execute()

        message_ids += [m["id"] for m in response.get("messages", [])]
        page_token = response.get("nextPageToken")

        if not page_token or len(message_ids) >= max_results:
            break

    return message_ids


# ---------- 3. Fetch one email ----------

def _decode(data):
    """Gmail sends the body as base64url text, so we decode it."""
    data += "=" * (-len(data) % 4)  # fix missing padding
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")


def _html_to_text(raw_html):
    raw_html = re.sub(r"<(script|style).*?</\1>", " ", raw_html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_body(payload):
    """Walks through the email parts and returns plain text (falls back to HTML)."""
    plain, html_part = None, None

    def walk(part):
        nonlocal plain, html_part
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")

        if mime == "text/plain" and data and plain is None:
            plain = _decode(data)
        elif mime == "text/html" and data and html_part is None:
            html_part = _decode(data)

        for sub in part.get("parts", []):
            walk(sub)

    walk(payload)

    if plain:
        return re.sub(r"\s+", " ", plain).strip()
    if html_part:
        return _html_to_text(html_part)
    return ""


def get_email_details(service, msg_id):
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}

    return {
        "id": msg_id,
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "body": _extract_body(msg["payload"]),
    }


# ---------- Quick test: run this file directly ----------

if __name__ == "__main__":
    service = get_gmail_service()
    query = build_query()
    print(f"Searching Gmail for: {query}")

    ids = search_emails(service, query)
    print(f"Found {len(ids)} emails\n")

    for msg_id in ids[:5]:
        email = get_email_details(service, msg_id)
        print("From:   ", email["from"])
        print("Subject:", email["subject"])
        print("Date:   ", email["date"])
        print("Body:   ", email["body"][:150], "...")
        print("-" * 60)