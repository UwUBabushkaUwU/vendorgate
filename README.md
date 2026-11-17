# VendorGate

ASA Case Study, PS-2: vendor onboarding, from submission to approval.

A vendor submits company details, bank details and two documents. VendorGate
checks that they are complete, valid and consistent, then returns one of three
statuses (approved, pending, rejected) with every reason visible, and tells the
vendor exactly what is needed.

## Run it locally (2 minutes)

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=your-key        # free key: https://aistudio.google.com/app/apikey
uvicorn app.main:app --port 8000
```

Open http://localhost:8000. Without a key the app still runs, using the local
document parser; the sidebar shows which reader is active.

Run the tests: `python -m pytest -q` (16 tests, every demo scenario end to end).

## Put it on a public link (Render, free)

1. Push this folder to a new GitHub repository.
2. On render.com: New > Blueprint, pick the repository. `render.yaml` does the rest.
3. When asked, paste your `GEMINI_API_KEY`.
4. Open the URL Render gives you. That is your "live and runnable" link.

Two things to know about the free plan:

- It sleeps after 15 minutes without traffic and takes about a minute to wake.
  Open the link a few minutes before anyone watches.
- Its disk is wiped when it sleeps, so run history and newly approved vendors
  disappear. Before a demo, run the five rehearsed cases once (about a minute)
  so the dashboard has history. Render's paid Starter plan removes both limits.

`render.yaml` uses the Singapore region. Keep it outside the EU/UK: Gemini's
free tier is reported as unavailable there.

## The process

```
 submission (form + tax certificate + bank proof)
        |
 1 Intake            anything missing?                      -> vendor fix
 2 Read documents    AI reads each file: what is it, what does it say?
                     wrong document type                    -> vendor fix
 3 Format checks     tax ID and bank details valid for the country? (checksums)
 4 Cross-reference   form vs documents vs each other
                     typo / wrong PAN / wrong state         -> vendor fix
                     bank account not in vendor's name      -> human review
 5 Risk screening    blocklisted tax ID, exact duplicate    -> reject
                     existing vendor + new bank account     -> human review
 6 Decision          the most serious finding wins
 7 Communication     vendor message + reviewer note + vendor record
```

| Outcome | When | What happens |
|---|---|---|
| Approved | every check passes | vendor record created, vendor told |
| Pending, waiting on vendor | missing, mistyped or wrong document | vendor gets a numbered list and a link to a pre-filled correction form |
| Pending, needs review | a risk signal that needs judgement | goes to the review queue; vendor gets a neutral holding message |
| Rejected | blocklisted tax ID, or exact duplicate of an active vendor | no record created; vendor gets a neutral message |

## Design decisions

1. **The AI reads, the rules decide.** Gemini does two jobs: read the documents
   (type and printed values) and reword requests to the vendor. Every pass or
   fail is deterministic code in `app/rules.py`, so the same input always gives
   the same decision and each one can be explained line by line.
2. **Three exits, not two.** "Not approved" splits into what the vendor can fix,
   what a person must judge, and what policy forbids. Mixing them is how real
   queues clog: reviewers end up chasing typos.
3. **Risk signals are never described to the vendor.** Telling a fraudster which
   check caught them teaches them to pass it. Those messages use fixed wording
   and the AI is not involved.
4. **A wrong document is not trusted, even if its numbers match.** In edge case 2
   the invoice shows the right GSTIN. A naive extractor would pass it.
5. **The AI's rewording is verified.** If the reworded request drops or alters
   any number or ID, it is discarded and the template wording is used.
6. **Nothing hangs.** If Gemini is slow, rate limited or down, the local parser
   takes over and the run records that. If a stage crashes, the run goes to
   human review instead of failing silently.
7. **Unknown is routed to a person, not guessed.** Unsupported country,
   unreadable scan with no AI available, name similarity of 70 to 89 percent.

## Edge cases

| Case | What is submitted | Outcome |
|---|---|---|
| 1. Valid on the surface | Every field passes its own format check. But the PAN is not the one inside the GSTIN (it is an individual's), and the address is in Maharashtra while the GSTIN is registered in Karnataka. | Pending, vendor: two precise requests |
| 2. Wrong document, then fixed | An invoice in the tax-certificate slot. Vendor opens the correction link, attaches the certificate. | Pending, vendor. Then approved, linked to the first run |
| 3. Existing vendor, new bank account | Documents are consistent. But the tax ID belongs to active vendor V-1001, the bank account is new, and it is in another country. | Pending, review. Reviewer is told to call the number already on file |
| 4. Blocked entity, new name | Name changed by one word; tax ID identical to an entity on the internal blocklist. | Rejected |

Also handled, without a rehearsed sample: missing fields and documents, a tax ID
in another country's format, a bank account held in a personal name, a bank
account shared with a different vendor, and submitting an approved vendor twice
(caught as a duplicate).

## Assumptions

- The vendor submits through a web form with two attachments. Email intake would
  feed the same pipeline.
- Automated rules cover India, the US, the UK and Germany. Other countries go to a
  person.
- The vendor master and blocklist are seeded sample data standing in for the
  buyer's ERP and a sanctions-screening service.
- Format and checksum validation proves an ID is well formed, not that it is
  registered. Registry lookups are the first thing to add (see limits).
- Vendor emails are saved to an outbox unless SMTP settings are provided.
- Sample companies, banks and documents are fictional and watermarked SPECIMEN.

## Known limits and what comes next

- No live registry checks: GSTN, VIES, Companies House, IRS TIN matching.
- No bank account verification (penny-drop / confirmation of payee).
- The local fallback parser only understands text PDFs with labelled fields.
  Scans and photos need Gemini.
- No login or roles; anyone with the link can act as reviewer.
- SQLite on one machine. Production needs a managed database and file storage.
- No reminders when a vendor stays silent after a request.
- Each stage stays on screen for at least half a second so a viewer can follow
  it. The processing time shown is the real compute time.

## Where things are

| File | What it does |
|---|---|
| `app/pipeline.py` | the seven stages and the decision rule. Read this first |
| `app/rules.py` | validators: GSTIN, PAN, EIN, VAT, IBAN, routing, name matching |
| `app/extract.py` | Gemini document reader and the local fallback |
| `app/messages.py` | vendor and reviewer messages |
| `app/scenarios.py` | the five rehearsed inputs |
| `app/db.py` | SQLite storage and seed data |
| `app/main.py` | web API |
| `app/static/index.html` | the whole interface |
| `scripts/make_samples.py` | regenerates the sample PDFs |
| `tests/test_scenarios.py` | end-to-end tests |
