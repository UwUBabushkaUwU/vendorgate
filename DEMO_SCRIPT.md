# Demo script and interview prep

## Before you record or present

1. Open the link 5 minutes early (the free host needs about a minute to wake).
2. Check the sidebar says **Document reader: Gemini**. If it says "Local parser",
   the key is missing.
3. Dashboard > **Reset demo data** (click twice). This clears runs and restores
   the vendor master, so every case behaves as rehearsed.
4. For the live interview only: after resetting, the dashboard is empty. That is
   fine; it fills as you run cases.

One behaviour to remember: running the happy path **twice** without a reset
gives "Rejected: already an active vendor" the second time. That is correct
(duplicate protection), and a nice thing to show on purpose. Do not let it
surprise you.

## 5-minute video

Say it in your own words. The timings matter more than the wording.

**0:00 to 0:30. Frame it.**
"I picked vendor onboarding. Today someone in procurement reads every form,
opens every attachment and chases vendors by email. A bad approval can mean
paying a fraudster. This process takes a submission and returns approved,
pending or rejected, shows every reason, and writes back to the vendor."

**0:30 to 1:40. Happy path.** Click *Happy path*, then *Run onboarding check*.
- Stage 2: "Gemini reads both documents and says what each one actually is."
- Stage 3: "Each ID is checked for the country. The GSTIN has a check character,
  so a typo is caught here."
- Stage 4, open the passed checks: "The form says Pvt Ltd, the bank says PRIVATE
  LIMITED. A naive comparison fails that. Mine normalises legal suffixes."
- Decision: "Approved with no human touch. Vendor record created, vendor told."
- One line on design: "The AI reads. Written rules decide. So the same input
  always gets the same answer and I can explain every one."

**1:40 to 3:00. Edge case 2, wrong document.** Load it, run it.
- "The vendor attached an invoice where the tax certificate goes. Notice the
  invoice shows the correct GSTIN. A process that only extracts numbers would
  approve this. Mine checks what the document is first."
- Show the vendor email: "One specific request, and a link."
- Click *Open the vendor's resubmission form*, *Attach the corrected sample
  document*, run. "Everything re-runs from scratch. Approved, and linked to the
  first attempt."

**3:00 to 4:15. Edge case 3, bank change.** Load it, run it.
- "Every document here is consistent. Nothing is wrong with the paperwork. But
  this tax ID already belongs to an active vendor, the bank account is new, and
  it is in a different country. That is the standard shape of payment-diversion
  fraud."
- "So it does not approve and does not reject. It goes to a person."
- *To the reviewer* tab: "The reviewer gets the evidence and a next step: call
  the number already on file, not the one in this submission."
- *To the vendor* tab: "The vendor is told nothing about why. If this is a
  fraudster, I am not going to teach them which check to beat."
- Type a note, click *Reject*.

**4:15 to 4:50. Dashboard.** "Every run, its outcome, and why. The review queue
is at the top. Share of submissions approved with no human touch is the number a
buyer cares about."

**4:50 to 5:00. Close.** "Next I would add live registry lookups and bank account
verification. Thanks."

## Live interview: running order

1. Reset. Happy path.
2. Edge case 1 (valid on the surface). The line: "Stage 3 is all green. Every
   field is valid alone. Stage 4 is where they contradict each other."
3. Edge case 2, including the resubmission.
4. Edge case 3, including the reviewer decision.
5. Edge case 4. The line: "Name-only hits go to a person because they are often
   false positives. An exact tax ID match on our own blocklist is a hard stop."
6. Dashboard, then *Rules & reference data*.
7. If there is time: run the happy path again and show the duplicate catch.

If they ask you to try something unrehearsed, good options that work:
change one character of the GSTIN (checksum fails), blank the IFSC (missing
field), change the account holder to a person's name (goes to review), or
upload any PDF of their own in a document slot.

## Questions you should expect

**Why not let the model make the decision?**
An approval that creates a payable vendor has to be repeatable and auditable.
A model can give different answers to the same input and cannot show its rule.
I use it where it is strong, reading messy documents, and keep the decision in
code a finance controller could read.

**What happens when the model is wrong or unavailable?**
Wrong: values it reads are cross-checked against the form, so a misread shows up
as a mismatch, not a silent approval. Its rewording of vendor messages is
rejected if any number or ID changes. Unavailable: a local parser takes over for
text PDFs, the run records which reader was used, and an unreadable scan goes to
a person.

**Why three outcomes for "not approved"?**
Because the next action is different. A typo needs the vendor. A risk signal
needs a person. A blocklist hit needs nobody. Sending all three to one queue is
what makes manual review slow.

**Why not auto-reject the bank change?**
Vendors really do change banks. Rejecting would be wrong often enough to hurt.
Approving would be wrong rarely but expensively. So a person decides, with a
specific action that takes two minutes.

**How did you pick the 90 percent name threshold?**
It is a starting point, not a tuned number. I would calibrate it on the buyer's
past onboarding decisions. The important design choice is the middle band:
70 to 89 percent goes to a person instead of being forced into yes or no.

**How would this go to production?**
Connect the real vendor master and a sanctions service in place of the seed
data. Add registry lookups and bank verification. Add login and reviewer roles.
Move to a managed database. Take submissions from email as well as the form.
Add reminders for vendors who do not respond.

**What does it not do?**
Say this before they find it: it validates format and consistency, not that an
ID is registered with the government. It does not verify the bank account
exists. The fallback parser cannot read scans.

**How would you measure it?**
Share approved with no human touch, time to decision, how many pending cases are
resolved on the first resubmission, and how often reviewers overturn a "needs
review" flag (that tells you a rule is too strict).

**Which tools did you use and why?**
Python and FastAPI because the logic is the product and I wanted it readable.
SQLite because one file is enough for a demo and gives a real audit trail.
Gemini for document reading. An AI coding assistant to build it faster. Be ready
to open `app/pipeline.py` and walk through one stage; read it end to end before
the interview so every answer above is yours.

## Day 1 email

Subject: `ASA Case Study — [Your Name] — PS-2`
