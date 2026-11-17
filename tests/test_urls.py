"""The URL and custom code rules on their own, no HTTP or database."""

import pytest

from app.urls import check_custom_code, normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  https://example.com/a  ", "https://example.com/a"),
        ("\thttps://example.com\n", "https://example.com"),
        ("HTTPS://EXAMPLE.COM/Path/To?Q=Mixed#Frag", "https://example.com/Path/To?Q=Mixed#Frag"),
        ("http://Example.com:8080/x", "http://example.com:8080/x"),
        ("https://User:Pass@Example.COM/x", "https://User:Pass@example.com/x"),
        ("http://[2001:DB8::1]:80/x", "http://[2001:db8::1]:80/x"),
        ("https://example.com/%7Euser?a=1&b=2", "https://example.com/%7Euser?a=1&b=2"),
    ],
)
def test_normalize_url_cleans_scheme_and_host_only(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "must not be empty"),
        ("   ", "must not be empty"),
        ("ftp://example.com/file", "must start with http:// or https://"),
        ("javascript:alert(1)", "must start with http:// or https://"),
        ("example.com/no-scheme", "must start with http:// or https://"),
        ("https://", "must include a host"),
        ("https:///path-only", "must include a host"),
        ("https://example.com:notaport/", "invalid port"),
        ("https://example.com:99999/", "invalid port"),
        ("https://exa mple.com/", "must not contain spaces"),
        ("https://example.com/a\nb", "must not contain spaces"),
    ],
)
def test_normalize_url_rejects_bad_urls(raw, message):
    with pytest.raises(ValueError, match=message):
        normalize_url(raw)


def test_url_length_limit_is_2048_characters():
    base = "https://example.com/"
    assert normalize_url(base + "a" * (2048 - len(base)))
    with pytest.raises(ValueError, match="at most 2048 characters"):
        normalize_url(base + "a" * (2049 - len(base)))


def test_url_length_counts_after_trimming():
    base = "https://example.com/"
    exactly_2048 = base + "a" * (2048 - len(base))
    assert normalize_url(f"   {exactly_2048}   ") == exactly_2048


def test_url_byte_limit_catches_long_non_ascii_urls():
    # 1000 characters, but 3 bytes each: too big for the Postgres index
    with pytest.raises(ValueError, match="2048 bytes"):
        normalize_url("https://example.com/" + "中" * 1000)


@pytest.mark.parametrize("code", ["abc", "my-link", "My_Link_2", "a" * 30, "---"])
def test_valid_custom_codes(code):
    assert check_custom_code(code) == code


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("ab", "3 to 30 characters"),
        ("a" * 31, "3 to 30 characters"),
        ("", "3 to 30 characters"),
        ("has space", "only contain letters"),
        ("slash/es", "only contain letters"),
        ("dot.dot", "only contain letters"),
        ("émoji", "only contain letters"),
    ],
)
def test_invalid_custom_codes(code, message):
    with pytest.raises(ValueError, match=message):
        check_custom_code(code)


@pytest.mark.parametrize("code", ["api", "API", "Docs", "health", "STATIC", "aDmIn"])
def test_reserved_codes_are_blocked_case_insensitively(code):
    with pytest.raises(ValueError, match="reserved"):
        check_custom_code(code)
