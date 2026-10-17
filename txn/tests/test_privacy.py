import pytest

from privacy import build_egress, contains_pii, luhn_ok, pseudonym, redact

KEY = b"k"


@pytest.mark.parametrize("text,kind", [
    ("mail me at jo.bloggs+x@mail.co.uk please", "EMAIL"),
    ("ring 07700 900123 today", "PHONE"),
    ("ring +44 7700 900123 today", "PHONE"),
    ("I live at SW1A 1AA now", "POSTCODE"),
    ("my car AB12 CDE was hit", "VRM"),
    ("old car A123 BCD was hit", "VRM"),
    ("policy PN-4471-0098 applies", "POLICY"),
    ("NI number AB 12 34 56 C here", "NINO"),
    ("card 4111 1111 1111 1111 used", "CARD"),
    ("born 03/07/1985 in Leeds", "DOB"),
    ("born 3 July 1985 in Leeds", "DOB"),
])
def test_detectors(text, kind):
    red, found = redact(text, KEY)
    assert [f.kind for f in found] == [kind], found
    assert found[0].value not in red
    assert f"{kind}_" in red


def test_luhn():
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")
    red, found = redact("ref 4111 1111 1111 1112 here", KEY)
    assert not any(f.kind == "CARD" for f in found)


def test_gazetteer_and_name_parts():
    red, found = redact("Alex Doe says Alex was parked. Doe agrees.", KEY, {"name": "Alex Doe"})
    assert "Alex" not in red and "Doe" not in red
    assert {f.kind for f in found} == {"PERSON"}


def test_pseudonyms_are_deterministic_and_normalised():
    assert pseudonym(KEY, "PHONE", "07700 900123") == pseudonym(KEY, "PHONE", "07700900123")
    assert pseudonym(KEY, "PHONE", "07700 900123") == pseudonym(KEY, "PHONE", "+44 7700 900123")
    assert pseudonym(KEY, "PERSON", "Alex") != pseudonym(b"other", "PERSON", "Alex")


def test_tokens_are_not_redetected():
    red, _ = redact("Alex on 07700 900123, car AB12 CDE", KEY, {"name": "Alex"})
    assert not contains_pii(red)


def test_plain_damage_text_untouched():
    text = "Rear bumper dented after reversing into a bollard; left tail lamp cracked."
    assert redact(text, KEY) == (text, [])


def test_egress_allowlist_copies_only_named_fields():
    payload, dropped = build_egress({
        "description_redacted": "bumper dented by PERSON_1a2b3c4d",
        "band": "RED", "ai_score": 0.9, "claimant_name": "Alex Doe", "email": "a@b.com",
    })
    assert set(payload) == {"description_redacted", "band", "ai_score"}
    assert "claimant_name" in dropped and "email" in dropped


def test_egress_drops_text_that_still_has_pii():
    payload, dropped = build_egress({"description_redacted": "call 07700 900123", "band": "GREEN"})
    assert "description_redacted" not in payload and "description_redacted" in dropped
