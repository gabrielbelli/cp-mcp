"""Render a BasketSolution into a buyer-friendly handoff document.

Three formats:
- markdown: for chat / display
- whatsapp: plain text, paste-ready, no markdown syntax
- pdf: printable handoff with thumbnails (requires WeasyPrint, optional)
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Optional

from .basket import BasketSolution


# ---- Number formatting ----------------------------------------------------


def _fmt_usd(value: float) -> str:
    return f"US$ {value:,.2f}"


def _fmt_brl(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"R$ {value:,.2f}"


def _fmt_dual(usd: float, brl: Optional[float]) -> str:
    s = _fmt_usd(usd)
    if brl is not None:
        s += f" / {_fmt_brl(brl)}"
    return s


# ---- Markdown -------------------------------------------------------------


def format_markdown(
    solution: BasketSolution,
    *,
    title: Optional[str] = None,
) -> str:
    """Render a buyer-handoff in Markdown for chat/display."""

    n_items = sum(len(v.items) for v in solution.visits)
    heading = title or f"Lista de compras — {n_items} itens, {len(solution.visits)} loja(s)"

    lines: list[str] = []
    lines.append(f"# {heading}")
    lines.append("")
    lines.append(
        f"**Total: {_fmt_dual(solution.total_usd, solution.total_brl)}**  •  "
        f"{solution.stores_used} loja(s)"
    )
    if not solution.feasible and solution.missing_items:
        lines.append("")
        lines.append(
            "> **Itens sem oferta nas lojas selecionadas:** "
            + ", ".join(f"_{m}_" for m in solution.missing_items)
        )
    lines.append("")

    for idx, visit in enumerate(solution.visits, start=1):
        lines.append(
            f"## {idx}. {visit.store_name} — {_fmt_dual(visit.subtotal_usd, visit.subtotal_brl)}"
        )
        if visit.whatsapp_url or visit.store_url:
            links: list[str] = []
            if visit.whatsapp_url:
                phone = visit.whatsapp_phone or "WhatsApp"
                links.append(f"[WhatsApp ({phone})]({visit.whatsapp_url})")
            if visit.store_url:
                links.append(f"[site da loja]({visit.store_url})")
            lines.append(" • ".join(links))
        for addr in visit.addresses:
            city = f" — {addr.city}" if addr.city else ""
            lines.append(f"- **Endereço:** {addr.address}{city}")
        lines.append("")
        for ai in visit.items:
            qty_prefix = f"({ai.qty}x) " if ai.qty != 1 else ""
            lines.append(f"- **{qty_prefix}{ai.title}**")
            price_line = f"  {_fmt_usd(ai.price_usd)}"
            brl = _fmt_brl(ai.price_brl)
            if brl:
                price_line += f" ({brl})"
            if ai.qty != 1:
                price_line += f" — subtotal {_fmt_usd(ai.price_usd * ai.qty)}"
            lines.append(price_line)
            meta_bits: list[str] = []
            if ai.offer_id:
                meta_bits.append(f"oferta #{ai.offer_id}")
            meta_bits.append(f"produto #{ai.product_id}")
            if ai.product_url:
                meta_bits.append(f"[ficha no Compras Paraguai]({ai.product_url})")
            if meta_bits:
                lines.append("  " + " · ".join(meta_bits))
            if ai.alternatives:
                lines.append("  *Alternativas:*")
                for alt in ai.alternatives[:5]:
                    delta = f" (+{_fmt_usd(alt.delta_usd)})" if alt.delta_usd > 0 else ""
                    lines.append(
                        f"  - {alt.store_name}: {_fmt_usd(alt.price_usd)}"
                        f"{f' / {_fmt_brl(alt.price_brl)}' if alt.price_brl else ''}{delta}"
                    )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---- WhatsApp -------------------------------------------------------------


def format_whatsapp(solution: BasketSolution) -> str:
    """Plain text, no markdown, paste-ready into WhatsApp.

    Designed to be unambiguous for a buyer who doesn't know the products: each
    line names the exact full product title (with storage/colour/year) so they
    can't grab the wrong variant.
    """

    n_items = sum(len(v.items) for v in solution.visits)
    lines: list[str] = []
    lines.append("LISTA DE COMPRAS - COMPRAS PARAGUAI")
    lines.append("")
    lines.append(f"Total: {_fmt_dual(solution.total_usd, solution.total_brl)}")
    lines.append(f"{n_items} item(ns) em {len(solution.visits)} loja(s)")
    if not solution.feasible and solution.missing_items:
        lines.append("")
        lines.append("ATENCAO: Itens nao encontrados nas lojas escolhidas:")
        for m in solution.missing_items:
            lines.append(f"  - {m}")
    lines.append("")
    lines.append("=" * 40)

    for idx, visit in enumerate(solution.visits, start=1):
        lines.append("")
        lines.append(f"LOJA {idx}: {visit.store_name}")
        lines.append(f"Subtotal: {_fmt_dual(visit.subtotal_usd, visit.subtotal_brl)}")
        if visit.whatsapp_phone:
            lines.append(f"WhatsApp: +{visit.whatsapp_phone}")
        if visit.store_url:
            lines.append(f"Site: {visit.store_url}")
        for addr in visit.addresses:
            city = f" - {addr.city}" if addr.city else ""
            lines.append(f"Endereco: {addr.address}{city}")
        lines.append("-" * 40)
        for ai in visit.items:
            qty = f"{ai.qty}x " if ai.qty != 1 else ""
            lines.append(f"* {qty}{ai.title}")
            price_bits = [_fmt_usd(ai.price_usd)]
            brl = _fmt_brl(ai.price_brl)
            if brl:
                price_bits.append(brl)
            lines.append(f"  Preco: {' / '.join(price_bits)}")
            if ai.offer_id:
                lines.append(f"  Cod. oferta: {ai.offer_id}")
            lines.append(f"  Cod. produto: {ai.product_id}")
            if ai.product_url:
                lines.append(f"  Link: {ai.product_url}")
        lines.append("")

    lines.append("=" * 40)
    lines.append(f"TOTAL FINAL: {_fmt_dual(solution.total_usd, solution.total_brl)}")
    return "\n".join(lines).rstrip() + "\n"


# ---- PDF ------------------------------------------------------------------

_PDF_CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; color: #1f2937; font-size: 10.5pt; }
h1 { font-size: 18pt; margin: 0 0 4pt; }
.summary { color: #4b5563; margin-bottom: 14pt; }
.store { border: 1px solid #e5e7eb; border-radius: 6pt; padding: 10pt 12pt; margin-bottom: 12pt; page-break-inside: avoid; }
.store-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6pt; }
.store-name { font-size: 13pt; font-weight: 600; }
.store-subtotal { font-size: 12pt; color: #1f2937; }
.store-contact { font-size: 9pt; color: #6b7280; margin-bottom: 4pt; }
.store-address { font-size: 9.5pt; color: #1f2937; margin-bottom: 8pt; }
.store-address .city { color: #6b7280; }
.item { display: flex; gap: 10pt; padding: 8pt 0; border-top: 1px dashed #e5e7eb; }
.item:first-of-type { border-top: 0; padding-top: 0; }
.item-thumb { width: 64pt; height: 64pt; object-fit: contain; background: #f9fafb; border-radius: 4pt; }
.item-body { flex: 1; }
.item-title { font-weight: 600; }
.item-meta { font-size: 9pt; color: #6b7280; }
.item-meta a { color: #2563eb; text-decoration: none; }
.item-price { font-weight: 600; margin-top: 2pt; }
.alts { font-size: 8.5pt; color: #4b5563; margin-top: 4pt; padding-left: 10pt; border-left: 2pt solid #e5e7eb; }
.alts-label { color: #6b7280; font-weight: 600; }
.alts ul { margin: 1pt 0 0; padding-left: 14pt; }
.alts li { margin: 1pt 0; }
.alts .delta { color: #6b7280; }
.footer { margin-top: 20pt; padding-top: 6pt; border-top: 2px solid #1f2937; display: flex; justify-content: space-between; font-size: 12pt; }
.warn { color: #b91c1c; font-weight: 600; }
"""


def _pdf_html(solution: BasketSolution, title: str) -> str:
    n_items = sum(len(v.items) for v in solution.visits)
    parts: list[str] = []
    parts.append(
        f"<html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
        f"<style>{_PDF_CSS}</style></head><body>"
    )
    parts.append(f"<h1>{html.escape(title)}</h1>")
    parts.append(
        "<div class='summary'>"
        f"{n_items} item(ns) · {len(solution.visits)} loja(s) · "
        f"<strong>{_fmt_dual(solution.total_usd, solution.total_brl)}</strong>"
        "</div>"
    )

    if not solution.feasible and solution.missing_items:
        parts.append("<div class='warn'>Itens nao localizados: ")
        parts.append(html.escape(", ".join(solution.missing_items)))
        parts.append("</div>")

    for visit in solution.visits:
        parts.append("<div class='store'>")
        parts.append(
            "<div class='store-head'>"
            f"<div class='store-name'>{html.escape(visit.store_name)}</div>"
            f"<div class='store-subtotal'>"
            f"{_fmt_dual(visit.subtotal_usd, visit.subtotal_brl)}"
            f"</div>"
            "</div>"
        )
        contact_bits: list[str] = []
        if visit.whatsapp_phone:
            contact_bits.append(f"WhatsApp: +{html.escape(visit.whatsapp_phone)}")
        if visit.store_url:
            contact_bits.append(
                f"<a href='{html.escape(visit.store_url)}'>{html.escape(visit.store_url)}</a>"
            )
        if contact_bits:
            parts.append("<div class='store-contact'>" + " · ".join(contact_bits) + "</div>")

        for addr in visit.addresses:
            city = (
                f" <span class='city'>· {html.escape(addr.city)}</span>" if addr.city else ""
            )
            parts.append(
                f"<div class='store-address'><strong>Endereço:</strong> "
                f"{html.escape(addr.address)}{city}</div>"
            )

        for ai in visit.items:
            parts.append("<div class='item'>")
            if ai.image_url:
                parts.append(f"<img class='item-thumb' src='{html.escape(ai.image_url)}'>")
            else:
                parts.append("<div class='item-thumb'></div>")
            parts.append("<div class='item-body'>")
            qty = f"{ai.qty}× " if ai.qty != 1 else ""
            parts.append(f"<div class='item-title'>{qty}{html.escape(ai.title)}</div>")
            meta = [f"produto #{ai.product_id}"]
            if ai.offer_id:
                meta.append(f"oferta #{ai.offer_id}")
            if ai.product_url:
                meta.append(
                    f"<a href='{html.escape(ai.product_url)}'>ver no Compras Paraguai</a>"
                )
            parts.append(f"<div class='item-meta'>{' · '.join(meta)}</div>")
            price = _fmt_usd(ai.price_usd)
            brl = _fmt_brl(ai.price_brl)
            line = price + (f" ({brl})" if brl else "")
            if ai.qty != 1:
                line += f" — subtotal {_fmt_usd(ai.price_usd * ai.qty)}"
            parts.append(f"<div class='item-price'>{line}</div>")
            if ai.alternatives:
                parts.append("<div class='alts'>")
                parts.append("<span class='alts-label'>Outras lojas:</span><ul>")
                for alt in ai.alternatives[:5]:
                    delta = (
                        f" <span class='delta'>(+{_fmt_usd(alt.delta_usd)})</span>"
                        if alt.delta_usd > 0
                        else ""
                    )
                    brl_alt = (
                        f" / {_fmt_brl(alt.price_brl)}" if alt.price_brl is not None else ""
                    )
                    parts.append(
                        f"<li>{html.escape(alt.store_name)}: "
                        f"{_fmt_usd(alt.price_usd)}{brl_alt}{delta}</li>"
                    )
                parts.append("</ul></div>")
            parts.append("</div></div>")
        parts.append("</div>")

    parts.append(
        "<div class='footer'>"
        f"<div>{datetime.now().strftime('%Y-%m-%d %H:%M')}</div>"
        f"<div><strong>Total: {_fmt_dual(solution.total_usd, solution.total_brl)}</strong></div>"
        "</div>"
    )
    parts.append("</body></html>")
    return "".join(parts)


def format_pdf(
    solution: BasketSolution,
    out_path: Path,
    *,
    title: Optional[str] = None,
) -> Path:
    """Render the solution as a PDF with item thumbnails. Requires WeasyPrint.

    Returns the absolute path to the written PDF.
    """

    try:
        from weasyprint import HTML  # type: ignore
    except (ImportError, OSError) as e:  # pragma: no cover - depends on system libs
        raise RuntimeError(
            "PDF output requires the 'pdf' extra (pip install 'cp-mcp[pdf]') AND "
            "system libs: macOS = `brew install pango`; Debian/Ubuntu = "
            "`apt install libpango-1.0-0 libpangoft2-1.0-0 libcairo2`. "
            "The provided Dockerfile already includes these. "
            f"(underlying error: {e})"
        ) from e

    n_items = sum(len(v.items) for v in solution.visits)
    title = title or f"Lista de compras ({n_items} itens, {len(solution.visits)} lojas)"
    html_doc = _pdf_html(solution, title)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_doc).write_pdf(str(out_path))
    return out_path.resolve()


def format_basket(
    solution: BasketSolution,
    fmt: str = "markdown",
    *,
    out_path: Optional[Path] = None,
    title: Optional[str] = None,
) -> dict:
    """Dispatch helper. Returns {"format": fmt, "content": str} for text formats,
    or {"format": "pdf", "path": str} for PDF.
    """

    fmt = fmt.lower()
    if fmt == "markdown":
        return {"format": "markdown", "content": format_markdown(solution, title=title)}
    if fmt in {"whatsapp", "wa", "text"}:
        return {"format": "whatsapp", "content": format_whatsapp(solution)}
    if fmt == "pdf":
        if out_path is None:
            raise ValueError("PDF format requires out_path")
        path = format_pdf(solution, Path(out_path), title=title)
        return {"format": "pdf", "path": str(path)}
    raise ValueError(f"Unknown format: {fmt!r}; expected markdown, whatsapp, or pdf")
