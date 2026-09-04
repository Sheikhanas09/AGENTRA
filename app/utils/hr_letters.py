"""
The letters HR writes
─────────────────────
An employment letter, an experience letter, a salary certificate. Small
documents, asked for constantly, and the one thing in the transcript the
employee wanted that this system could not produce at all:

    "mera experience letter chahiye"
    -> "I don't have that information."

Every fact in these letters is already on record — joining date,
department, salary, whether they still work here. Nobody should have to
ask a person to retype them.

═══════════════════════════════════════════════════════════
WHAT THIS FILE MAY AND MAY NOT SAY
═══════════════════════════════════════════════════════════
It states facts held in the database and nothing else. It does not
praise, assess or recommend — an experience letter that calls someone
"an excellent team player" is making a claim no table in this system
supports, and the company would be the one standing behind it.

Salary appears only when the letter was asked to include it. A bank
needs it; a landlord asking for proof of employment does not, and the
default is the one that shares less.

═══════════════════════════════════════════════════════════
IT IS STILL SIGNED BY A PERSON
═══════════════════════════════════════════════════════════
Producing the PDF is not the same as issuing it. The letter is generated
once the CEO approves the request — the same `hr_requests` row the case
system raises. That keeps a human between "an employee asked" and "the
company has certified", which is the whole point of a certificate.
"""

import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from app.utils.payslip_pdf import _accent, _logo_flowable, money

# What a letter can be. The model never invents one of these — it picks
# from this list, exactly like a tool name.
LETTER_KINDS = {
    "employment": {
        "title": "EMPLOYMENT CERTIFICATE",
        "for": "confirming current employment",
        "needs_active": True,
    },
    "experience": {
        "title": "EXPERIENCE CERTIFICATE",
        "for": "confirming a completed period of service",
        "needs_active": False,
    },
    "salary": {
        "title": "SALARY CERTIFICATE",
        "for": "confirming employment and pay, usually for a bank",
        "needs_active": True,
    },
}


def _duration(start: date, end: date) -> str:
    """"2 years 4 months", the way a letter says it."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    months = max(0, months)
    years, months = divmod(months, 12)

    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    return " ".join(parts) or "less than a month"


def build_letter_pdf(kind: str, employee: dict, company: dict,
                     addressed_to: str = None,
                     include_salary: bool = False,
                     purpose: str = None) -> bytes:
    """
    One letter, as bytes.

    `employee` and `company` are plain dicts assembled by the caller, so
    this function touches no database and can be tested on its own.
    """
    spec = LETTER_KINDS.get(kind) or LETTER_KINDS["employment"]
    accent = _accent(company.get("primary_color"))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=spec["title"],
        author=company.get("name") or "Company",
    )

    ss = getSampleStyleSheet()
    st_company = ParagraphStyle("co", parent=ss["Normal"],
                                fontName="Helvetica-Bold", fontSize=13,
                                leading=15, textColor=accent)
    st_small = ParagraphStyle("sm", parent=ss["Normal"], fontSize=7.5,
                              leading=10, textColor=colors.HexColor("#555555"))
    st_title = ParagraphStyle("ti", parent=ss["Normal"],
                              fontName="Helvetica-Bold", fontSize=12,
                              leading=15, spaceBefore=6, spaceAfter=2)
    st_body = ParagraphStyle("bd", parent=ss["Normal"], fontSize=9.5,
                             leading=15)
    st_foot = ParagraphStyle("ft", parent=ss["Normal"], fontSize=7,
                             leading=9,
                             textColor=colors.HexColor("#777777"))

    flow = []

    # ──── Letterhead ────
    left = [Paragraph(company.get("name") or "Company", st_company)]
    for line in (company.get("address"), company.get("email"),
                 company.get("phone")):
        if line:
            left.append(Paragraph(str(line), st_small))

    logo = _logo_flowable(company.get("logo"))
    head = Table([[left, logo or ""]], colWidths=[110 * mm, 56 * mm])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    flow += [head, Spacer(1, 4 * mm)]

    rule = Table([[""]], colWidths=[166 * mm], rowHeights=[1.2])
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent)]))
    flow += [rule, Spacer(1, 6 * mm)]

    issued = employee.get("issued_on") or str(date.today())
    flow.append(Paragraph(f"Date: {issued}", st_body))
    flow.append(Spacer(1, 4 * mm))

    if addressed_to:
        flow.append(Paragraph(f"To: {addressed_to}", st_body))
    else:
        flow.append(Paragraph("TO WHOM IT MAY CONCERN", st_body))
    flow.append(Spacer(1, 5 * mm))

    flow.append(Paragraph(spec["title"], st_title))
    flow.append(Spacer(1, 4 * mm))

    # ──── The body ────
    name = employee.get("name") or "the employee"
    joined = employee.get("joined")
    left_on = employee.get("left_on")
    dept = employee.get("department")
    still_here = employee.get("active", True)

    role_clause = f" as {dept}" if dept else ""

    if still_here:
        sentence = (
            f"This is to certify that <b>{name}</b> has been employed with "
            f"{company.get('name') or 'this company'}{role_clause} since "
            f"<b>{joined}</b>"
        )
        if employee.get("duration"):
            sentence += f", a period of {employee['duration']}"
        sentence += ", and remains in our employment as of the date above."
    else:
        sentence = (
            f"This is to certify that <b>{name}</b> was employed with "
            f"{company.get('name') or 'this company'}{role_clause} from "
            f"<b>{joined}</b> to <b>{left_on}</b>"
        )
        if employee.get("duration"):
            sentence += f", a period of {employee['duration']}"
        sentence += "."

    flow.append(Paragraph(sentence, st_body))
    flow.append(Spacer(1, 4 * mm))

    if include_salary and employee.get("gross_monthly") is not None:
        cur = employee.get("currency") or "PKR"
        flow.append(Paragraph(
            f"Their current gross monthly remuneration is "
            f"<b>{cur} {money(employee['gross_monthly'])}</b>.", st_body))
        flow.append(Spacer(1, 4 * mm))

    if purpose:
        flow.append(Paragraph(
            f"This certificate has been issued at their request for the "
            f"purpose of {purpose}.", st_body))
    else:
        flow.append(Paragraph(
            "This certificate has been issued at their request.", st_body))

    flow.append(Spacer(1, 14 * mm))

    # ──── Signature ────
    # A blank block, not a rendered name. A certificate whose signature
    # the software supplies is not certifying anything.
    flow.append(Paragraph("For and on behalf of "
                          f"{company.get('name') or 'the company'},", st_body))
    flow.append(Spacer(1, 16 * mm))

    sig = Table([[""]], colWidths=[64 * mm], rowHeights=[0.8])
    sig.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#999999"))]))
    flow.append(sig)
    flow.append(Paragraph("Authorised Signatory", st_small))

    flow.append(Spacer(1, 10 * mm))
    if company.get("footer"):
        flow.append(Paragraph(str(company["footer"]), st_foot))

    doc.build(flow)
    return buf.getvalue()


# ══════════════════════════════════════════════
# Assembling the facts
# ══════════════════════════════════════════════
def letter_context(db, employee_id: int, company_id: int) -> dict:
    """
    Everything a letter needs, read from what is already on record.

    Nothing is asked of the employee that the system already knows —
    that is the whole reason this exists.
    """
    from app.models.payroll import CompanyBranding, SalaryStructure
    from app.models.user import User
    from app.utils.pkt import get_pkt_today

    u = db.query(User).filter(User.id == employee_id).first()
    if not u:
        return {}

    # By `company_id` — a company is not its CEO's user row. This letter
    # carries the CEO's name as the signatory, so getting None here put
    # an unsigned letter in front of a bank.
    ceo = db.query(User).filter(
        User.company_id == company_id, User.role == "ceo").first()
    brand = db.query(CompanyBranding).filter(
        CompanyBranding.company_id == company_id).first()
    salary = db.query(SalaryStructure).filter(
        SalaryStructure.employee_id == employee_id).first()

    today = get_pkt_today()
    gross = None
    if salary:
        gross = float(salary.base_salary or 0) + sum([
            float(salary.house_allowance or 0),
            float(salary.transport_allowance or 0),
            float(salary.medical_allowance or 0),
            float(salary.other_allowances or 0),
        ])

    return {
        "employee": {
            "name": u.full_name,
            "department": u.department,
            "joined": str(u.joining_date) if u.joining_date else None,
            "active": u.status == "active",
            "left_on": None if u.status == "active" else str(today),
            "duration": _duration(u.joining_date, today)
                        if u.joining_date else None,
            "gross_monthly": gross,
            "currency": (salary.currency if salary else None) or "PKR",
            "issued_on": str(today),
        },
        "company": {
            "name": (ceo.company_name if ceo else None) or "Company",
            "address": brand.company_address if brand else None,
            "email": brand.contact_email if brand else None,
            "phone": brand.contact_phone if brand else None,
            "logo": brand.logo_data if brand else None,
            "primary_color": (brand.primary_color if brand else None),
            "footer": brand.footer_text if brand else None,
        },
    }
