"""Generate the sample vendor documents used in the demo.
Run:  python scripts/make_samples.py
All companies, banks and numbers are fictional test data, and every page is
marked SPECIMEN so nobody can mistake these for real records."""
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")
os.makedirs(OUT, exist_ok=True)
W, H = A4


def page(filename, heading, sub, rows, accent, body=None, table=None):
    c = canvas.Canvas(os.path.join(OUT, filename), pagesize=A4)
    c.setTitle(heading)
    # watermark
    c.saveState()
    c.setFillColor(colors.Color(0.85, 0.85, 0.85, alpha=0.45))
    c.setFont("Helvetica-Bold", 64)
    c.translate(W / 2, H / 2)
    c.rotate(35)
    c.drawCentredString(0, 0, "SPECIMEN")
    c.restoreState()
    # header band
    c.setFillColor(accent)
    c.rect(0, H - 92, W, 92, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(48, H - 48, heading)
    c.setFont("Helvetica", 10)
    c.drawString(48, H - 68, sub)
    y = H - 132
    c.setFillColor(colors.black)
    for label, value in rows:
        c.setFont("Helvetica", 10.5)
        # one string per line so text extraction keeps label and value together
        c.drawString(48, y, f"{label}: {value}")
        y -= 21
    if table:
        y -= 8
        c.setFont("Helvetica-Bold", 10)
        for x, t in zip((48, 300, 370, 460), table[0]):
            c.drawString(x, y, t)
        c.line(48, y - 5, W - 48, y - 5)
        c.setFont("Helvetica", 10)
        for r in table[1:]:
            y -= 20
            for x, t in zip((48, 300, 370, 460), r):
                c.drawString(x, y, t)
        y -= 14
    if body:
        y -= 10
        c.setFont("Helvetica", 10)
        for line in body:
            c.drawString(48, y, line)
            y -= 15
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(colors.grey)
    c.drawString(48, 40, "SPECIMEN - fictional test data created for a software demonstration. Not a real document.")
    c.save()


GREEN, NAVY, MAROON, SLATE = colors.HexColor("#1f5f4a"), colors.HexColor("#1d3557"), colors.HexColor("#7a1f3d"), colors.HexColor("#3d4451")


def gst(filename, name, gstin, pan, constitution, address, state, issued):
    page(filename, "Certificate of Registration (Specimen)", "Goods and Services Tax - Form GST REG-06 layout", [
        ("GSTIN", gstin), ("Legal Name", name), ("Trade Name", name), ("PAN", pan),
        ("Constitution of Business", constitution),
        ("Address of Principal Place of Business", address), ("State", state),
        ("Date of Liability", "2021-07-01"), ("Date of Issue", issued), ("Type of Registration", "Regular"),
    ], GREEN, ["This certificate of registration is issued to the taxpayer named above for the",
               "principal place of business stated. It is valid from the date of liability."])


def bank(filename, title, bank_name, holder, account_label, account, code_label, code, issued, branch):
    rows = [("Bank Name", bank_name), ("Branch", branch), ("Account Holder Name", holder),
            (account_label, account)]
    if code:
        rows.append((code_label, code))
    rows += [("Account Type", "Current / Business"), ("Date of Issue", issued)]
    page(filename, title, f"{bank_name} (fictional bank)", rows, NAVY,
         ["We confirm that the above account is held with us in the name shown and is",
          "active as on the date of issue. This letter is issued at the customer's request."])


# --- happy path
gst("northwind_gst_certificate.pdf", "Northwind Precision Components Private Limited", "27AAECN4521P1Z4", "AAECN4521P",
    "Private Limited Company", "Plot 14, MIDC Chakan Phase II, Pune 410501", "Maharashtra", "2024-02-12")
bank("northwind_bank_letter.pdf", "Bank Account Confirmation Letter", "Sahyadri National Bank",
     "NORTHWIND PRECISION COMPONENTS PRIVATE LIMITED", "Account Number", "50200048817345", "IFSC", "SNBK0004521",
     "2026-10-03", "Chakan Industrial Branch, Pune")

# --- edge 1: certificate is genuine-looking and registered in Karnataka
gst("kaveri_gst_certificate.pdf", "Kaveri Agro Exports LLP", "29AAKFK9034L1ZP", "AAKFK9034L",
    "Limited Liability Partnership", "No. 88, 2nd Main, Yeshwanthpur Industrial Suburb, Bengaluru 560022", "Karnataka", "2023-08-21")
bank("kaveri_cancelled_cheque.pdf", "Cancelled Cheque (Specimen Copy)", "Deccan Cooperative National Bank",
     "Kaveri Agro Exports LLP", "Account Number", "917020065512098", "IFSC", "DCNB0000876",
     "2026-09-18", "Yeshwanthpur Branch, Bengaluru")

# --- edge 2: an invoice sent by mistake, then the right certificate
page("lumen_invoice_sent_by_mistake.pdf", "TAX INVOICE", "Lumen Analytics Pvt Ltd", [
    ("Invoice No", "LA/2026-27/0418"), ("Date", "2026-10-22"),
    ("Seller", "Lumen Analytics Pvt Ltd"), ("GSTIN", "33AACCL6620M1ZH"),
    ("Address", "4th Floor, Tidel Park, Taramani, Chennai 600113"), ("State", "Tamil Nadu"),
    ("Bill To", "Harbour Retail Ventures Pvt Ltd, Kochi"),
], SLATE, ["Payment terms: 30 days from invoice date."],
     table=[("Description", "Qty", "Rate (INR)", "Amount (INR)"),
            ("Dashboard build - retail analytics", "1", "4,20,000.00", "4,20,000.00"),
            ("Monthly support retainer - October", "1", "60,000.00", "60,000.00"),
            ("Subtotal", "", "", "4,80,000.00"),
            ("IGST @ 18%", "", "", "86,400.00"),
            ("Amount Due", "", "", "5,66,400.00")])
gst("lumen_gst_certificate.pdf", "Lumen Analytics Private Limited", "33AACCL6620M1ZH", "AACCL6620M",
    "Private Limited Company", "4th Floor, Tidel Park, Taramani, Chennai 600113", "Tamil Nadu", "2022-11-04")
bank("lumen_bank_letter.pdf", "Bank Account Confirmation Letter", "Coromandel Merchant Bank",
     "Lumen Analytics Private Limited", "Account Number", "38140200005567", "IFSC", "CRMB0002210",
     "2026-10-11", "Taramani Branch, Chennai")

# --- edge 3: existing German vendor, new Lithuanian account
page("helix_vat_certificate.pdf", "VAT Registration Confirmation (Specimen)", "Bescheinigung - Umsatzsteuer-Identifikationsnummer", [
    ("Legal Name", "Helix Packaging GmbH"), ("USt-IdNr.", "DE294776378"),
    ("Registered Address", "Hanauer Landstrasse 291, 60314 Frankfurt am Main"), ("State", "Hessen"),
    ("Date of Issue", "2019-05-14"),
], MAROON, ["This confirms the VAT identification number assigned to the business named above."])
bank("helix_bank_confirmation_new.pdf", "Account Confirmation", "Baltic Commerce Bank UAB",
     "Helix Packaging GmbH", "IBAN", "LT26 7044 0123 4567 8901", "", "", "2026-11-09", "Vilnius")

# --- edge 4: blocked entity, lightly renamed
page("orion_vat_certificate.pdf", "VAT Registration Certificate (Specimen)", "Value Added Tax - certificate of registration", [
    ("Legal Name", "Orion Trade Solutions Ltd"), ("VAT Registration Number", "GB927364113"),
    ("Registered Address", "Unit 7, Castlefield Business Park, Manchester M15 4PR"),
    ("Region", "Greater Manchester"), ("Date of Issue", "2026-08-30"),
], MAROON, ["This certificate confirms registration for Value Added Tax for the business named above."])
bank("orion_bank_letter.pdf", "Bank Account Confirmation Letter", "Pennine Clearing Bank",
     "Orion Trade Solutions Ltd", "IBAN", "GB29 NWBK 6016 1331 9268 19", "", "", "2026-10-27", "Manchester Deansgate")

print("wrote", len(os.listdir(OUT)), "files to", OUT)
