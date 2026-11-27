"""Masking: sensitive values are hidden, ordinary text is left alone."""
import pytest

from stepchaser.mask import mask

HIDDEN = [
    ("my ssn is 123-45-6789 can you check", "SSN", "123-45-6789"),
    ("SSN 123 45 6789", "SSN", "123 45 6789"),
    ("social security number: 123456789 thanks", "SSN", "123456789"),
    ("ITIN 912-70-1234 please", "SSN", "912-70-1234"),
    ("our EIN is 12-3456789", "EIN", "12-3456789"),
    ("account number 000123456789 at the bank", "BANK", "000123456789"),
    ("routing 021000021 and acct 1234567890", "BANK", "021000021"),
    ("my iban GB82WEST12345698765432 is new", "BANK", "GB82WEST12345698765432"),
    ("card 4111 1111 1111 1111 expires soon", "CARD", "4111 1111 1111 1111"),
    ("card 4111-1111-1111-1111", "CARD", "4111-1111-1111-1111"),
    ("DOB 04/24/1990", "DOB", "04/24/1990"),
    ("email me at sam.sample@example.test", "EMAIL", "sam.sample@example.test"),
    ("call 415-555-0134 after five", "PHONE", "415-555-0134"),
    ("call (415) 555 0134", "PHONE", "(415) 555 0134"),
    ("reference 5550134999123 is mine", "NUMBER", "5550134999123"),
]

PLAIN = [
    "how much does tax preparation cost",
    "I need help with credit repair for my business",
    "we file 3 returns a year and have 12 employees",
    "the 2025 deadline is April 15",
    "package costs $149 or $1,500 for the bundle",
    "call us on weekdays between 9 and 5",
    "my zip is 94107",
    "order 12345 shipped on 03/04/2026",
]


@pytest.mark.parametrize("text,kind,secret", HIDDEN)
def test_sensitive_values_are_replaced(text, kind, secret):
    m = mask(text)
    digits = "".join(ch for ch in secret if ch.isdigit())
    assert secret not in m.text
    if digits:
        assert digits not in "".join(ch for ch in m.text if ch.isdigit()), m.text
    assert kind in m.kinds and m.sensitive


@pytest.mark.parametrize("text", PLAIN)
def test_ordinary_text_is_untouched(text):
    m = mask(text)
    assert m.text == text and not m.sensitive


def test_card_check_uses_luhn_so_a_long_reference_is_not_called_a_card():
    m = mask("tracking 4111 1111 1111 1112")
    assert "CARD" not in m.kinds and "NUMBER" in m.kinds        # still hidden, labelled as a number


def test_counts_each_kind():
    m = mask("ssn 123-45-6789 and 987-65-4321, email a@example.test")
    assert m.kinds == {"SSN": 2, "EMAIL": 1}


def test_empty_and_none():
    assert mask("").text == "" and mask(None).text == ""
