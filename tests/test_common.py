from __future__ import annotations

from cp_mcp.parsers.common import abs_url, detect_condition, parse_price, parse_product_path


def test_parse_price_basic() -> None:
    assert parse_price("US$ 665,00") == 665.0
    assert parse_price("R$ 3.404,80") == 3404.8
    assert parse_price("US$&nbsp;1.250,00") == 1250.0
    assert parse_price("") is None
    assert parse_price(None) is None
    assert parse_price("abc") is None


def test_parse_product_path() -> None:
    p = parse_product_path("/notebook-apple-macbook-air-2025-apple-m4_59619/")
    assert p == ("notebook-apple-macbook-air-2025-apple-m4", 59619)
    p = parse_product_path("https://www.comprasparaguai.com.br/celular-apple-iphone-15-128gb_48875/")
    assert p == ("celular-apple-iphone-15-128gb", 48875)
    assert parse_product_path("/categorias/") is None
    assert parse_product_path("") is None


def test_abs_url() -> None:
    assert abs_url("/foo/") == "https://www.comprasparaguai.com.br/foo/"
    assert abs_url("https://example.com/x") == "https://example.com/x"
    assert abs_url(None) is None
    assert abs_url("") is None


def test_detect_condition() -> None:
    assert detect_condition("Apple iPhone 15 128GB") == "new"
    assert detect_condition("Apple iPhone 15 128GB Recondicionado") == "recondicionado"
    assert detect_condition("Apple iPhone 16 Pro 128GB CPO") == "cpo"
    # Don't trip on substrings inside other words.
    assert detect_condition("Drone DJI Mavic incpoorating") == "new"
    # Slug forms
    assert detect_condition("", "celular-apple-iphone-15-128gb-recondicionado") == "recondicionado"
    assert detect_condition("", "celular-apple-iphone-16-pro-128gb-cpo") == "cpo"
