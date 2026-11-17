"""The onboarding process: seven stages, run in order, each one recorded.

  1 Intake            is everything we need actually here?
  2 Read documents    what do the attachments really say? (AI reads)
  3 Format checks     is each ID / bank detail valid for the claimed country?
  4 Cross-reference   do the form and the documents agree with each other?
  5 Risk screening    blocklist, duplicates, bank-detail changes
  6 Decision          approved / pending / rejected, with the reasons
  7 Communication     tell the vendor (and the reviewer) what happens next

Every check produces a finding with a severity:
  pass, info       nothing to do
  vendor_fix       the vendor can fix it  -> PENDING, waiting on vendor
  review           needs human judgement  -> PENDING, needs review
  reject           hard policy stop       -> REJECTED
The decision is simply the most serious finding. No scoring, no AI vote.
"""
import os
import time
import traceback

from . import db, extract, messages, rules
from .rules import norm_id

STAGES = [
    ("intake", "Intake & completeness", "Is everything we need actually here?"),
    ("read", "Read documents", "What is each attachment really, and what does it say?"),
    ("format", "Format checks", "Is each ID and bank detail valid for the claimed country?"),
    ("xref", "Cross-reference", "Do the form and the documents agree with each other?"),
    ("risk", "Risk screening", "Blocklist, duplicates and bank-detail changes."),
    ("decide", "Decision", "Approved, pending or rejected, with the reasons."),
    ("notify", "Communication", "Tell the vendor and the reviewer what happens next."),
]

# Minimum time each stage stays on screen so a viewer can follow the run.
# Purely presentational: real compute time is recorded separately.
PACE_MS = int(os.environ.get("STAGE_PACE_MS", "500"))

REQUIRED_FIELDS = [
    ("legal_name", "Registered legal name"), ("country", "Country"),
    ("tax_id", "Tax registration number"), ("address", "Registered address"),
    ("state_region", "State / region"), ("contact_name", "Contact name"),
    ("contact_email", "Contact email"), ("bank_account_holder", "Bank account holder name"),
    ("bank_account", "Bank account number / IBAN"),
]
SEV_ORDER = {"pass": 0, "info": 1, "vendor_fix": 2, "review": 3, "reject": 4}


def n(count, word, plural=None):
    return f"{count} {word if count == 1 else (plural or word + 's')}"


def a(noun):
    return ("an " if noun[0] in "aeiou" else "a ") + noun


def F(code, severity, title, detail, ask=None):
    return {"code": code, "severity": severity, "title": title, "detail": detail, "ask": ask}


class Run:
    def __init__(self, rid):
        self.rid = rid
        self.row = db.get_run(rid)
        self.sub = self.row["submission"]
        self.stages = [{"key": k, "title": t, "question": q, "status": "waiting", "findings": [],
                        "summary": "", "work_ms": None} for k, t, q in STAGES]
        self.docs = {}
        self.findings = []
        self.save()

    def save(self):
        db.update_run(self.rid, stages=self.stages)

    def stage(self, key, fn):
        st = next(s for s in self.stages if s["key"] == key)
        st["status"] = "running"
        self.save()
        t0 = time.time()
        out = fn() or {}
        work = int((time.time() - t0) * 1000)
        if work < PACE_MS:
            time.sleep((PACE_MS - work) / 1000)
        found = out.get("findings", [])
        self.findings += found
        worst = max([SEV_ORDER[f["severity"]] for f in found], default=0)
        st.update(status=out.get("status") or {0: "pass", 1: "pass", 2: "action", 3: "review", 4: "stop"}[worst],
                  findings=found, summary=out.get("summary", ""), work_ms=work,
                  extra=out.get("extra"))
        self.save()

    # ------------------------------------------------------------ 1 intake
    def intake(self):
        s, out = self.sub, []
        missing = [label for key, label in REQUIRED_FIELDS if not (s.get(key) or "").strip()]
        country = s.get("country")
        if country == "IN" and not (s.get("bank_code") or "").strip():
            missing.append("IFSC code")
        if country == "US" and not (s.get("bank_code") or "").strip():
            missing.append("Bank routing number")
        for label in missing:
            out.append(F("MISSING_FIELD", "vendor_fix", f"Missing: {label}",
                         f"The form was submitted without '{label}'.", f"Please provide: {label}."))
        if s.get("contact_email") and not rules.valid_email(s["contact_email"]):
            out.append(F("BAD_EMAIL", "vendor_fix", "Contact email is not a valid address",
                         f"'{s['contact_email']}' is not a usable email address.",
                         "Please provide a valid contact email address."))
        for slot, label in (("tax_doc", "Tax registration certificate"), ("bank_doc", "Bank proof (bank letter or cancelled cheque)")):
            if not s.get(slot):
                out.append(F("MISSING_DOC", "vendor_fix", f"Missing document: {label}",
                             "No file was attached in this slot.", f"Please attach: {label}."))
        if country and country not in rules.SUPPORTED_COUNTRIES:
            out.append(F("UNSUPPORTED_COUNTRY", "review", f"Country '{country}' is outside automated coverage",
                         "Automated rules exist for India, the US, the UK and Germany. Rather than guess, this goes to a person."))
        parent = self.row.get("parent_id")
        if parent:
            out.append(F("RESUBMISSION", "info", f"Resubmission of {parent}",
                         "The vendor is responding to an earlier request. Every check is re-run from scratch; nothing is assumed from the first attempt."))
        if not [f for f in out if f["severity"] != "info"]:
            out.append(F("COMPLETE", "pass", "All required fields and both documents are present", ""))
        return {"findings": out, "summary": n(len(missing), "field") + " missing" if missing else "Complete"}

    # ------------------------------------------------------------ 2 read
    def read(self):
        out, engines, cards = [], set(), []
        expected = {"tax_doc": "tax_registration_certificate", "bank_doc": "bank_proof"}
        for slot, want in expected.items():
            path = self.sub.get(slot)
            if not path:
                continue
            doc = extract.read_document(path)
            self.docs[slot] = doc
            engines.add(doc["engine"])
            got = doc["doc_type"]
            want_label, got_label = extract.DOC_TYPE_LABEL[want], extract.DOC_TYPE_LABEL.get(got, got)
            cards.append({"slot": want_label, "file": os.path.basename(path).split("__", 1)[-1],
                          "read_as": got_label, "engine": doc["engine"], "summary": doc.get("summary"),
                          "note": doc.get("engine_note"),
                          "fields": {k: doc.get(k) for k in extract.FIELDS if doc.get(k)}})
            if got == "unreadable":
                out.append(F("DOC_UNREADABLE", "review", f"Could not read the {want_label}",
                             "The file has no machine-readable text and the AI reader was not available. A person needs to open it."))
                self.docs[slot] = None
            elif got != want:
                out.append(F("WRONG_DOC", "vendor_fix",
                             f"Wrong document: expected a {want_label}, received {a(got_label)}",
                             f"The file in the '{want_label}' slot is {a(got_label)}. Any numbers printed on it are not accepted as proof, even if they match the form.",
                             f"The file you attached as your {want_label} is {a(got_label)}. Please send the {want_label} itself."))
                self.docs[slot] = None  # do not trust values from the wrong document
            else:
                out.append(F("DOC_OK", "pass", f"{want_label.capitalize()} recognised", doc.get("summary") or ""))
        return {"findings": out, "summary": "Read by " + " + ".join(sorted(engines)) if engines else "No documents to read",
                "extra": {"documents": cards}}

    # ------------------------------------------------------------ 3 format
    def format(self):
        s, out = self.sub, []
        c = s.get("country")
        if c not in rules.SUPPORTED_COUNTRIES:
            return {"findings": [F("FORMAT_SKIPPED", "info", "Format checks skipped", "No automated format rules for this country.")],
                    "summary": "Skipped"}
        label = rules.TAX_ID_LABEL[c]
        if s.get("tax_id"):
            ok, msg = rules.check_tax_id(c, s["tax_id"])
            if ok:
                out.append(F("TAX_ID_OK", "pass", f"{label} is valid for {rules.SUPPORTED_COUNTRIES[c]}", msg))
            else:
                others = rules.looks_like_other_country(c, s["tax_id"])
                if others:
                    o = others[0]
                    out.append(F("TAX_ID_WRONG_COUNTRY", "vendor_fix",
                                 f"Tax ID looks like a {rules.SUPPORTED_COUNTRIES[o]} {rules.TAX_ID_LABEL[o]}, but the country given is {rules.SUPPORTED_COUNTRIES[c]}",
                                 f"'{s['tax_id']}' does not fit the {label} format. {msg}",
                                 f"The tax number you gave looks like a {rules.SUPPORTED_COUNTRIES[o]} {rules.TAX_ID_LABEL[o]}, but you selected {rules.SUPPORTED_COUNTRIES[c]}. Please confirm your country of registration and send the matching tax number."))
                else:
                    out.append(F("TAX_ID_BAD", "vendor_fix", f"{label} is not valid", f"'{s['tax_id']}': {msg}",
                                 f"Please re-check your {label}. {msg}"))
        if c == "IN" and s.get("pan"):
            pan = norm_id(s["pan"])
            if rules.PAN_RE.match(pan):
                out.append(F("PAN_OK", "pass", "PAN format is valid", f"Entity type letter '{pan[3]}' = {rules.PAN_ENTITY[pan[3]]}."))
            else:
                out.append(F("PAN_BAD", "vendor_fix", "PAN format is not valid", f"'{s['pan']}' is not a 10-character PAN.",
                             "Please re-check your PAN (10 characters, e.g. AAAAA9999A)."))
        if s.get("bank_account"):
            for ok, what, msg in rules.check_bank(c, s.get("bank_account"), s.get("bank_code")):
                if what in ("IFSC", "Routing number") and not (s.get("bank_code") or "").strip():
                    continue  # already reported as missing at intake
                out.append(F("BANK_FMT_OK" if ok else "BANK_FMT_BAD", "pass" if ok else "vendor_fix",
                             f"{what} is valid" if ok else f"{what} is not valid", msg,
                             None if ok else f"Please re-check your {what}. {msg}"))
        bad = len([f for f in out if f["severity"] != "pass"])
        return {"findings": out, "summary": n(bad, "format problem") if bad else "Every field is individually valid"}

    # ------------------------------------------------------------ 4 xref
    def xref(self):
        s, out = self.sub, []
        c = s.get("country")
        tax, bank = self.docs.get("tax_doc"), self.docs.get("bank_doc")
        legal = s.get("legal_name") or ""

        def compare_name(a_label, a, b_label, b, on_bad, code, ask):
            score, how = rules.name_similarity(a, b)
            verdict = rules.name_verdict(score)
            detail = f"{a_label}: '{a}'. {b_label}: '{b}'. Result: {how}."
            if verdict == "match":
                out.append(F(code + "_OK", "pass", f"{b_label} matches {a_label.lower()}", detail))
            elif verdict == "near":
                out.append(F(code + "_NEAR", "review", f"{b_label} is close to, but not the same as, {a_label.lower()}",
                             detail + " Too close to call automatically."))
            else:
                out.append(F(code + "_MISMATCH", on_bad, f"{b_label} does not match {a_label.lower()}", detail, ask))

        # --- tax certificate vs form
        if tax:
            if tax.get("legal_name"):
                compare_name("Legal name on form", legal, "Name on tax certificate", tax["legal_name"], "vendor_fix", "TAXDOC_NAME",
                             f"The tax certificate you sent is registered to '{tax['legal_name']}', but you are applying as '{legal}'. Please send the certificate for the applying company, or correct the legal name.")
            if tax.get("tax_id") and s.get("tax_id"):
                a, b = norm_id(s["tax_id"]), norm_id(tax["tax_id"])
                if a == b:
                    out.append(F("TAXDOC_ID_OK", "pass", "Tax ID on form matches the certificate", f"Both show {b}."))
                else:
                    diff = sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))
                    hint = " They differ by one character, which looks like a typing error." if diff == 1 else ""
                    out.append(F("TAXDOC_ID_MISMATCH", "vendor_fix", "Tax ID on form differs from the certificate",
                                 f"Form: {a}. Certificate: {b}.{hint}",
                                 f"The tax number on your form ({a}) is different from the one on your certificate ({b}). Please confirm which is correct."))
            elif s.get("tax_id"):
                out.append(F("TAXDOC_ID_ABSENT", "vendor_fix", "The certificate does not show a tax ID",
                             "No tax registration number could be read from the certificate.",
                             "Your tax certificate does not show the registration number clearly. Please send a clearer copy."))
        # --- India: the GSTIN carries information that must agree with other fields
        if c == "IN" and s.get("tax_id") and rules.check_gstin(s["tax_id"])[0]:
            g = norm_id(s["tax_id"])
            gst_state, gst_pan = rules.GST_STATES[g[:2]], g[2:12]
            if s.get("pan"):
                pan = norm_id(s["pan"])
                if pan == gst_pan:
                    out.append(F("PAN_GSTIN_OK", "pass", "PAN matches the PAN embedded in the GSTIN", f"Both are {pan}."))
                else:
                    extra = ""
                    exp = rules.expected_pan_entity(legal)
                    if rules.PAN_RE.match(pan) and exp and pan[3] != exp:
                        extra = f" That PAN's type letter '{pan[3]}' means {rules.PAN_ENTITY[pan[3]]}, but the applicant is a {rules.PAN_ENTITY[exp]}."
                    out.append(F("PAN_GSTIN_MISMATCH", "vendor_fix", "PAN on form is not the PAN inside the GSTIN",
                                 f"Characters 3 to 12 of a GSTIN are the holder's PAN. GSTIN contains {gst_pan}; form says {pan}.{extra}",
                                 f"The PAN you entered ({pan}) is not the PAN your GST registration is issued under ({gst_pan}).{extra} Please send the PAN of the business itself."))
            state = (s.get("state_region") or "").strip()
            if state:
                if rules.normalise_name(state) == rules.normalise_name(gst_state):
                    out.append(F("STATE_GSTIN_OK", "pass", "Address state matches the GSTIN state code", f"GSTIN state code {g[:2]} = {gst_state}."))
                else:
                    out.append(F("STATE_GSTIN_MISMATCH", "vendor_fix", f"Address is in {state}, but the GSTIN is registered in {gst_state}",
                                 f"GST registration is per state. State code {g[:2]} means {gst_state}; the form's address is in {state}.",
                                 f"Your GSTIN is registered in {gst_state}, but the address on your form is in {state}. GST registration is per state: please send the GSTIN for {state}, or give us the registered {gst_state} address."))
        # --- bank
        holder = s.get("bank_account_holder") or ""
        if holder and legal:
            score, how = rules.name_similarity(legal, holder)
            verdict = rules.name_verdict(score)
            trading = s.get("trading_name") or ""
            if verdict != "match" and trading and rules.name_verdict(rules.name_similarity(trading, holder)[0]) == "match":
                out.append(F("BANK_HOLDER_TRADING", "pass", "Bank account is held in the declared trading name",
                             f"Account holder '{holder}' matches trading name '{trading}'."))
            elif verdict == "match":
                out.append(F("BANK_HOLDER_OK", "pass", "Bank account holder matches the legal name",
                             f"Legal name: '{legal}'. Account holder: '{holder}'. Result: {how}."))
            else:
                out.append(F("BANK_HOLDER_MISMATCH", "review", "Bank account is not held in the vendor's name",
                             f"Legal name: '{legal}'. Account holder: '{holder}'. Result: {how}. Paying a business into someone else's account is a classic payment-fraud pattern."))
        if bank:
            if bank.get("account_holder") and holder:
                compare_name("Account holder on form", holder, "Account holder on bank proof", bank["account_holder"], "review", "BANKDOC_HOLDER", None)
            if bank.get("account_number") and s.get("bank_account"):
                a, b = norm_id(s["bank_account"]), norm_id(bank["account_number"])
                if a == b:
                    out.append(F("BANKDOC_ACCT_OK", "pass", "Account number on form matches the bank proof", f"Both end in {b[-4:]}."))
                else:
                    out.append(F("BANKDOC_ACCT_MISMATCH", "vendor_fix", "Account number on form differs from the bank proof",
                                 f"Form ends {a[-4:]}; bank proof ends {b[-4:]}.",
                                 "The account number on your form is different from the one on your bank proof. Please confirm the correct account and send matching proof."))
            elif s.get("bank_account"):
                out.append(F("BANKDOC_ACCT_ABSENT", "vendor_fix", "The bank proof does not show an account number",
                             "No account number could be read from the bank proof.",
                             "Your bank proof does not show the account number clearly. Please send a bank letter or cancelled cheque that shows it."))
        if c in rules.USES_IBAN and s.get("bank_account") and rules.check_iban(s["bank_account"])[0]:
            icc = norm_id(s["bank_account"])[:2]
            if icc != c:
                out.append(F("BANK_COUNTRY", "review", f"Bank account is in a different country ({icc}) from the vendor ({c})",
                             "Legitimate for some groups, but also how diverted payments leave the country. A person should confirm."))
        email = (s.get("contact_email") or "").lower()
        if "@" in email and email.split("@")[1] in rules.FREE_MAIL:
            out.append(F("FREE_MAIL", "info", "Contact uses a free email provider",
                         f"'{email}' is not a company domain. Not a problem on its own; noted for the reviewer."))
        bad = len([f for f in out if f["severity"] not in ("pass", "info")])
        return {"findings": out, "summary": n(bad, "inconsistency", "inconsistencies") + " found" if bad else "Form and documents agree"}

    # ------------------------------------------------------------ 5 risk
    def risk(self):
        s, out = self.sub, []
        tax_id, acct = norm_id(s.get("tax_id")), norm_id(s.get("bank_account"))
        legal = s.get("legal_name") or ""
        for b in db.blocklist():
            score, _ = rules.name_similarity(legal, b["legal_name"])
            if b["tax_id"] and tax_id and norm_id(b["tax_id"]) == tax_id:
                same = "" if score >= 0.99 else f" The name has changed ('{b['legal_name']}' then, '{legal}' now) but the tax ID is the same."
                out.append(F("BLOCKLIST_TAX_ID", "reject", "Tax ID is on the internal blocklist",
                             f"Exact tax ID match with blocklisted entity '{b['legal_name']}'. Reason on file: {b['reason']}{same}"))
            elif score >= rules.NAME_MATCH:
                out.append(F("LIST_NAME_HIT", "review", f"Name resembles a {b['kind'].replace('_', ' ')} entry",
                             f"'{legal}' vs '{b['legal_name']}' ({int(score * 100)}% similar). Name-only hits are often false positives, so this goes to a person instead of being rejected."))
        for v in db.vendors():
            if tax_id and v["tax_id"] == tax_id:
                if acct and v["bank_account"] == acct:
                    out.append(F("DUPLICATE", "reject", f"Already an active vendor ({v['id']})",
                                 f"Same tax ID and same bank account as {v['id']} {v['legal_name']}. Creating a second record would allow duplicate payments."))
                else:
                    out.append(F("BANK_CHANGE", "review", f"Existing vendor {v['id']} submitting NEW bank details",
                                 f"Tax ID matches active vendor {v['id']} {v['legal_name']}, but the bank account is different from the one on file (ending {v['bank_account'][-4:]}). This is the standard shape of bank-detail-change fraud. Recommended: call the vendor on the number already on file ({v['phone_on_file']}), not the number in this submission."))
            elif acct and v["bank_account"] == acct:
                out.append(F("SHARED_BANK", "review", f"Bank account already belongs to another vendor ({v['id']})",
                             f"This account is on file for {v['id']} {v['legal_name']}, which has a different tax ID."))
        if not out:
            out.append(F("RISK_CLEAR", "pass", "No blocklist hit, no duplicate, no bank-detail conflict",
                         f"Screened against {len(db.blocklist())} list entries and {len(db.vendors())} active vendors."))
        hits = len([f for f in out if f["severity"] != "pass"])
        return {"findings": out, "summary": n(hits, "risk signal") if hits else "Clear"}

    # ------------------------------------------------------------ 6 decide
    def decide(self):
        decisive = [f for f in self.findings if f["severity"] in ("vendor_fix", "review", "reject")]
        worst = max([SEV_ORDER[f["severity"]] for f in decisive], default=0)
        passed = len([f for f in self.findings if f["severity"] == "pass"])
        if worst == 4:
            status, lane = "rejected", "policy"
            headline = "Rejected: " + next(f["title"] for f in decisive if f["severity"] == "reject")
        elif worst == 3:
            status, lane = "pending", "review"
            n = len([f for f in decisive if f["severity"] == "review"])
            headline = f"Pending: {n} risk signal{'s' if n != 1 else ''} need{'s' if n == 1 else ''} a human decision"
        elif worst == 2:
            status, lane = "pending", "vendor"
            headline = f"Pending: waiting on the vendor for {len(decisive)} item{'s' if len(decisive) != 1 else ''}"
        else:
            status, lane = "approved", "auto"
            headline = f"Approved automatically: all {passed} checks passed"
        self.decision = {"status": status, "lane": lane, "headline": headline, "decisive": decisive,
                         "checks_passed": passed, "checks_total": passed + len(decisive)}
        parent = self.row.get("parent_id")
        if parent:
            prow = db.get_run(parent)
            before = {f["code"] for f in (prow.get("decision") or {}).get("decisive", [])} if prow else set()
            still = {f["code"] for f in decisive}
            self.decision["resubmission"] = {"parent": parent, "resolved": len(before - still), "still_open": len(before & still)}
        f = F("DECISION", "info", headline,
              "Rule: the outcome is the most serious finding. Reject beats human review, which beats vendor fix, which beats approve.")
        return {"findings": [f], "summary": status.upper(),
                "status": {"auto": "pass", "vendor": "action", "review": "review", "policy": "stop"}[lane]}

    # ------------------------------------------------------------ 7 notify
    def notify(self):
        d, s = self.decision, self.sub
        out = []
        if d["status"] == "approved":
            vid = db.add_vendor(s["legal_name"], s["country"], s["tax_id"], s["bank_account"],
                                s.get("contact_phone") or "", f"onboarded by run {self.rid}")
            d["vendor_id"] = vid
            out.append(F("VENDOR_CREATED", "pass", f"Vendor record {vid} created in the vendor master", ""))
        for m in messages.build(self.rid, s, d):
            db.add_outbox(self.rid, **m)
            who = "vendor" if m["audience"] == "vendor" else "internal reviewer"
            out.append(F("MESSAGE", "pass", f"Message to {who}: {m['delivery']}", f"Subject: {m['subject']} (written by {m['written_by']})"))
        return {"findings": out, "summary": n(len(out), "action")}

    def work_ms(self):
        """Real processing time, excluding the on-screen pacing."""
        return sum(s["work_ms"] or 0 for s in self.stages)

    def execute(self):
        try:
            for key, _, _ in STAGES:
                self.stage(key, getattr(self, key))
            db.update_run(self.rid, state="done", status=self.decision["status"], lane=self.decision["lane"],
                          decision=self.decision, finished_at=db.now(), duration_ms=self.work_ms())
        except Exception as e:  # never leave a run hanging: fail safe to a human
            traceback.print_exc()
            for st in self.stages:
                if st["status"] == "running":
                    st.update(status="review", summary="This step hit an unexpected error",
                              findings=[F("STAGE_ERROR", "review", "Unexpected error in this step", f"{type(e).__name__}: {e}")])
                elif st["status"] == "waiting":
                    st["status"] = "skipped"
            decision = {"status": "pending", "lane": "review",
                        "headline": "Pending: the process hit an error, so a person must review this submission",
                        "decisive": [F("STAGE_ERROR", "review", "Automated processing did not complete", f"{type(e).__name__}: {e}")],
                        "checks_passed": 0, "checks_total": 0}
            db.update_run(self.rid, stages=self.stages, state="done", status="pending", lane="review",
                          decision=decision, finished_at=db.now(), duration_ms=self.work_ms())


def execute(rid):
    Run(rid).execute()


def apply_review(rid, action, reviewer, note):
    """A human closes out a run that was waiting for review."""
    row = db.get_run(rid)
    if not row or row["status"] != "pending" or row["lane"] != "review":
        raise ValueError("This run is not waiting for review.")
    s, d = row["submission"], row["decision"]
    review = {"action": action, "reviewer": reviewer or "Reviewer", "note": note or "", "at": db.now()}
    if action == "approve":
        existing = next((v for v in db.vendors() if v["tax_id"] == norm_id(s.get("tax_id"))), None)
        if existing:
            db.update_vendor_bank(existing["id"], s["bank_account"], f"bank details updated after review of {rid}")
            review["effect"] = f"Bank details updated on {existing['id']}"
        else:
            vid = db.add_vendor(s["legal_name"], s["country"], s["tax_id"], s["bank_account"],
                                s.get("contact_phone") or "", f"approved after review of {rid}")
            review["effect"] = f"Vendor record {vid} created"
        status = "approved"
    else:
        review["effect"] = "No vendor record created or changed"
        status = "rejected"
    d["headline"] = f"{status.capitalize()} by {review['reviewer']} after review"
    d["status"] = status
    for m in messages.after_review(rid, s, status):
        db.add_outbox(rid, **m)
    db.update_run(rid, status=status, lane="human", decision=d, review=review)
    return db.get_run(rid)
