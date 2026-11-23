"""Fictional procurement master data: 9 vendors and 15 purchase orders. All names are invented."""
from __future__ import annotations

VENDORS = [
    # id, name, status, country, tax_id, address
    ("V001", "Northwind Industrial Supplies Ltd", "ACTIVE", "US", "US-EIN-84-1100231", "410 Harbor Way, Oakland, CA 94607"),
    ("V002", "Brightline Office Solutions Inc", "ACTIVE", "US", "US-EIN-47-2208841", "88 Market Street, Austin, TX 78701"),
    ("V003", "Apex Logistics Partners LLC", "ACTIVE", "US", "US-EIN-36-9917720", "1200 Freight Blvd, Chicago, IL 60609"),
    ("V004", "Meridian Software Services Pvt Ltd", "ACTIVE", "IN", "29ABCDE1234F1Z5", "5th Floor, Tech Park, Whitefield, Bengaluru 560066"),
    ("V005", "Kestrel Facility Management Pvt Ltd", "ACTIVE", "IN", "09FGHIJ5678K1Z2", "21 Civil Lines, Kanpur, Uttar Pradesh 208001"),
    ("V006", "Helios Packaging GmbH", "ACTIVE", "DE", "DE812345678", "Industriestrasse 14, 70565 Stuttgart"),
    ("V007", "Orion Cloud Hosting Inc", "ACTIVE", "US", "US-EIN-91-3300456", "700 Skyline Drive, Seattle, WA 98101"),
    ("V008", "Zenith Consulting Group LLC", "BLOCKED", "US", "US-EIN-55-1029384", "9 Summit Plaza, Denver, CO 80202"),
    ("V009", "Cobalt Print & Design Co", "INACTIVE", "US", "US-EIN-22-7788990", "33 Elm Court, Portland, OR 97201"),
]

# po_number, vendor_id, currency, tax_rate(%), status, committed_net, description, [(line description, qty, unit_price)]
PURCHASE_ORDERS = [
    ("PO-24001", "V001", "USD", "8", "OPEN", "0", "Safety consumables Q4",
     [("Industrial gloves (carton)", "100", "12.50"), ("Safety goggles", "50", "8.40")]),
    ("PO-24002", "V002", "USD", "7", "OPEN", "0", "Office supplies Q4",
     [("Copy paper A4 (box)", "200", "4.20"), ("Toner cartridge", "10", "62.00")]),
    ("PO-24003", "V003", "USD", "5", "OPEN", "0", "Freight and handling, Chicago-Dallas lane",
     [("Pallet freight Chicago to Dallas", "12", "185.00"), ("Warehouse handling (hour)", "40", "32.50")]),
    ("PO-24004", "V007", "USD", "8", "OPEN", "0", "Managed hosting 2026",
     [("Managed hosting plan (month)", "12", "450.00")]),
    ("PO-24005", "V004", "INR", "18", "PARTIALLY_BILLED", "380000", "Platform implementation",
     [("Implementation services (day)", "200", "2500.00")]),
    ("PO-24006", "V005", "INR", "18", "OPEN", "0", "Facility services FY26-27",
     [("Housekeeping services (month)", "6", "85000.00"), ("Security services (month)", "6", "65000.00")]),
    ("PO-24007", "V006", "EUR", "19", "OPEN", "0", "Packaging materials",
     [("Corrugated boxes 40x30x20", "5000", "0.42"), ("Stretch film roll", "300", "6.80")]),
    ("PO-24008", "V001", "USD", "8", "CLOSED", "1120", "Safety vests (fully billed)",
     [("Hi-vis safety vest", "80", "14.00")]),
    ("PO-24009", "V007", "USD", "8", "OPEN", "0", "Reserved compute capacity",
     [("Reserved compute instance (month)", "6", "980.00")]),
    ("PO-24010", "V003", "USD", "5", "OPEN", "0", "Cross-dock services",
     [("Cross-dock pallet transfer", "30", "21.00"), ("Pallet wrapping service", "100", "3.75")]),
    ("PO-24011", "V002", "USD", "7", "PARTIALLY_BILLED", "1000", "Ergonomic furniture",
     [("Ergonomic office chair", "20", "150.00"), ("Standing desk", "5", "200.00")]),
    ("PO-24012", "V004", "INR", "18", "PARTIALLY_BILLED", "40000", "Support retainer",
     [("Cloud support retainer (month)", "12", "40000.00")]),
    ("PO-24013", "V009", "USD", "0", "OPEN", "0", "Trade-show banners (vendor inactive)",
     [("Banner printing", "50", "20.00")]),
    ("PO-24014", "V008", "USD", "0", "OPEN", "0", "Strategy workshops (vendor blocked)",
     [("Strategy workshop (day)", "3", "4000.00")]),
    ("PO-24015", "V006", "EUR", "19", "CANCELLED", "0", "Pallet wrap (cancelled)",
     [("Pallet wrap film", "100", "3.50")]),
]
