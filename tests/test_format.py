from __future__ import annotations

from cp_mcp.basket import AssignedItem, BasketSolution, StoreVisit
from cp_mcp.format import format_markdown, format_whatsapp


def _solution() -> BasketSolution:
    return BasketSolution(
        stores_used=2,
        total_usd=2674.00,
        total_brl=13689.36,
        feasible=True,
        visits=[
            StoreVisit(
                store_name="Best Shop Paraguai",
                subtotal_usd=1075.0,
                subtotal_brl=5504.0,
                whatsapp_phone="595973700006",
                whatsapp_url="https://api.whatsapp.com/send?phone=595973700006",
                store_url=None,
                items=[
                    AssignedItem(
                        label='MBA M4 16/512 13.6"',
                        product_id=59619,
                        title='Notebook Apple MacBook Air 2025 Apple M4 / Memória 16GB / SSD 512GB / 13.6"',
                        qty=1,
                        price_usd=1075.0,
                        price_brl=5504.0,
                        offer_id=4801770,
                        image_url="https://example.com/macbook.jpg",
                        product_url="https://www.comprasparaguai.com.br/notebook-apple-macbook-air-2025-apple-m4-memoria-16gb-ssd-512gb-136_59619/",
                    )
                ],
            ),
            StoreVisit(
                store_name="Topdek Informática",
                subtotal_usd=1599.0,
                subtotal_brl=8185.36,
                whatsapp_phone="595900000000",
                whatsapp_url="https://api.whatsapp.com/send?phone=595900000000",
                store_url="https://topdek.example/",
                items=[
                    AssignedItem(
                        label="iPhone 16 Pro Max 256GB",
                        product_id=55844,
                        title="Apple iPhone 16 Pro Max 256GB",
                        qty=1,
                        price_usd=1228.0,
                        price_brl=6286.0,
                        offer_id=999,
                        image_url=None,
                        product_url=None,
                    ),
                    AssignedItem(
                        label="SSD 990 Pro 2TB",
                        product_id=51621,
                        title="SSD M.2 Samsung 990 Pro 2TB",
                        qty=1,
                        price_usd=371.0,
                        price_brl=1899.0,
                        offer_id=12345,
                        image_url=None,
                        product_url=None,
                    ),
                ],
            ),
        ],
    )


def test_markdown_contains_essentials() -> None:
    md = format_markdown(_solution())
    assert "# Lista de compras" in md
    assert "Best Shop Paraguai" in md
    assert "Topdek Informática" in md
    assert "Notebook Apple MacBook Air 2025" in md
    assert "MacBook" in md
    assert "iPhone" in md
    assert "US$ 2,674.00" in md
    assert "R$ 13,689.36" in md
    # WhatsApp link rendered
    assert "api.whatsapp.com" in md


def test_whatsapp_no_markdown_syntax() -> None:
    text = format_whatsapp(_solution())
    # No headers / bold / italics / links
    assert "**" not in text
    assert "##" not in text and "# " not in text
    assert "](http" not in text
    # Has the basics a buyer needs
    assert "LOJA 1: Best Shop Paraguai" in text
    assert "LOJA 2: Topdek" in text  # accent-stripped fine
    assert "595973700006" in text
    assert "Cod. produto: 59619" in text
    assert "TOTAL FINAL: US$ 2,674.00 / R$ 13,689.36" in text


def test_markdown_handles_missing_items() -> None:
    sol = _solution()
    sol.feasible = False
    sol.missing_items = ["RTX 4070 Super"]
    md = format_markdown(sol)
    assert "RTX 4070 Super" in md
    assert "Itens sem oferta" in md
