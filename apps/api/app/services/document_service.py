"""Invoice document rendering: PDF (reportlab) + CSV.

Clean-room original layout: our own typography and structure, branded via the
tenant's saved branding snapshot. PDFs embed: invoice meta, party block,
summary, customer-visible line items, notes. Internal-only amounts (margin,
provider cost) are NEVER rendered on a customer document.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models.billing_core import Customer
from app.models.invoices import Invoice, InvoiceLine

MONEY_INNER = "#,##0.00"


def _fmt(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value:,.2f}"


def render_invoice_csv(invoice: Invoice, customer: Customer,
                       lines: list[InvoiceLine]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["invoice_number", invoice.invoice_number])
    w.writerow(["customer", customer.display_name])
    w.writerow(["period_start", invoice.period_start.date().isoformat()])
    w.writerow(["period_end", invoice.period_end.date().isoformat()])
    w.writerow(["currency", invoice.currency])
    w.writerow(["payment_terms", invoice.payment_terms])
    w.writerow([])
    w.writerow(["line", "kind", "group", "description", "quantity", "amount"])
    for ln in lines:
        w.writerow([ln.line_number, ln.kind, ln.group_key, ln.description,
                    f"{ln.quantity}" if ln.quantity is not None else "",
                    f"{ln.amount:.2f}"])
    w.writerow([])
    w.writerow(["subtotal", _fmt(invoice.subtotal)])
    w.writerow(["discounts", _fmt(invoice.discounts_total)])
    w.writerow(["credits", _fmt(invoice.credits_total)])
    w.writerow(["service_fees", _fmt(invoice.fees_total)])
    w.writerow(["adjustments", _fmt(invoice.adjustments_total)])
    w.writerow(["taxes", _fmt(invoice.taxes_total)])
    w.writerow(["prior_period_adjustments", _fmt(invoice.prior_period_adjustments_total)])
    w.writerow(["total", f"{invoice.total:.2f}"])
    return buf.getvalue().encode("utf-8")


def render_invoice_pdf(invoice: Invoice, customer: Customer,
                       lines: list[InvoiceLine], branding: dict | None = None) -> bytes:
    b = branding or {}
    product = str(b.get("product_name") or "Cloud PartnerOps")
    accent = colors.HexColor(str(b.get("primary_color") or "#0F3D5C"))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title=f"Invoice {invoice.invoice_number}",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1x", parent=styles["Title"], fontName="Times-Bold",
                        fontSize=20, textColor=accent, spaceAfter=2)
    meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=9,
                          textColor=colors.HexColor("#555555"))
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8,
                           textColor=colors.HexColor("#333333"))
    story: list = []
    story.append(Paragraph(product, h1))
    story.append(Paragraph("Billing Statement", styles["Heading2"]))
    story.append(Spacer(1, 10))

    info = [
        [Paragraph("<b>Invoice</b>", small), _fmt_inv(invoice)],
        [Paragraph("<b>Bill to</b>", small),
         f"{customer.display_name}  ·  {customer.code}"],
        [Paragraph("<b>Billing period</b>", small),
         f"{invoice.period_start:%b %d, %Y} – {invoice.period_end:%b %d, %Y}"],
        [Paragraph("<b>Currency / terms</b>", small),
         f"{invoice.currency} · {invoice.payment_terms}"],
    ]
    tbl = Table(info, colWidths=[1.6 * inch, 5.15 * inch])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#666666")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 16))

    head = [Paragraph("<b>#</b>", styles["Normal"]),
            Paragraph("<b>Description</b>", styles["Normal"]),
            Paragraph("<b>Qty</b>", styles["Normal"]),
            Paragraph("<b>Amount</b>", styles["Normal"])]
    data = [head]
    for ln in lines:
        if not ln.customer_visible:
            continue
        data.append([
            str(ln.line_number), ln.description[:120],
            f"{ln.quantity:,.0f}" if ln.quantity else "—",
            _fmt(ln.amount),
        ])
    ltbl = Table(data, colWidths=[0.4 * inch, 4.2 * inch, 0.9 * inch, 1.25 * inch],
                 repeatRows=1)
    ltbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFEFEF")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, accent),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (2, 1), (3, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#FAFAF8")]),
    ]))
    story.append(ltbl)
    story.append(Spacer(1, 12))

    summary_rows = [
        ("Subtotal", invoice.subtotal),
        ("Discounts", invoice.discounts_total),
        ("Credits", invoice.credits_total),
        ("Service fees", invoice.fees_total),
        ("Adjustments", invoice.adjustments_total),
        ("Taxes", invoice.taxes_total),
        ("Prior-period adjustments", invoice.prior_period_adjustments_total),
    ]
    sdata = [[k, _fmt(v)] for k, v in summary_rows if v not in (None, Decimal("0"))]
    sdata.append([Paragraph("<b>Total due</b>", styles["Normal"]),
                  Paragraph(f"<b>{_fmt(invoice.total)} {invoice.currency}</b>", styles["Normal"])])
    stbl = Table(sdata, colWidths=[3.6 * inch, 1.75 * inch],
                 hAlign="RIGHT",
                 style=TableStyle([
                     ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                     ("FONTSIZE", (0, 0), (-1, -1), 9),
                     ("LINEABOVE", (0, -1), (-1, -1), 0.75, accent),
                 ]))
    story.append(stbl)

    if invoice.notes_customer:
        story.append(Spacer(1, 14))
        story.append(Paragraph("Notes", styles["Heading3"]))
        story.append(Paragraph(invoice.notes_customer, styles["Normal"]))
    story.append(Spacer(1, 20))
    support = b.get("support_email") or ""
    story.append(Paragraph(
        f"{product} · Generated {invoice.period_end:%Y-%m-%d} · Invoice {invoice.invoice_number} · "
        f"Status {invoice.status}" + (f" · Questions: {support}" if support else ""),
        meta))
    doc.build(story)
    return buf.getvalue()


def _fmt_inv(inv: Invoice) -> str:
    return (f"{inv.invoice_number} · issued {inv.issued_at:%Y-%m-%d}"
            if inv.issued_at else f"{inv.invoice_number} · status {inv.status}")


def render_note_pdf(note, invoice, customer, branding: dict | None = None) -> bytes:
    """Credit/debit note document (same visual language as invoices)."""
    b = branding or {}
    product = str(b.get("product_name") or "Cloud PartnerOps")
    accent = colors.HexColor(str(b.get("primary_color") or "#0F3D5C"))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title=f"{note.kind.capitalize()} Note {note.note_number}",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1n", parent=styles["Title"], fontName="Times-Bold",
                        fontSize=20, textColor=accent, spaceAfter=2)
    meta = ParagraphStyle("metan", parent=styles["Normal"], fontSize=9,
                          textColor=colors.HexColor("#555555"))
    small = ParagraphStyle("smalln", parent=styles["Normal"], fontSize=8,
                           textColor=colors.HexColor("#333333"))
    story: list = [
        Paragraph(product, h1),
        Paragraph(f"{note.kind.capitalize()} Note", styles["Heading2"]),
        Spacer(1, 10),
    ]
    info = [
        [Paragraph("<b>Note</b>", small), note.note_number],
        [Paragraph("<b>Corrects invoice</b>", small),
         invoice.invoice_number if invoice else "—"],
        [Paragraph("<b>Bill to</b>", small),
         f"{customer.display_name}  ·  {customer.code}" if customer else "—"],
        [Paragraph("<b>Status</b>", small), note.status],
    ]
    tbl = Table(info, colWidths=[1.6 * inch, 5.15 * inch])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 14))
    data = [[Paragraph("<b>#</b>", styles["Normal"]),
             Paragraph("<b>Description</b>", styles["Normal"]),
             Paragraph("<b>Amount</b>", styles["Normal"])]]
    for ln in note.lines:
        data.append([str(ln.get("line_number", "")), str(ln.get("description", ""))[:160],
                     _fmt(Decimal(str(ln.get("amount", "0"))))])
    ltbl = Table(data, colWidths=[0.4 * inch, 4.7 * inch, 1.65 * inch], repeatRows=1)
    ltbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFEFEF")),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (2, 1), (2, -1), "RIGHT"),
    ]))
    story.append(ltbl)
    story.append(Spacer(1, 12))
    label = "Credit total" if note.kind == "credit" else "Debit total"
    sdata = [[Paragraph(f"<b>{label}</b>", styles["Normal"]),
              Paragraph(f"<b>{_fmt(note.amount)} {note.currency}</b>", styles["Normal"])]]
    story.append(Table(sdata, colWidths=[3.6 * inch, 1.75 * inch], hAlign="RIGHT",
                       style=TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"),
                                         ("LINEABOVE", (0, 0), (-1, 0), 0.75, accent)])))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Reason", styles["Heading3"]))
    story.append(Paragraph(note.reason[:1500], styles["Normal"]))
    story.append(Spacer(1, 20))
    story.append(Paragraph(
        f"{product} · {note.note_number} · against "
        f"{invoice.invoice_number if invoice else '—'} · status {note.status}", meta))
    doc.build(story)
    return buf.getvalue()
