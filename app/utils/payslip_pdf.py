"""
The salary slip PDF — ReportLab
──────────────────────────────
Like `payroll_calc.py`, this file is EMPTY of everything else: no DB, no
HTTP. A dict goes in and PDF bytes come out.

Same benefit: the PDF can be tested without starting a server, and the
PDF-building code can be called from anywhere.

═══════════════════════════════════════════════════════════
ONE TEMPLATE, EVERY COMPANY
═══════════════════════════════════════════════════════════
This is a multi-company system — each CEO supplies their own logo, colour
and address. The layout stays the same; only these change. So branding is
a parameter, never hardcoded.

With no colour, or a bad one, the system default green is used — building
a PDF must never fail just because the CEO typed "red" in the colour field.
"""

import io
import re
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

DEFAULT_ACCENT = colors.HexColor("#05DC7F")
INK = colors.HexColor("#1A1A1A")
MUTED = colors.HexColor("#6B7280")
RULE = colors.HexColor("#E5E7EB")
BAND = colors.HexColor("#F7F8F8")


# ══════════════════════════════════════════════
# The amount in words — the Pakistani convention
# ══════════════════════════════════════════════
ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
        "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
        "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
        "Eighty", "Ninety"]


def _two(n: int) -> str:
    if n < 20:
        return ONES[n]
    return (TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else "")).strip()


def _three(n: int) -> str:
    """0-999"""
    out = []
    if n >= 100:
        out.append(ONES[n // 100] + " Hundred")
        n %= 100
    if n:
        out.append(_two(n))
    return " ".join(out)


def amount_in_words(amount) -> str:
    """
    142248.50 → "One Lac Forty Two Thousand Two Hundred Forty Eight Rupees
                 and Fifty Paisa Only"

    The Pakistani/Indian convention is used — lac and crore, not million.
    A slip should follow local practice.

    This is not decoration: writing the amount in words as well as digits
    protects the slip from tampering (changing a digit is easy, changing
    mushkil).
    """
    amount = Decimal(str(amount or 0))
    if amount < 0:
        return "Zero Rupees Only"

    rupees = int(amount)
    paisa = int((amount - rupees) * 100)

    if rupees == 0:
        words = "Zero"
    else:
        parts = []
        crore = rupees // 10_000_000
        rupees %= 10_000_000
        lac = rupees // 100_000
        rupees %= 100_000
        thousand = rupees // 1_000
        rest = rupees % 1_000

        if crore:
            parts.append(_three(crore) + " Crore")
        if lac:
            parts.append(_two(lac) + " Lac")
        if thousand:
            parts.append(_two(thousand) + " Thousand")
        if rest:
            parts.append(_three(rest))
        words = " ".join(parts)

    out = f"{words} Rupees"
    if paisa:
        out += f" and {_two(paisa)} Paisa"
    return out + " Only"


# ══════════════════════════════════════════════
# Madadgar
# ══════════════════════════════════════════════
def _accent(hex_color) -> colors.Color:
    """
    Take the branding colour — fall back to the default if it is invalid.

    Building a PDF must never fail because something odd was typed into
    the colour field.
    """
    if not hex_color:
        return DEFAULT_ACCENT
    text = str(hex_color).strip()
    if not re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", text):
        return DEFAULT_ACCENT
    try:
        return colors.HexColor(text)
    except Exception:
        return DEFAULT_ACCENT


def money(value) -> str:
    """142248.5 → '142,248.50' — thousands separator, always 2 decimals"""
    return f"{Decimal(str(value or 0)):,.2f}"


def _logo_flowable(logo_bytes, max_h=16 * mm):
    """
    Make the logo fit to place into the PDF.

    Returns None on a bad image — the slip is still produced, just without
    a logo. Nobody's slip should be held up by one broken logo.
    """
    if not logo_bytes:
        return None
    try:
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(logo_bytes)) as im:
            w, h = im.size
        if not w or not h:
            return None
        ratio = w / h
        return Image(io.BytesIO(logo_bytes), width=max_h * ratio, height=max_h)
    except Exception:
        return None


# ══════════════════════════════════════════════
# The PDF itself
# ══════════════════════════════════════════════
def build_payslip_pdf(slip: dict) -> bytes:
    """
    One salary slip as a PDF.

    `slip` must contain:
        company:    {name, address, email, phone, footer, color, logo_bytes}
        employee:   {name, employee_id, department, designation}
        period:     "May 2026"
        attendance: {present_days, working_days, net_hours, overtime_hours,
                     late_count, paid_leave_days, unpaid_leave_days}
        earnings:   [(label, amount), ...]
        deductions: [(label, amount), ...]
        totals:     {gross, deductions, net}
        currency:   "PKR"
        generated:  "2026-06-01"
    """
    company = slip.get("company") or {}
    employee = slip.get("employee") or {}
    att = slip.get("attendance") or {}
    totals = slip.get("totals") or {}
    currency = slip.get("currency") or "PKR"
    accent = _accent(company.get("color"))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Salary Slip - {slip.get('period', '')}",
        author=company.get("name") or "Agentra",
    )

    ss = getSampleStyleSheet()
    st_company = ParagraphStyle("c", parent=ss["Normal"], fontName="Helvetica-Bold",
                                fontSize=13, textColor=INK, leading=16)
    st_small = ParagraphStyle("s", parent=ss["Normal"], fontSize=7.5,
                              textColor=MUTED, leading=10)
    st_title = ParagraphStyle("t", parent=ss["Normal"], fontName="Helvetica-Bold",
                              fontSize=15, textColor=accent, alignment=TA_RIGHT,
                              leading=18)
    st_period = ParagraphStyle("p", parent=ss["Normal"], fontSize=8.5,
                               textColor=MUTED, alignment=TA_RIGHT)
    st_sec = ParagraphStyle("sec", parent=ss["Normal"], fontName="Helvetica-Bold",
                            fontSize=8, textColor=MUTED, leading=11)
    st_words = ParagraphStyle("w", parent=ss["Normal"], fontSize=8,
                              textColor=INK, leading=11)
    st_foot = ParagraphStyle("f", parent=ss["Normal"], fontSize=7,
                             textColor=MUTED, alignment=TA_CENTER, leading=9)

    story = []

    # ──── Header: logo + company | SALARY SLIP ────
    left = []
    logo = _logo_flowable(company.get("logo_bytes"))
    if logo:
        left.append(logo)
        left.append(Spacer(1, 3))
    left.append(Paragraph(company.get("name") or "Company", st_company))
    for key in ("address", "email", "phone"):
        if company.get(key):
            left.append(Paragraph(str(company[key]), st_small))

    right = [
        Paragraph("SALARY SLIP", st_title),
        Paragraph(slip.get("period", ""), st_period),
    ]

    story.append(Table([[left, right]], colWidths=[105 * mm, 69 * mm],
                       style=TableStyle([
                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                       ])))
    story.append(Spacer(1, 8))
    story.append(Table([[""]], colWidths=[174 * mm], rowHeights=[2.2],
                       style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent)])))
    story.append(Spacer(1, 10))

    # ──── Employee ────
    emp_rows = [
        ["Employee", str(employee.get("name") or "—"),
         "Employee ID", str(employee.get("employee_id") or "—")],
        ["Department", str(employee.get("department") or "—"),
         "Designation", str(employee.get("designation") or "—")],
    ]
    story.append(Table(emp_rows, colWidths=[24 * mm, 63 * mm, 26 * mm, 61 * mm],
                       style=TableStyle([
                           ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                           ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
                           ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
                           ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
                           ("FONTNAME", (3, 0), (3, -1), "Helvetica-Bold"),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                           ("TOPPADDING", (0, 0), (-1, -1), 3),
                       ])))
    story.append(Spacer(1, 10))

    # ──── Attendance — the "why" behind the slip ────
    # This section exists so the employee can see what each deduction was
    # based on. Printing "late: 1500" alone is not enough; "3 days late"
    # has to be visible too.
    story.append(Paragraph("ATTENDANCE SUMMARY", st_sec))
    story.append(Spacer(1, 3))
    a_head = ["Working Days", "Present", "Net Hours", "Overtime", "Late",
              "Paid Leave", "Unpaid Leave"]
    a_body = [
        str(att.get("working_days", "—")),
        str(att.get("present_days", "—")),
        f"{att.get('net_hours', 0):g}",
        f"{att.get('overtime_hours', 0):g} h",
        f"{att.get('late_count', 0)}x",
        str(att.get("paid_leave_days", 0)),
        str(att.get("unpaid_leave_days", 0)),
    ]
    story.append(Table([a_head, a_body], colWidths=[24.8 * mm] * 7,
                       style=TableStyle([
                           ("BACKGROUND", (0, 0), (-1, 0), BAND),
                           ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                           ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                           ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                           ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                           ("GRID", (0, 0), (-1, -1), 0.4, RULE),
                           ("TOPPADDING", (0, 0), (-1, -1), 4),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                       ])))
    story.append(Spacer(1, 12))

    # ──── Earnings | Deductions — side by side ────
    # Pad both columns to the same number of lines so GROSS PAY and TOTAL
    # DEDUCTIONS land on exactly the same row. Otherwise the shorter
    # column's total sits higher and the slip looks lopsided.
    earn_rows = list(slip.get("earnings") or [])
    ded_rows = list(slip.get("deductions") or [])
    rows_needed = max(len(earn_rows), len(ded_rows))

    def block(title, rows, total_label, total_value):
        data = [[title, currency]]
        for label, amount in rows:
            data.append([str(label), money(amount)])
        # Blank lines — purely for alignment
        for _ in range(rows_needed - len(rows)):
            data.append(["", ""])
        data.append([total_label, money(total_value)])

        n = len(data) - 1
        return Table(data, colWidths=[52 * mm, 32 * mm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BAND),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), INK),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LINEABOVE", (0, n), (-1, n), 0.8, accent),
            ("FONTNAME", (0, n), (-1, n), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.4, RULE),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]))

    earn = block("EARNINGS", earn_rows, "GROSS PAY", totals.get("gross", 0))
    ded = block("DEDUCTIONS", ded_rows,
                "TOTAL DEDUCTIONS", totals.get("deductions", 0))

    story.append(Table([[earn, "", ded]], colWidths=[84 * mm, 6 * mm, 84 * mm],
                       style=TableStyle([
                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                       ])))
    story.append(Spacer(1, 12))

    # ──── Net salary ────
    net = totals.get("net", 0)
    net_table = Table(
        [[Paragraph("<b>NET SALARY</b>", ParagraphStyle(
            "n", fontSize=10, textColor=colors.white, leading=13)),
          Paragraph(f"<b>{currency} {money(net)}</b>", ParagraphStyle(
              "nv", fontSize=13, textColor=colors.white,
              alignment=TA_RIGHT, leading=16))]],
        colWidths=[104 * mm, 70 * mm],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), accent),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("LEFTPADDING", (0, 0), (0, 0), 8),
            ("RIGHTPADDING", (-1, 0), (-1, 0), 8),
        ]))
    story.append(net_table)
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"<font color='#6B7280'>In words:</font> {amount_in_words(net)}", st_words))

    # ──── Warnings — never hidden ────
    for w in (slip.get("warnings") or []):
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"<font color='#9A5B14'>Note: {w}</font>", st_small))

    # ──── Footer ────
    story.append(Spacer(1, 16))
    story.append(Table([[""]], colWidths=[174 * mm], rowHeights=[0.6],
                       style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), RULE)])))
    story.append(Spacer(1, 5))

    foot = company.get("footer") or "This is a computer generated salary slip."
    story.append(Paragraph(foot, st_foot))
    if slip.get("generated"):
        story.append(Paragraph(f"Generated on {slip['generated']}", st_foot))

    doc.build(story)
    return buf.getvalue()
