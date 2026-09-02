"""
How this company's HR system actually works
───────────────────────────────────────────
"How do I apply for leave?" is one of the most common things an employee
asks, and until this file existed it was one of the worst-answered.

The help desk had records and policy, but no knowledge of ITSELF — so a
model with nothing to go on filled the gap from its general idea of what
an HR system looks like. It told a real employee to "log in to the
attendance system between 09:00 and 09:15", which is not a thing that
exists here. That is worse than "I don't know": the employee follows it,
it does not work, and they stop believing the next answer too.

So the steps below are the ACTUAL screens and buttons of this product,
written down once. Where a step depends on how this company is set up —
whether a photo is taken, whether the office location is checked, which
leave types need a certificate — it is read from that company's own
configuration rather than assumed.

═══════════════════════════════════════════════════════════
NOTHING HERE MENTIONS AUTOMATION
═══════════════════════════════════════════════════════════
The employee is being told how to use their HR system. That HR decisions
partly run on a schedule is not their business and not their concern —
it is the company's arrangement, and telling them changes how they write
a leave reason. So: "HR will respond", never "it is auto-approved after
48 hours".
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.models.attendance import (
    CompanyWorkPolicy, CompanyLeaveType, OfficeLocation, FaceEnrollment,
)


def get_how_it_works(db: Session, employee_id: int, company_id: int,
                     topic: Optional[str] = None) -> dict:
    """
    The steps for the thing they are asking how to do.

    `topic` narrows it; without one they get the lot, which is still
    small. Sending every topic every time would crowd out their actual
    records in the prompt, and the records are what the answer needs.
    """
    policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == company_id).first()

    office = db.query(OfficeLocation).filter(
        OfficeLocation.company_id == company_id,
        OfficeLocation.is_active == True,          # noqa: E712
    ).first()

    enrolled = db.query(FaceEnrollment).filter(
        FaceEnrollment.employee_id == employee_id).first() is not None

    cert_types = [
        t.label for t in db.query(CompanyLeaveType).filter(
            CompanyLeaveType.company_id == company_id,
            CompanyLeaveType.is_enabled == True,       # noqa: E712
            CompanyLeaveType.requires_certificate == True,   # noqa: E712
        ).all()
    ]

    notice_types = [
        f"{t.label} ({t.advance_notice_days} days notice)"
        for t in db.query(CompanyLeaveType).filter(
            CompanyLeaveType.company_id == company_id,
            CompanyLeaveType.is_enabled == True,       # noqa: E712
        ).all()
        if (t.advance_notice_days or 0) > 0
    ]

    # ──── Attendance ────
    check_in_steps = ["Open the Attendance tab and press Check In."]
    if office:
        # The office NAME goes in parentheses rather than into the
        # sentence. A company that called its site "python technologies"
        # produced "aapko python technologies par hona zaroori hai" —
        # correct, and reading like a mistake.
        check_in_steps.append(
            "Your location is confirmed at that moment, so you need to be "
            f"at the office ({office.office_name}) — checking in from "
            "elsewhere is recorded against the day."
        )
    check_in_steps.append(
        "A photo is taken with the check-in. If the camera is unavailable "
        "the check-in still goes through."
    )
    if policy and policy.shift_start:
        check_in_steps.append(
            f"The shift starts at {policy.shift_start}"
            + (f", with {policy.late_tolerance_mins} minutes' grace"
               if policy and policy.late_tolerance_mins else "")
            + " — after that the day is marked late."
        )

    topics = {
        "apply_leave": {
            "where": "Leave tab",
            "steps": [
                "Open the Leave tab and press Apply for Leave.",
                "Choose the leave type, the from and to dates, and write "
                "the reason.",
                "Attach a medical certificate if the type needs one.",
                "Submit. HR reviews it and you are told the outcome by "
                "email, and on your dashboard.",
            ],
            "needs_certificate": cert_types or None,
            "advance_notice": notice_types or None,
            "also": "You can ask here instead — say the dates and the "
                    "reason, check what appears, and confirm it.",
        },
        "cancel_leave": {
            "where": "Leave tab",
            "steps": [
                "Open the Leave tab and find the request.",
                "A request still waiting on HR can be cancelled at any "
                "time.",
                "Approved leave can be cancelled up until the day it "
                "starts. Once it has started, HR has to do it.",
                "Cancelling returns the days to your balance.",
            ],
        },
        "check_in": {
            "where": "Attendance tab",
            "steps": check_in_steps,
            "face_enrolled": enrolled,
        },
        "break_and_check_out": {
            "where": "Attendance tab",
            "steps": [
                "Press Break when you step away and Resume when you are "
                "back — break time is not counted as working time unless "
                "the company includes it.",
                "Press Check Out at the end of the day; a photo is taken "
                "the same way.",
                "If you forget to check out, the day stays open and your "
                "hours for it cannot be worked out. Tell HR the time you "
                "left and it can be corrected — you can raise that from "
                "here.",
            ],
        },
        "salary_slip": {
            "where": "Payroll tab",
            "steps": [
                "Open the Payroll tab — every month you have been paid is "
                "listed.",
                "Press Download on a month to get the slip as a PDF.",
                "Press the row itself to see how that month was worked "
                "out.",
            ],
            "also": "You can ask here for a slip and it is attached to "
                    "the reply.",
        },
        "interviews": {
            "where": "Interviews tab",
            "steps": [
                "The Interviews tab lists any interview you are on the "
                "panel for, with the candidate, the time and the meeting "
                "link.",
            ],
        },

        # ──── The one that was answered from thin air ────
        # Asked where to find jobs, HR said there was no Jobs screen and
        # that openings went out by "internal announcements" — then
        # offered to go and check an "internal job board". None of those
        # exist. The Jobs page has existed the whole time.
        "jobs": {
            "where": "Jobs page",
            "steps": [
                "Open the Jobs page at /jobs — it works without logging "
                "in, so you can share the link with someone outside the "
                "company too.",
                "Search by title, department or skill.",
                "Press Apply on a role. It opens an email to the hiring "
                "manager with the subject already filled in.",
                "Attach your CV as a PDF and send it. That IS the "
                "application — there is no separate form.",
            ],
            "also": "I can tell you what is open right now if you ask.",
        },
        # ──── "where": null, and that is deliberate ────
        # This one has no tab. When it said "Ask HR" the model read it as
        # the NAME of a tab and told a real employee to "go to the Ask HR
        # tab" — a screen that does not exist. Every other topic names a
        # tab the employee can actually see; this one names none, because
        # there is none.
        "personal_details": {
            "where": None,
            "no_tab": "There is no screen for this — it is handled right "
                      "here in the chat.",
            "steps": [
                "Your email, phone, department and password are not "
                "editable from your own account.",
                "Say what needs changing here and it is done for you.",
            ],
        },
    }

    if topic and topic in topics:
        return {"topic": topic, **topics[topic]}
    return {"topics": topics}


# ══════════════════════════════════════════════
# What this system does NOT hold
# ══════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────
# WHY A LIST OF ABSENCES IS WORTH KEEPING
# ─────────────────────────────────────────────────────────────────
# Telling a model "never invent" does not stop it inventing. Given a
# question it has nothing for, it produces the most plausible-sounding
# answer instead of none — and the answers were plausible:
#
#   "your profile is on the Dashboard under My Profile"
#       There is no My Profile. The tabs are Dashboard, Attendance,
#       Interviews, Leave, Payroll, and that is all of them.
#   "salary is credited on the regular payroll date, usually the last
#    working day of the month"
#       No payday is recorded anywhere in this system. That sentence was
#       written from what payroll usually looks like.
#   "repeated lateness may lead to an attendance review by management"
#       There is no attendance review.
#
# The fix is not a stronger warning, it is DATA. A model that is handed
# "we do not have a performance appraisal process" answers confidently
# and correctly. A model handed nothing fills the silence.
#
# Add to this list whenever the help desk is caught making something up.
NOT_IN_THIS_SYSTEM = {
    "performance_reviews":
        "There is no appraisal or performance review process in this "
        "system — no ratings, no goals, no review cycle. Attendance is "
        "the only measure it holds, and attendance is not performance.",

    "promotion":
        "There is no documented promotion or eligibility policy.",

    "payday":
        "No pay date is recorded. Payroll for a month is processed from "
        "the 1st of the following month; when it reaches an account is "
        "not something this system knows.",

    "my_profile_screen":
        "There is no profile screen. The tabs are Dashboard, Attendance, "
        "Interviews, Leave and Payroll. Personal details are changed by "
        "asking here.",

    "attendance_review":
        "There is no formal attendance review or warning process.",

    "training_budget":
        "There is no training or certification budget on record.",

    "announcements":
        "There is no announcements or notice board feature. Jobs are on "
        "the Jobs page; everything else comes by email.",

    "internal_job_board":
        "There is no internal job board. Open roles are on the Jobs "
        "page at /jobs, which is public.",

    "resignation_flow":
        "There is no resignation or exit process in the system yet. "
        "Notice periods come from the policy document if it says "
        "anything; the rest is handled by a person.",
}


def get_system_limits(db, employee_id: int, company_id: int,
                      topic: Optional[str] = None) -> dict:
    """
    What this system genuinely does not have.

    Handed to the model so it can say so plainly instead of describing
    how an HR system usually works. Being able to answer "we do not run
    that here" is a real answer; guessing is not.
    """
    if topic and topic in NOT_IN_THIS_SYSTEM:
        return {"topic": topic, "we_do_not_have": NOT_IN_THIS_SYSTEM[topic]}
    return {"we_do_not_have": NOT_IN_THIS_SYSTEM}
