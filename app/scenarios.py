"""Rehearsed demo inputs. Every company here is fictional; tax IDs and IBANs
carry valid checksums so the format checks are doing real work."""

SCENARIOS = [
    {
        "key": "happy", "tag": "Happy path", "title": "Clean submission, India",
        "teaser": "Everything agrees. Bank account is in 'PRIVATE LIMITED', form says 'Pvt Ltd'.",
        "expect": "Approved automatically",
        "form": {
            "legal_name": "Northwind Precision Components Pvt Ltd", "trading_name": "", "country": "IN",
            "tax_id": "27AAECN4521P1Z4", "pan": "AAECN4521P",
            "address": "Plot 14, MIDC Chakan Phase II, Pune 410501", "state_region": "Maharashtra",
            "contact_name": "Priya Deshmukh", "contact_email": "priya.deshmukh@northwindprecision.example",
            "contact_phone": "+91 20 5550 0164",
            "bank_account_holder": "NORTHWIND PRECISION COMPONENTS PRIVATE LIMITED",
            "bank_account": "50200048817345", "bank_code": "SNBK0004521",
        },
        "tax_doc": "northwind_gst_certificate.pdf", "bank_doc": "northwind_bank_letter.pdf",
    },
    {
        "key": "subtle", "tag": "Edge case 1", "title": "Valid on the surface, inconsistent underneath",
        "teaser": "Every field passes its own format check. Together they contradict each other.",
        "expect": "Pending, waiting on vendor (2 items)",
        "form": {
            "legal_name": "Kaveri Agro Exports LLP", "trading_name": "", "country": "IN",
            "tax_id": "29AAKFK9034L1ZP", "pan": "BKRPS4471Q",
            "address": "Gat No. 212, Pimpalgaon Baswant, Nashik 422209", "state_region": "Maharashtra",
            "contact_name": "Sandeep Rao", "contact_email": "sandeep@kaveriagro.example",
            "contact_phone": "+91 253 555 0128",
            "bank_account_holder": "Kaveri Agro Exports LLP",
            "bank_account": "917020065512098", "bank_code": "DCNB0000876",
        },
        "tax_doc": "kaveri_gst_certificate.pdf", "bank_doc": "kaveri_cancelled_cheque.pdf",
    },
    {
        "key": "wrongdoc", "tag": "Edge case 2", "title": "Wrong document, then the vendor fixes it",
        "teaser": "An invoice is attached where the tax certificate should be. It even shows the right GSTIN.",
        "expect": "Pending, waiting on vendor. Then resubmit: approved",
        "form": {
            "legal_name": "Lumen Analytics Pvt Ltd", "trading_name": "", "country": "IN",
            "tax_id": "33AACCL6620M1ZH", "pan": "AACCL6620M",
            "address": "4th Floor, Tidel Park, Taramani, Chennai 600113", "state_region": "Tamil Nadu",
            "contact_name": "Meera Krishnan", "contact_email": "meera.k@lumenanalytics.example",
            "contact_phone": "+91 44 5550 0191",
            "bank_account_holder": "Lumen Analytics Private Limited",
            "bank_account": "38140200005567", "bank_code": "CRMB0002210",
        },
        "tax_doc": "lumen_invoice_sent_by_mistake.pdf", "bank_doc": "lumen_bank_letter.pdf",
        "fix": {"tax_doc": "lumen_gst_certificate.pdf"},
    },
    {
        "key": "bankchange", "tag": "Edge case 3", "title": "Existing vendor, new bank account",
        "teaser": "Documents look perfect. But this vendor already exists, and the bank account has moved abroad.",
        "expect": "Pending, needs human review. Vendor is told nothing about why",
        "form": {
            "legal_name": "Helix Packaging GmbH", "trading_name": "", "country": "DE",
            "tax_id": "DE294776378", "pan": "",
            "address": "Hanauer Landstrasse 291, 60314 Frankfurt am Main", "state_region": "Hessen",
            "contact_name": "Jonas Weber", "contact_email": "helix.packaging.accounts@gmail.com",
            "contact_phone": "+370 5 555 0177",
            "bank_account_holder": "Helix Packaging GmbH",
            "bank_account": "LT267044012345678901", "bank_code": "",
        },
        "tax_doc": "helix_vat_certificate.pdf", "bank_doc": "helix_bank_confirmation_new.pdf",
    },
    {
        "key": "blocklist", "tag": "Edge case 4", "title": "Blocked entity re-applying under a new name",
        "teaser": "Name changed by one word. Tax ID is the same as a vendor offboarded for fraud.",
        "expect": "Rejected. Vendor gets a neutral message",
        "form": {
            "legal_name": "Orion Trade Solutions Ltd", "trading_name": "", "country": "GB",
            "tax_id": "GB927364113", "pan": "",
            "address": "Unit 7, Castlefield Business Park, Manchester M15 4PR", "state_region": "Greater Manchester",
            "contact_name": "Daniel Hughes", "contact_email": "d.hughes@oriontradesolutions.example",
            "contact_phone": "+44 161 555 0149",
            "bank_account_holder": "Orion Trade Solutions Ltd",
            "bank_account": "GB29NWBK60161331926819", "bank_code": "",
        },
        "tax_doc": "orion_vat_certificate.pdf", "bank_doc": "orion_bank_letter.pdf",
    },
]

BY_KEY = {s["key"]: s for s in SCENARIOS}
