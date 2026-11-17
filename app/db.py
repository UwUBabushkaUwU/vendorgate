"""SQLite storage: runs (with their stage-by-stage trail), vendor master,
blocklist, outbox and reviewer actions. One file, no server to manage."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

from .rules import norm_id

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
os.makedirs(os.path.join(DATA_DIR, "uploads"), exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "vendorgate.db")

_lock = threading.RLock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, seq INTEGER, created_at TEXT, finished_at TEXT,
  state TEXT, status TEXT, lane TEXT, vendor_name TEXT, country TEXT,
  scenario TEXT, parent_id TEXT, duration_ms INTEGER,
  submission TEXT, stages TEXT, decision TEXT, review TEXT);
CREATE TABLE IF NOT EXISTS vendors (
  id TEXT PRIMARY KEY, legal_name TEXT, country TEXT, tax_id TEXT,
  bank_account TEXT, phone_on_file TEXT, source TEXT, added_at TEXT);
CREATE TABLE IF NOT EXISTS blocklist (
  id INTEGER PRIMARY KEY AUTOINCREMENT, legal_name TEXT, tax_id TEXT,
  kind TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, created_at TEXT,
  audience TEXT, to_addr TEXT, subject TEXT, body TEXT, delivery TEXT, written_by TEXT);
"""

# Fictional reference data standing in for the buyer's ERP vendor master
# and compliance lists. Tax IDs and IBANs carry valid checksums.
SEED_VENDORS = [
    ("V-1001", "Helix Packaging GmbH", "DE", "DE294776378", "DE12500105170648489890", "+49 69 5550 0142"),
    ("V-1002", "Sterling Office Supplies Ltd", "GB", "GB365684514", "GB33BUKB20201555555555", "+44 20 7946 0155"),
    ("V-1003", "Aarav Textiles Pvt Ltd", "IN", "24AAFCA7812K1ZO", "50200011223344", "+91 79 5550 0117"),
    ("V-1004", "Cascade Freight Inc", "US", "841234567", "000123456789", "+1 206 555 0133"),
]
SEED_BLOCKLIST = [
    ("Orion Trading Solutions Ltd", "GB927364113", "internal_blocklist",
     "Offboarded March 2025 after an invoice-fraud investigation. Do not re-onboard."),
    ("Meridian Global Commodities FZE", "", "watchlist",
     "Sample entry standing in for an external sanctions / watchlist screening service."),
]


def init():
    with _lock:
        _conn.executescript(SCHEMA)
        if _conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] == 0:
            seed()
        _conn.commit()


def seed():
    for v in SEED_VENDORS:
        _conn.execute("INSERT OR REPLACE INTO vendors VALUES (?,?,?,?,?,?,?,?)",
                      (*v, "seed (existing ERP vendor)", now()))
    for b in SEED_BLOCKLIST:
        _conn.execute("INSERT INTO blocklist (legal_name,tax_id,kind,reason) VALUES (?,?,?,?)", b)


def reset():
    with _lock:
        for t in ("runs", "vendors", "blocklist", "outbox"):
            _conn.execute(f"DELETE FROM {t}")
        seed()
        _conn.commit()


def _row(r):
    if r is None:
        return None
    d = dict(r)
    for k in ("submission", "stages", "decision", "review"):
        if k in d and d[k]:
            d[k] = json.loads(d[k])
    return d


def create_run(submission, scenario=None, parent_id=None):
    with _lock:
        seq = (_conn.execute("SELECT COALESCE(MAX(seq),0) FROM runs").fetchone()[0]) + 1
        rid = f"R-{seq:04d}"
        _conn.execute(
            "INSERT INTO runs (id,seq,created_at,state,vendor_name,country,scenario,parent_id,submission,stages) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, seq, now(), "running", submission.get("legal_name") or "(no name given)",
             submission.get("country") or "", scenario, parent_id, json.dumps(submission), "[]"))
        _conn.commit()
        return rid


def update_run(rid, **fields):
    with _lock:
        sets, vals = [], []
        for k, v in fields.items():
            sets.append(f"{k}=?")
            vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
        _conn.execute(f"UPDATE runs SET {','.join(sets)} WHERE id=?", (*vals, rid))
        _conn.commit()


def get_run(rid):
    with _lock:
        return _row(_conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())


def list_runs():
    with _lock:
        rows = _conn.execute(
            "SELECT id,created_at,finished_at,state,status,lane,vendor_name,country,scenario,parent_id,duration_ms,decision,review FROM runs ORDER BY seq DESC").fetchall()
    out = []
    for r in rows:
        d = _row(r)
        dec = d.pop("decision") or {}
        d["headline"] = dec.get("headline")
        d["open_items"] = len(dec.get("decisive", []))
        d["reviewed"] = bool(d.pop("review"))
        out.append(d)
    return out


def vendors():
    with _lock:
        return [dict(r) for r in _conn.execute("SELECT * FROM vendors ORDER BY id").fetchall()]


def blocklist():
    with _lock:
        return [dict(r) for r in _conn.execute("SELECT * FROM blocklist ORDER BY id").fetchall()]


def add_vendor(legal_name, country, tax_id, bank_account, phone, source):
    with _lock:
        n = _conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
        vid = f"V-{1001 + n}"
        while _conn.execute("SELECT 1 FROM vendors WHERE id=?", (vid,)).fetchone():
            n += 1
            vid = f"V-{1001 + n}"
        _conn.execute("INSERT INTO vendors VALUES (?,?,?,?,?,?,?,?)",
                      (vid, legal_name, country, norm_id(tax_id), norm_id(bank_account), phone, source, now()))
        _conn.commit()
        return vid


def update_vendor_bank(vid, bank_account, source):
    with _lock:
        _conn.execute("UPDATE vendors SET bank_account=?, source=? WHERE id=?", (norm_id(bank_account), source, vid))
        _conn.commit()


def add_outbox(run_id, audience, to_addr, subject, body, delivery, written_by):
    with _lock:
        _conn.execute(
            "INSERT INTO outbox (run_id,created_at,audience,to_addr,subject,body,delivery,written_by) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, now(), audience, to_addr, subject, body, delivery, written_by))
        _conn.commit()


def outbox_for(run_id):
    with _lock:
        return [dict(r) for r in _conn.execute("SELECT * FROM outbox WHERE run_id=? ORDER BY id", (run_id,)).fetchall()]
