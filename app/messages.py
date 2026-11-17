"""What the process says, and to whom.

Two audiences, deliberately different:
  vendor    gets only what they need to act on, in plain language.
  reviewer  gets the full evidence and a recommended next step.

Risk signals are NEVER described to the vendor. If a submission looks like
fraud, telling the sender which check caught them teaches them to pass it
next time. Those messages come from a fixed template, never from the AI.
"""
import json
import os
import re
import smtplib
from email.message import EmailMessage

from . import extract

TEAM = os.environ.get("TEAM_NAME", "Vendor Onboarding Team")
BANNED = ["fraud", "suspicious", "blocklist", "blacklist", "sanction", "watchlist", "risk"]


def _base_url(s):
    return (os.environ.get("BASE_URL") or s.get("_base_url") or "").rstrip("/")


def _send(to_addr, subject, body):
    """Optional real email. Returns a delivery description."""
    host, user, pw = os.environ.get("SMTP_HOST"), os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    if not (host and user and pw):
        return "saved to outbox (email sending not switched on)"
    real_to = os.environ.get("DEMO_MAIL_TO") or to_addr
    try:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = os.environ.get("MAIL_FROM", user), real_to, subject
        msg.set_content(body)
        with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORT", "465")), timeout=10) as smtp:
            smtp.login(user, pw)
            smtp.send_message(msg)
        return f"emailed to {real_to}"
    except Exception as e:
        return f"saved to outbox (email failed: {type(e).__name__})"


def _ids(text):
    """Tokens that carry digits: reference numbers the AI must not alter."""
    return {t.strip(".,()'\"") for t in text.split() if re.search(r"\d", t)}


def _polish(asks, legal_name):
    """Ask Gemini to rewrite the requests in friendlier language.
    Accepted only if nothing was added, dropped or altered; else template."""
    prompt = (
        "Rewrite each request below so a busy, non-expert supplier contact understands exactly what to send. "
        "Keep every number, ID and name EXACTLY as written. One or two sentences each. "
        "Do not add requests, do not remove requests, do not speculate about reasons. "
        f"The supplier is {legal_name}.\n"
        'Return ONLY JSON: {"items": ["...", "..."]} with the same number of items in the same order.\n\n'
        + json.dumps(asks, indent=1))
    try:
        text, model = extract.gemini_call([{"text": prompt}], timeout=15.0)
        items = extract._parse_json(text)["items"]
        assert len(items) == len(asks)
        for src, new in zip(asks, items):
            assert isinstance(new, str) and 10 < len(new) < 500
            assert _ids(src) <= _ids(new), "an identifier was changed"
            assert not any(b in new.lower() for b in BANNED)
        return items, f"Gemini ({model}), verified by rules"
    except Exception:
        return asks, "template"


def _wrap(name, lines, ref):
    hello = f"Hello {name.split()[0]}," if name else "Hello,"
    return "\n".join([hello, "", *lines, "", f"Reference: {ref}", TEAM])


def build(rid, s, d):
    out = []
    to = s.get("contact_email") or "(no contact email given)"
    name, legal = s.get("contact_name") or "", s.get("legal_name") or "your company"
    decisive = d["decisive"]
    asks = [f["ask"] for f in decisive if f["severity"] == "vendor_fix" and f.get("ask")]
    link = f"{_base_url(s)}/?resubmit={rid}"

    if d["status"] == "approved":
        subject = f"{legal} is approved as a supplier"
        body = _wrap(name, [f"Good news: {legal} has passed onboarding and is now set up as a supplier (ID {d.get('vendor_id', '')}).",
                            "You can start sending invoices. Payments will go to the bank account ending "
                            f"{(s.get('bank_account') or '')[-4:]}."], rid)
        written = "template"
    elif d["status"] == "pending" and d["lane"] == "vendor":
        items, written = _polish(asks, legal)
        subject = f"Action needed to finish onboarding {legal}: {len(items)} item{'s' if len(items) != 1 else ''}"
        lines = [f"Thanks for your submission. To finish setting up {legal} we need {len(items)} thing{'s' if len(items) != 1 else ''} from you:", ""]
        lines += [f"  {i}. {t}" for i, t in enumerate(items, 1)]
        lines += ["", f"You will not need to re-enter anything else. Use this link to send only these items: {link}"]
        body = _wrap(name, lines, rid)
    elif d["status"] == "pending":  # review lane: fixed neutral wording, no AI
        subject = f"We have received your onboarding details for {legal}"
        lines = [f"Thank you. Your details for {legal} are with our onboarding team for a final check.",
                 "We will confirm the outcome within 2 working days."]
        if asks:
            lines += ["", "In the meantime, please send the following:", ""] + [f"  {i}. {t}" for i, t in enumerate(asks, 1)]
            lines += ["", f"Use this link to send the corrections: {link}"]
        else:
            lines.append("You do not need to do anything right now.")
        body = _wrap(name, lines, rid)
        written = "fixed template (AI not used for risk cases)"
    else:  # rejected
        if any(f["code"] == "DUPLICATE" for f in decisive):
            subject = f"{legal} is already registered as a supplier"
            body = _wrap(name, [f"{legal} is already set up as a supplier with the same tax and bank details, so no new record was created.",
                                "If something has changed, please reach your usual procurement contact."], rid)
        else:
            subject = f"Update on your onboarding application for {legal}"
            body = _wrap(name, ["We are unable to proceed with this supplier application.",
                                "If you believe this is a mistake, reply to this message and our procurement team will respond."], rid)
        written = "fixed template (AI not used for rejections)"
    out.append({"audience": "vendor", "to_addr": to, "subject": subject, "body": body,
                "delivery": _send(to, subject, body), "written_by": written})

    internal = [f for f in decisive if f["severity"] in ("review", "reject")]
    if internal:
        kind = "REVIEW NEEDED" if d["status"] == "pending" else "REJECTED (for your records)"
        lines = [f"{kind}: {legal} ({s.get('country')}), run {rid}", "", "Why:"]
        lines += [f"  - {f['title']}\n    {f['detail']}" for f in internal]
        if asks:
            lines += ["", f"Also waiting on the vendor for {len(asks)} fixable item{'s' if len(asks) != 1 else ''}."]
        lines += ["", "The vendor has been sent a neutral holding message that does not mention these findings."
                  if d["status"] == "pending" else "The vendor has been told only that we cannot proceed."]
        out.append({"audience": "reviewer", "to_addr": "Procurement review queue",
                    "subject": f"[{kind}] {legal}", "body": "\n".join(lines),
                    "delivery": "added to the review queue" if d["status"] == "pending" else "logged",
                    "written_by": "template"})
    return out


def after_review(rid, s, status):
    to, name, legal = s.get("contact_email") or "", s.get("contact_name") or "", s.get("legal_name") or "your company"
    if status == "approved":
        subject = f"{legal}: onboarding check complete"
        body = _wrap(name, [f"Our final check is complete and the details for {legal} are confirmed.",
                            "You do not need to do anything else."], rid)
    else:
        subject = f"Update on your onboarding application for {legal}"
        body = _wrap(name, ["We are unable to proceed with this request.",
                            "If you believe this is a mistake, reply to this message and our procurement team will respond."], rid)
    return [{"audience": "vendor", "to_addr": to, "subject": subject, "body": body,
             "delivery": _send(to, subject, body), "written_by": "fixed template (after human review)"}]
