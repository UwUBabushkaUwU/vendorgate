"""Web layer: serves the UI and a small JSON API. All the thinking is in
pipeline.py; this file only receives submissions and reports state."""
import os
import statistics
import threading
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import db, extract, pipeline, rules
from .scenarios import BY_KEY, SCENARIOS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "samples")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
UPLOADS = os.path.join(db.DATA_DIR, "uploads")
MAX_BYTES = 10 * 1024 * 1024
ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
FORM_FIELDS = ["legal_name", "trading_name", "country", "tax_id", "pan", "address", "state_region",
               "contact_name", "contact_email", "contact_phone", "bank_account_holder",
               "bank_account", "bank_code"]

app = FastAPI(title="VendorGate")
db.init()


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
        return HTMLResponse(fh.read(), headers={"Cache-Control": "no-store"})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/meta")
def meta():
    return {
        "countries": rules.SUPPORTED_COUNTRIES,
        "tax_label": rules.TAX_ID_LABEL,
        "uses_iban": sorted(rules.USES_IBAN),
        "scenarios": SCENARIOS,
        "stages": [{"key": k, "title": t, "question": q} for k, t, q in pipeline.STAGES],
        "ai": {"configured": bool(extract.gemini_key()), "model": extract._working_model["name"] or extract.gemini_models()[0]},
        "email": bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS")),
    }


def _sample_path(name):
    path = os.path.join(SAMPLES, os.path.basename(name or ""))
    return path if name and os.path.isfile(path) else None


async def _save_upload(upload):
    ext = os.path.splitext(upload.filename or "")[1].lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"'{upload.filename}': only PDF, PNG or JPG files are accepted.")
    data = await upload.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(400, f"'{upload.filename}' is larger than 10 MB.")
    if not data:
        return None
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in os.path.basename(upload.filename))
    path = os.path.join(UPLOADS, f"{uuid.uuid4().hex[:10]}__{safe}")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


@app.post("/api/runs")
async def create_run(request: Request):
    form = await request.form()
    sub = {k: (form.get(k) or "").strip() for k in FORM_FIELDS}
    parent_id = (form.get("parent_id") or "").strip() or None
    parent = db.get_run(parent_id) if parent_id else None
    if parent_id and not parent:
        raise HTTPException(400, "The run being resubmitted was not found.")
    for slot in ("tax_doc", "bank_doc"):
        path = None
        upload = form.get(slot)
        if upload is not None and hasattr(upload, "filename") and upload.filename:
            path = await _save_upload(upload)
        if not path:
            path = _sample_path(form.get(f"sample_{slot}"))
        if not path and parent and form.get(f"keep_{slot}"):
            path = parent["submission"].get(slot)  # unchanged document carried over
        sub[slot] = path
    sub["_base_url"] = str(request.base_url).rstrip("/")
    scenario = (form.get("scenario") or "").strip() or None
    tag = BY_KEY[scenario]["tag"] if scenario in BY_KEY else None
    if parent and parent.get("scenario"):
        tag = parent["scenario"].split(" · ")[0] + " · resubmission"
    rid = db.create_run(sub, scenario=tag, parent_id=parent_id)
    threading.Thread(target=pipeline.execute, args=(rid,), daemon=True).start()
    return {"id": rid}


def _public(run):
    if not run:
        return None
    sub = dict(run["submission"])
    sub.pop("_base_url", None)
    for slot in ("tax_doc", "bank_doc"):
        sub[slot + "_name"] = os.path.basename(sub[slot]).split("__", 1)[-1] if sub.get(slot) else None
        sub.pop(slot, None)
    run = dict(run)
    run["submission"] = sub
    run["outbox"] = db.outbox_for(run["id"])
    return run


@app.get("/api/runs")
def list_runs():
    runs = db.list_runs()
    done = [r for r in runs if r["state"] == "done"]
    auto = [r for r in done if r["status"] == "approved" and r["lane"] == "auto"]
    times = [r["duration_ms"] for r in done if r["duration_ms"] is not None]
    return {"runs": runs, "kpi": {
        "total": len(runs),
        "approved": len([r for r in done if r["status"] == "approved"]),
        "pending_vendor": len([r for r in done if r["status"] == "pending" and r["lane"] == "vendor"]),
        "pending_review": len([r for r in done if r["status"] == "pending" and r["lane"] == "review"]),
        "rejected": len([r for r in done if r["status"] == "rejected"]),
        "auto_rate": round(100 * len(auto) / len(done)) if done else None,
        "median_ms": int(statistics.median(times)) if times else None,
    }}


@app.get("/api/runs/{rid}")
def get_run(rid: str):
    run = db.get_run(rid)
    if not run:
        raise HTTPException(404, "Run not found")
    return _public(run)


@app.get("/api/runs/{rid}/doc/{slot}")
def get_doc(rid: str, slot: str):
    run = db.get_run(rid)
    if not run or slot not in ("tax_doc", "bank_doc") or not run["submission"].get(slot):
        raise HTTPException(404, "Document not found")
    path = run["submission"][slot]
    return FileResponse(path, filename=os.path.basename(path).split("__", 1)[-1], content_disposition_type="inline")


@app.post("/api/runs/{rid}/review")
async def review(rid: str, request: Request):
    body = await request.json()
    action = body.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")
    if not (body.get("note") or "").strip():
        raise HTTPException(400, "A note is required so the decision can be audited.")
    try:
        return _public(pipeline.apply_review(rid, action, (body.get("reviewer") or "").strip(), body["note"].strip()))
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/reference")
def reference():
    return {"vendors": db.vendors(), "blocklist": db.blocklist(),
            "policy": {"name_match": rules.NAME_MATCH, "name_near": rules.NAME_NEAR}}


@app.post("/api/reset")
def reset():
    db.reset()
    return {"ok": True}


@app.get("/samples/{name}")
def sample(name: str):
    path = _sample_path(name)
    if not path:
        raise HTTPException(404, "Sample not found")
    return FileResponse(path, filename=name, content_disposition_type="inline")


@app.exception_handler(HTTPException)
async def http_error(_, exc):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
