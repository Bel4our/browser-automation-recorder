import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from impressum import (
    CompanyStatus,
    evaluate_extraction,
    extract_visible_text,
    find_impressum_link,
    normalize_url,
    optimize_impressum_text,
)


def test_normalize_url():
    assert normalize_url("nutrimmun.de") == "https://nutrimmun.de"
    assert normalize_url("https://baroloandchampagne.com") == "https://baroloandchampagne.com"
    assert normalize_url("   ") is None
    assert normalize_url(None) is None


def test_find_impressum_link_exact_and_fallback():
    html_exact = '<html><body><footer><a href="/legal">Imprint</a><a href="/x">Other</a></footer></body></html>'
    assert find_impressum_link(html_exact, "https://example.com") == "https://example.com/legal"

    html_href = '<html><body><a href="../impressum.html">Legal Notice</a></body></html>'
    assert find_impressum_link(html_href, "https://example.com/de/home") == "https://example.com/impressum.html"

    html_none = '<html><body><a href="/contact">Contact</a></body></html>'
    assert find_impressum_link(html_none, "https://example.com") is None


def test_extract_visible_text_removes_noise_tags():
    html = """
    <html>
      <head><style>.a{color:red}</style><script>console.log(1)</script></head>
      <body>
        <nav>Menu Item</nav>
        <main><h1>Impressum</h1><p>ACME GmbH</p></main>
        <footer>Copyright</footer>
      </body>
    </html>
    """
    txt = extract_visible_text(html)
    assert "console.log" not in txt
    assert "Menu Item" not in txt
    assert "Impressum" in txt
    assert "ACME GmbH" in txt


def test_optimize_impressum_text_under_and_over_threshold():
    short = "Impressum\nACME GmbH\nTel: +49 30 123456"
    out_short, stats_short = optimize_impressum_text(short, max_chars=8000)
    assert out_short == short
    assert stats_short["strategy"] == "unchanged_under_threshold"

    noise_head = ("Navigation Link\nCookie Einstellungen\n" * 300)
    legal_body = (
        "Angaben gemäß § 5 DDG\n"
        "ACME GmbH\nMusterstraße 12\n10115 Berlin\n"
        "Tel: +49 30 987654\nE-Mail: info@acme.de\n"
        "HRB 12345 Amtsgericht Berlin\nUSt-IdNr.: DE123456789\n"
    )
    noise_tail = ("\nDatenschutz\nAGB\n" + "Filler text footer. " * 400)
    full = noise_head + legal_body + noise_tail

    out_long, stats_long = optimize_impressum_text(full, max_chars=2000)
    assert len(out_long) <= 2000
    assert "ACME GmbH" in out_long
    assert "DE123456789" in out_long
    assert stats_long["after_tokens"] < stats_long["before_tokens"]


def test_evaluate_extraction_statuses():
    s_fail, _ = evaluate_extraction({}, "Fallback GmbH")
    assert s_fail == CompanyStatus.PARSING_FAILED

    s_incomplete, mapped_inc = evaluate_extraction(
        {
            "company_name": "Test GmbH",
            "postal_address": {"street": "Main St", "house_no": None, "postcode": "10115", "city": "Berlin", "country": "Germany"},
        },
        "Fallback GmbH",
    )
    assert s_incomplete == CompanyStatus.ADDRESS_INCOMPLETE
    assert mapped_inc["legal_name"] == "Test GmbH"

    s_ok, mapped_ok = evaluate_extraction(
        {
            "company_name": None,
            "postal_address": {"street": "Main St", "house_no": "12", "postcode": "10115", "city": "Berlin", "country": None},
            "email": "INFO@TEST.DE",
        },
        "Fallback GmbH",
    )
    assert s_ok == CompanyStatus.OK
    assert mapped_ok["legal_name"] == "Fallback GmbH"
    assert mapped_ok["email"] == "info@test.de"
    assert mapped_ok["postal_address"] == "Main St 12, 10115 Berlin"