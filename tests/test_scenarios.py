"""End-to-end tests: every demo scenario, run through the real pipeline.
Run:  python -m pytest -q
Uses the local document parser (no API key needed) and a throwaway database."""
import os
import sys
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["STAGE_PACE_MS"] = "0"
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from app import db, pipeline, rules  # noqa: E402
from app.scenarios import BY_KEY  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")


def run(key, parent=None, **override):
    sc = BY_KEY[key]
    sub = dict(sc["form"])
    sub["tax_doc"] = os.path.join(SAMPLES, override.pop("tax_doc", sc["tax_doc"]))
    sub["bank_doc"] = os.path.join(SAMPLES, override.pop("bank_doc", sc["bank_doc"]))
    sub.update(override)
    rid = db.create_run(sub, scenario=sc["tag"], parent_id=parent)
    pipeline.execute(rid)
    return db.get_run(rid)


def codes(r):
    return {f["code"] for f in r["decision"]["decisive"]}


@pytest.fixture(autouse=True)
def fresh():
    db.init()
    db.reset()


def vendor_msg(r):
    return next(m for m in db.outbox_for(r["id"]) if m["audience"] == "vendor")


def test_happy_path_is_approved_and_vendor_created():
    r = run("happy")
    assert (r["status"], r["lane"]) == ("approved", "auto")
    assert r["decision"]["decisive"] == []
    assert any(v["legal_name"].startswith("Northwind") for v in db.vendors())
    assert all(s["status"] == "pass" for s in r["stages"])


def test_happy_path_twice_is_caught_as_duplicate():
    run("happy")
    r = run("happy")
    assert r["status"] == "rejected" and codes(r) == {"DUPLICATE"}


def test_subtle_inconsistency_goes_back_to_vendor_with_two_asks():
    r = run("subtle")
    assert (r["status"], r["lane"]) == ("pending", "vendor")
    assert codes(r) == {"PAN_GSTIN_MISMATCH", "STATE_GSTIN_MISMATCH"}
    fmt = next(s for s in r["stages"] if s["key"] == "format")
    assert fmt["status"] == "pass"  # every field is individually valid
    body = vendor_msg(r)["body"]
    assert "Karnataka" in body and "AAKFK9034L" in body and "?resubmit=" in body
    assert next(s for s in r["stages"] if s["key"] == "xref")["summary"] == "2 inconsistencies found"


def test_wrong_document_is_not_trusted_even_though_gstin_matches():
    r = run("wrongdoc")
    assert (r["status"], r["lane"]) == ("pending", "vendor")
    assert codes(r) == {"WRONG_DOC"}
    assert "invoice" in vendor_msg(r)["body"]


def test_resubmission_with_right_document_is_approved():
    first = run("wrongdoc")
    second = run("wrongdoc", parent=first["id"], tax_doc=BY_KEY["wrongdoc"]["fix"]["tax_doc"])
    assert (second["status"], second["lane"]) == ("approved", "auto")
    assert second["decision"]["resubmission"] == {"parent": first["id"], "resolved": 1, "still_open": 0}


def test_bank_change_on_existing_vendor_needs_human_and_vendor_is_not_tipped_off():
    r = run("bankchange")
    assert (r["status"], r["lane"]) == ("pending", "review")
    assert {"BANK_CHANGE", "BANK_COUNTRY"} <= codes(r)
    body = vendor_msg(r)["body"].lower()
    for word in ("fraud", "bank", "risk", "lithuania", "iban"):
        assert word not in body
    reviewer = next(m for m in db.outbox_for(r["id"]) if m["audience"] == "reviewer")
    assert "+49 69 5550 0142" in reviewer["body"]  # call-back number on file


def test_reviewer_can_reject_and_it_is_recorded():
    r = run("bankchange")
    before = [v for v in db.vendors() if v["id"] == "V-1001"][0]["bank_account"]
    done = pipeline.apply_review(r["id"], "reject", "Asha", "Called number on file; vendor did not request a change.")
    assert done["status"] == "rejected" and done["review"]["reviewer"] == "Asha"
    assert [v for v in db.vendors() if v["id"] == "V-1001"][0]["bank_account"] == before
    with pytest.raises(ValueError):
        pipeline.apply_review(r["id"], "approve", "Asha", "second attempt")


def test_blocklisted_tax_id_is_rejected_despite_new_name():
    r = run("blocklist")
    assert r["status"] == "rejected" and "BLOCKLIST_TAX_ID" in codes(r)
    assert "blocklist" not in vendor_msg(r)["body"].lower()
    assert not any(v["legal_name"].startswith("Orion") for v in db.vendors())


def test_missing_fields_and_documents():
    r = run("happy", contact_email="", bank_code="")
    assert (r["status"], r["lane"]) == ("pending", "vendor")
    assert codes(r) == {"MISSING_FIELD"} and len(r["decision"]["decisive"]) == 2


def test_tax_id_from_wrong_country_is_named():
    r = run("bankchange", tax_id="27AAECN4521P1Z4")
    assert "TAX_ID_WRONG_COUNTRY" in codes(r)


def test_personal_bank_account_goes_to_review():
    r = run("happy", bank_account_holder="Rakesh Sharma")
    assert r["lane"] == "review" and "BANK_HOLDER_MISMATCH" in codes(r)


def test_checksums():
    assert rules.check_gstin("27AAECN4521P1Z4")[0] and not rules.check_gstin("27AAECN4521P1Z5")[0]
    assert rules.check_iban("DE12 5001 0517 0648 4898 90")[0] and not rules.check_iban("DE13500105170648489890")[0]
    assert rules.check_gb_vat("GB927364113")[0] and rules.check_de_vat("DE294776378")[0]
    assert rules.check_aba("125000024")[0] and not rules.check_aba("125000025")[0]


def test_name_matching():
    assert rules.name_similarity("Acme Tools Pvt Ltd", "ACME TOOLS PRIVATE LIMITED")[0] == 1.0
    assert rules.name_verdict(rules.name_similarity("Brightline Logistics LLC", "Marcus T. Feldman")[0]) == "mismatch"


# ---- the Gemini path, exercised with a stand-in for the HTTP call ----------
class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


def gemini_reply(obj):
    import json
    return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "```json\n" + json.dumps(obj) + "\n```"}]}}]})


def test_gemini_reader_is_used_when_key_is_set(monkeypatch):
    from app import extract
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    extract._working_model["name"] = None
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(url)
        if "gemini-3.6-flash" in url:
            return FakeResponse(404)  # first model name not available: must try the next
        if "inline_data" not in json["contents"][0]["parts"][0]:
            return FakeResponse(500)  # message polishing fails: must fall back to template
        return gemini_reply({"doc_type": "invoice", "confidence": 0.97, "legal_name": "Lumen Analytics Pvt Ltd",
                             "tax_id": "33AACCL6620M1ZH", "summary": "A tax invoice issued by Lumen Analytics."})

    monkeypatch.setattr(extract.httpx, "post", fake_post)
    r = run("wrongdoc", bank_doc="lumen_invoice_sent_by_mistake.pdf")
    read = next(s for s in r["stages"] if s["key"] == "read")
    assert "Gemini (gemini-3.8-flash)" in read["summary"]
    assert [f["code"] for f in r["decision"]["decisive"]].count("WRONG_DOC") == 2
    assert vendor_msg(r)["written_by"] == "template"
    extract._working_model["name"] = None


def test_gemini_outage_falls_back_to_local_parser(monkeypatch):
    from app import extract
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    extract._working_model["name"] = None
    monkeypatch.setattr(extract.httpx, "post", lambda *a, **k: FakeResponse(429))
    r = run("happy")
    assert (r["status"], r["lane"]) == ("approved", "auto")
    read = next(s for s in r["stages"] if s["key"] == "read")
    assert "local text parser" in read["summary"]
    assert "HTTP 429" in read["extra"]["documents"][0]["note"]


def test_ai_rewording_is_rejected_if_it_changes_an_identifier(monkeypatch):
    from app import extract, messages
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    asks = ["The PAN you entered (BKRPS4471Q) is not the PAN on your registration (AAKFK9034L)."]
    monkeypatch.setattr(extract, "gemini_call", lambda parts, **k: ('{"items": ["Please send the right PAN, AAKFK9034X."]}', "m"))
    assert messages._polish(asks, "Kaveri")[1] == "template"
    monkeypatch.setattr(extract, "gemini_call", lambda parts, **k: ('{"items": ["You gave PAN BKRPS4471Q, but your registration uses AAKFK9034L. Please send that one."]}', "m"))
    assert messages._polish(asks, "Kaveri")[1].startswith("Gemini")


def test_thinking_is_minimal_and_dropped_if_a_model_rejects_it(monkeypatch):
    from app import extract
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    extract._working_model["name"] = None
    extract._no_thinking_cfg.clear()
    seen = []

    def fake_post(url, json=None, timeout=None, headers=None):
        cfg = json["generationConfig"].get("thinkingConfig")
        seen.append(cfg)
        if cfg is not None:
            return FakeResponse(400)  # pretend this model does not accept the setting
        return gemini_reply({"doc_type": "bank_proof", "confidence": 0.9, "summary": "x"})

    monkeypatch.setattr(extract.httpx, "post", fake_post)
    text, model = extract.gemini_call([{"text": "hi"}])
    assert seen[0] == {"thinkingLevel": "minimal"} and seen[1] is None
    assert model == "gemini-3.6-flash"
    extract._working_model["name"] = None
    extract._no_thinking_cfg.clear()


def test_both_documents_are_read_at_the_same_time(monkeypatch):
    import threading
    import time as _t
    from app import extract
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    extract._working_model["name"] = None
    live, peak, lock = [0], [0], threading.Lock()

    def slow_read(path):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        _t.sleep(0.3)
        with lock:
            live[0] -= 1
        return extract.read_locally(path)

    monkeypatch.setattr(extract, "read_document", slow_read)
    r = run("happy")
    assert peak[0] == 2
    assert (r["status"], r["lane"]) == ("approved", "auto")
