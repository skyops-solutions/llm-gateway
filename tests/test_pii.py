from app.pii import mask_text


def test_masks_email_and_phone():
    text, counts = mask_text("jane@acme.io, +44 20 7946 0958")
    assert text == "[EMAIL], [PHONE]"
    assert counts == {"email": 1, "phone": 1}


def test_keeps_short_numbers_and_prices():
    text, counts = mask_text("Order 12345 costs 99.50 on 2026-11-01")
    assert text == "Order 12345 costs 99.50 on 2026-11-01"
    assert counts == {"email": 0, "phone": 0}
