"""Document reading.

Primary engine: Google Gemini (reads any PDF or image, including scans).
Fallback engine: a local text parser, used when no API key is set or the
Gemini call fails / times out. The fallback only understands text PDFs with
labelled fields, but it means a rate limit never breaks a live run.
The engine actually used is recorded on every run.
"""
import base64
import json
import os
import re

import httpx
from pypdf import PdfReader

DOC_TYPES = ["tax_registration_certificate", "bank_proof", "invoice",
             "incorporation_certificate", "identity_document", "other"]

DOC_TYPE_LABEL = {
    "tax_registration_certificate": "tax registration certificate",
    "bank_proof": "bank proof (bank letter or cancelled cheque)",
    "invoice": "invoice",
    "incorporation_certificate": "certificate of incorporation",
    "identity_document": "personal identity document",
    "other": "unrecognised document",
    "unreadable": "unreadable document",
}

FIELDS = ["legal_name", "tax_id", "secondary_tax_id", "address", "state_region",
          "account_holder", "account_number", "bank_code", "bank_name", "issue_date"]

PROMPT = """You are reading ONE document that a vendor uploaded during supplier onboarding.

Return ONLY a JSON object with exactly these keys:
{
  "doc_type": one of %s,
  "confidence": number 0..1 for how sure you are of doc_type,
  "legal_name": registered legal name of the business the document is about, or null,
  "tax_id": the main tax registration number printed (GSTIN, EIN, VAT number), or null,
  "secondary_tax_id": PAN if printed separately (India), else null,
  "address": registered address as printed, or null,
  "state_region": state / region from the address, or null,
  "account_holder": bank account holder name exactly as printed, or null,
  "account_number": bank account number or IBAN exactly as printed, or null,
  "bank_code": IFSC, ABA routing number or sort code, or null,
  "bank_name": name of the bank, or null,
  "issue_date": date of issue in YYYY-MM-DD, or null,
  "summary": one short sentence describing what this document is
}

Rules:
- Copy values exactly as printed. Never guess, correct or complete a value.
- If a value is not printed in the document, use null.
- An invoice is NOT a tax registration certificate even if it shows a tax number.
- For an invoice, legal_name is the business that ISSUED it.
""" % json.dumps(DOC_TYPES)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def gemini_models():
    first = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
    chain = [first, "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"]
    seen, out = set(), []
    for m in chain:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def gemini_key():
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


_working_model = {"name": None}


_no_thinking_cfg = set()  # models that rejected our thinking setting


def _thinking(model):
    """Reading a certificate needs no deep reasoning: keep thinking minimal for speed."""
    level = os.environ.get("GEMINI_THINKING", "minimal").strip().lower()
    if level in ("", "default", "off") or model in _no_thinking_cfg:
        return None
    if model.startswith("gemini-2.5"):
        return {"thinkingBudget": 0}
    return {"thinkingLevel": level}


def gemini_call(parts, want_json=True, timeout=40.0):
    """Call Gemini. Returns (text, model). Raises on failure."""
    key = gemini_key()
    if not key:
        raise RuntimeError("no GEMINI_API_KEY set")
    models = gemini_models()
    if _working_model["name"]:
        models = [_working_model["name"]]
    last = None
    for model in models:
        for attempt in (1, 2):
            gen = {"temperature": 0}
            if want_json:
                gen["responseMimeType"] = "application/json"
            think = _thinking(model)
            if think:
                gen["thinkingConfig"] = think
            body = {"contents": [{"parts": parts}], "generationConfig": gen}
            try:
                r = httpx.post(GEMINI_URL.format(model=model), json=body, timeout=timeout,
                               headers={"x-goog-api-key": key, "Content-Type": "application/json"})
            except Exception as e:  # network error / timeout
                raise RuntimeError(f"Gemini request failed: {type(e).__name__}")
            if r.status_code == 400 and think and attempt == 1:
                _no_thinking_cfg.add(model)  # this model rejects the setting: retry without it
                continue
            break
        if r.status_code == 404:  # model name not available on this key: try the next
            last = f"model {model} not found"
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Gemini returned HTTP {r.status_code}")
        data = r.json()
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]
                           if not p.get("thought"))
        except Exception:
            raise RuntimeError("Gemini returned no content")
        _working_model["name"] = model
        return text, model
    raise RuntimeError(last or "no Gemini model available")


def _parse_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    start, end = t.find("{"), t.rfind("}")
    return json.loads(t[start:end + 1])


def _mime(path):
    ext = os.path.splitext(path)[1].lower()
    return {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "application/pdf")


def _clean(result):
    out = {"doc_type": result.get("doc_type") if result.get("doc_type") in DOC_TYPES else "other",
           "confidence": float(result.get("confidence") or 0),
           "summary": (result.get("summary") or "")[:300]}
    for f in FIELDS:
        v = result.get(f)
        out[f] = str(v).strip() if v not in (None, "", "null") else None
    return out


def read_with_gemini(path):
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode()
    parts = [{"inline_data": {"mime_type": _mime(path), "data": data}}, {"text": PROMPT}]
    text, model = gemini_call(parts)
    out = _clean(_parse_json(text))
    out["engine"] = f"Gemini ({model})"
    return out


# ------------------------------------------------------------ local fallback
def _pdf_text(path):
    try:
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    except Exception:
        return ""


def _grab(text, labels):
    for label in labels:
        m = re.search(rf"(?<![A-Za-z]){label}\s*:\s*(.+)", text, flags=re.I)
        if m:
            return m.group(1).strip()
    return None


def read_locally(path):
    text = _pdf_text(path) if path.lower().endswith(".pdf") else ""
    if len(text.strip()) < 20:
        return {"doc_type": "unreadable", "confidence": 0.0, "engine": "local text parser",
                "summary": "No machine-readable text found (scan or image). Needs the AI reader or a human.",
                **{f: None for f in FIELDS}}
    low = text.lower()
    scores = {
        "invoice": sum(k in low for k in ["tax invoice", "invoice no", "bill to", "amount due", "subtotal", "qty"]),
        "tax_registration_certificate": sum(k in low for k in ["registration certificate", "certificate of registration", "form gst reg-06", "vat registration", "employer identification number", "umsatzsteuer", "date of liability"]),
        "bank_proof": sum(k in low for k in ["account holder", "ifsc", "iban", "cancelled cheque", "bank confirmation", "account confirmation", "routing number"]),
        "incorporation_certificate": sum(k in low for k in ["certificate of incorporation", "corporate identity number"]),
    }
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        best = "other"
    if scores.get("invoice", 0) >= 2:
        best = "invoice"
    out = {
        "doc_type": best,
        "confidence": 0.9 if best != "other" else 0.4,
        "legal_name": _grab(text, ["Legal Name", "Registered Name", "Name of Business", "Seller", "Account Holder Name", "Account Holder"]) if best != "bank_proof" else None,
        "tax_id": _grab(text, ["GSTIN", "Registration Number", "VAT Registration Number", "VAT Number", "USt-IdNr\\.?", "EIN", "Employer Identification Number"]),
        "secondary_tax_id": _grab(text, ["PAN"]),
        "address": _grab(text, ["Address of Principal Place of Business", "Registered Address", "Address"]),
        "state_region": _grab(text, ["State", "Region"]),
        "account_holder": _grab(text, ["Account Holder Name", "Account Holder", "Account Name"]),
        "account_number": _grab(text, ["IBAN", "Account Number", "Account No\\.?"]),
        "bank_code": _grab(text, ["IFSC Code", "IFSC", "Routing Number", "Sort Code"]),
        "bank_name": _grab(text, ["Bank Name", "Bank"]),
        "issue_date": _grab(text, ["Date of Issue", "Issue Date", "Date"]),
        "summary": f"Text PDF classified as {DOC_TYPE_LABEL[best]} from its headings and labels.",
        "engine": "local text parser",
    }
    return out


def read_document(path):
    """Try Gemini; fall back to the local parser. Never raises."""
    note = None
    if gemini_key():
        try:
            return read_with_gemini(path)
        except Exception as e:
            note = f"AI reader unavailable ({e}); used the local parser instead."
    else:
        note = "No AI key configured; used the local parser."
    out = read_locally(path)
    out["engine_note"] = note
    return out
