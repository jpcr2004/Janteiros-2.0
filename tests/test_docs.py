from janteiros.arena.docs import parse_value, quote_terms

PDF = """Quote - Gießerei
Offered terms
Unit price (EUR) 139.80
Payment terms (days net) 14
Index clause none
Our standard terms (apply to everything not stated above)
Lead time (days) 42
Volume per year (pcs) none
Contract term (months) 12
Incoterm FCA
Tooling (EUR) 0.00
Quality commitment (ppm) 250
Chargeback per complaint (EUR) none
Index clause AS-14404, 30% material, deadband 0.0 %, base 2026-08
Price valid from 2027-01-01
Terms fixed by your enquiry
Payment terms (days net) garbage
"""


def test_quote_parser_is_tolerant():
    q = quote_terms(PDF)
    assert q["stated"] == {"unit_price": 139.8, "payment_days": 14, "index_clause": None}
    std = q["standard"]
    assert std["lead_time_days"] == 42 and std["volume_per_year"] is None and std["incoterm"] == "FCA"
    assert std["index_clause"] == {"series": "AS-14404", "material_share": 0.3, "deadband_pct": 0.0,
                                   "base_month": "2026-08"}  # fmt: skip
    assert std["effective_date"] == "2027-01-01" and std["ppm_commitment"] == 250
    assert "fixed" not in q or "payment_days" not in q["fixed"]


def test_parse_value_numbers():
    assert parse_value("unit_price", "1,234.50") == (True, 1234.5)
    assert parse_value("payment_days", "none") == (True, None)
    assert parse_value("payment_days", "soon")[0] is False
