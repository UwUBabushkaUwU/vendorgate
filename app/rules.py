"""Deterministic validation rules.

Design principle: the AI model READS documents and DRAFTS messages.
It never decides. Every pass / fail below is plain code that gives the
same answer every time and can be audited line by line.
"""
import re
from difflib import SequenceMatcher

SUPPORTED_COUNTRIES = {
    "IN": "India",
    "US": "United States",
    "GB": "United Kingdom",
    "DE": "Germany",
}

# Name-match thresholds (0..1). Shown in the UI under "Policy".
NAME_MATCH = 0.90   # at or above: treated as the same entity
NAME_NEAR = 0.70    # between NEAR and MATCH: close, a human should look


def norm_id(value):
    """Strip spaces, dashes and dots, upper-case. 'de 123-456' -> 'DE123456'."""
    return re.sub(r"[\s\-\./]", "", value or "").upper()


# ---------------------------------------------------------------- India
_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PAN_RE = re.compile(r"^[A-Z]{3}[ABCFGHJLPT][A-Z][0-9]{4}[A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")

GST_STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "27": "Maharashtra", "29": "Karnataka", "30": "Goa", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry", "36": "Telangana",
    "37": "Andhra Pradesh",
}

PAN_ENTITY = {
    "C": "company", "P": "individual", "F": "firm / LLP",
    "H": "Hindu undivided family", "A": "association of persons",
    "T": "trust", "B": "body of individuals", "L": "local authority",
    "J": "artificial juridical person", "G": "government",
}


def gstin_check_char(first14):
    total = 0
    for i, ch in enumerate(first14):
        product = _B36.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _B36[(36 - total % 36) % 36]


def check_gstin(value):
    v = norm_id(value)
    if not GSTIN_RE.match(v):
        return False, "A GSTIN is 15 characters: 2-digit state code, 10-character PAN, entity number, 'Z', check character."
    if gstin_check_char(v[:14]) != v[14]:
        return False, "The GSTIN check character does not validate, which usually means a typing error."
    if v[:2] not in GST_STATES:
        return False, f"State code {v[:2]} is not a recognised GST state code."
    return True, f"Valid GSTIN. State code {v[:2]} ({GST_STATES[v[:2]]}), embedded PAN {v[2:12]}."


def expected_pan_entity(legal_name):
    """What PAN entity letter does the legal name imply? None if unclear."""
    n = normalise_name(legal_name)
    toks = n.split()
    if "llp" in toks:
        return "F"
    if "ltd" in toks or "pvt" in toks:
        return "C"
    return None


# ---------------------------------------------------------------- US
EIN_RE = re.compile(r"^\d{9}$")


def check_ein(value):
    v = norm_id(value)
    if not EIN_RE.match(v):
        return False, "A US EIN is 9 digits, written NN-NNNNNNN."
    return True, "Valid EIN format."


def check_aba(value):
    v = norm_id(value)
    if not re.match(r"^\d{9}$", v):
        return False, "A US routing number is 9 digits."
    d = [int(c) for c in v]
    total = 3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])
    if total % 10 != 0:
        return False, "The routing number fails its checksum, which usually means a typing error."
    return True, "Valid routing number (checksum passes)."


# ---------------------------------------------------------------- UK / DE
def check_gb_vat(value):
    v = norm_id(value)
    if not re.match(r"^GB\d{9}$", v):
        return False, "A UK VAT number is 'GB' followed by 9 digits."
    d = [int(c) for c in v[2:]]
    total = sum(w * x for w, x in zip(range(8, 1, -1), d[:7])) + d[7] * 10 + d[8]
    if total % 97 == 0 or (total + 55) % 97 == 0:
        return True, "Valid UK VAT number (checksum passes)."
    return False, "The UK VAT number fails its checksum, which usually means a typing error."


def check_de_vat(value):
    v = norm_id(value)
    if not re.match(r"^DE\d{9}$", v):
        return False, "A German VAT ID (USt-IdNr.) is 'DE' followed by 9 digits."
    product = 10
    for ch in v[2:10]:
        s = (int(ch) + product) % 10
        if s == 0:
            s = 10
        product = (2 * s) % 11
    check = 11 - product
    if check == 10:
        check = 0
    if check != int(v[10]):
        return False, "The German VAT ID fails its checksum, which usually means a typing error."
    return True, "Valid German VAT ID (checksum passes)."


IBAN_LENGTHS = {"DE": 22, "GB": 22, "LT": 20, "FR": 27, "NL": 18, "ES": 24,
                "IT": 27, "IE": 22, "BE": 16, "AT": 20, "CH": 21, "PL": 28}


def iban_check_digits(country, bban):
    raw = bban + country + "00"
    num = int("".join(str(int(c, 36)) for c in raw))
    return f"{98 - num % 97:02d}"


def check_iban(value):
    v = norm_id(value)
    if not re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$", v):
        return False, "An IBAN starts with a 2-letter country code and 2 check digits."
    cc = v[:2]
    if cc in IBAN_LENGTHS and len(v) != IBAN_LENGTHS[cc]:
        return False, f"An IBAN for {cc} has {IBAN_LENGTHS[cc]} characters; this one has {len(v)}."
    rearranged = v[4:] + v[:4]
    if int("".join(str(int(c, 36)) for c in rearranged)) % 97 != 1:
        return False, "The IBAN fails its checksum, which usually means a typing error."
    return True, f"Valid IBAN (checksum passes), issued in {cc}."


# ---------------------------------------------------------------- dispatch
TAX_ID_LABEL = {"IN": "GSTIN", "US": "EIN", "GB": "VAT number", "DE": "VAT ID (USt-IdNr.)"}
TAX_CHECK = {"IN": check_gstin, "US": check_ein, "GB": check_gb_vat, "DE": check_de_vat}
USES_IBAN = {"GB", "DE"}


def check_tax_id(country, value):
    return TAX_CHECK[country](value)


def looks_like_other_country(country, value):
    """If the ID is wrong for the claimed country, is it right for another?"""
    hits = []
    for cc, fn in TAX_CHECK.items():
        if cc == country:
            continue
        if cc == "US":  # 9 bare digits is too generic to be a useful hint
            continue
        ok, _ = fn(value)
        v = norm_id(value)
        shaped = (cc == "IN" and GSTIN_RE.match(v)) or (cc in ("GB", "DE") and v.startswith(cc) and len(v) == 11)
        if ok or shaped:
            hits.append(cc)
    return hits


def check_bank(country, account, code):
    """Returns list of (ok, label, message)."""
    out = []
    acct = norm_id(account)
    if country in USES_IBAN:
        ok, msg = check_iban(acct)
        out.append((ok, "IBAN", msg))
    elif country == "IN":
        ok = bool(re.match(r"^\d{9,18}$", acct))
        out.append((ok, "Account number", "Valid account number length." if ok else "An Indian bank account number is 9 to 18 digits."))
        ok = bool(IFSC_RE.match(norm_id(code)))
        out.append((ok, "IFSC", "Valid IFSC format." if ok else "An IFSC is 11 characters: 4 letters, a zero, then 6 letters or digits."))
    elif country == "US":
        ok = bool(re.match(r"^\d{4,17}$", acct))
        out.append((ok, "Account number", "Valid account number length." if ok else "A US bank account number is 4 to 17 digits."))
        ok, msg = check_aba(code)
        out.append((ok, "Routing number", msg))
    return out


# ---------------------------------------------------------------- names
_WORD_MAP = {
    "private": "pvt", "limited": "ltd", "incorporated": "inc",
    "corporation": "corp", "company": "co", "pvt.": "pvt", "ltd.": "ltd",
}
LEGAL_TOKENS = {"pvt", "ltd", "inc", "corp", "co", "llc", "llp", "gmbh", "ag",
                "plc", "sa", "bv", "kg", "ug", "mbh", "the"}


def normalise_name(name):
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    toks = [_WORD_MAP.get(t, t) for t in s.split()]
    return " ".join(toks)


def core_name(name):
    return " ".join(t for t in normalise_name(name).split() if t not in LEGAL_TOKENS)


def name_similarity(a, b):
    """Score 0..1 plus a plain-English note on how it was reached."""
    if not a or not b:
        return 0.0, "one of the names is empty"
    na, nb = normalise_name(a), normalise_name(b)
    if na == nb:
        if a.strip().lower() == b.strip().lower():
            return 1.0, "identical"
        return 1.0, "identical once case, punctuation and legal suffixes are normalised (e.g. 'Private Limited' = 'Pvt Ltd')"
    ca, cb = core_name(a), core_name(b)
    if ca and ca == cb:
        return 0.95, "same business name; only the legal-form wording differs"
    direct = SequenceMatcher(None, ca, cb).ratio()
    sorted_ratio = SequenceMatcher(None, " ".join(sorted(ca.split())), " ".join(sorted(cb.split()))).ratio()
    score = round(max(direct, sorted_ratio), 2)
    return score, f"{int(score * 100)}% similar after normalisation"


def name_verdict(score):
    if score >= NAME_MATCH:
        return "match"
    if score >= NAME_NEAR:
        return "near"
    return "mismatch"


FREE_MAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "proton.me",
             "protonmail.com", "icloud.com", "rediffmail.com", "aol.com"}


def valid_email(value):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", value or ""))
