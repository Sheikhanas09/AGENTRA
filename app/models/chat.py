"""
HR Help Desk tables
───────────────────
    chat_sessions / chat_messages  — the conversation itself
    hr_requests                    — anything the employee asked FOR
    hr_cases                       — the file HR keeps while it works
    hr_settings                    — every number this company decides by
    hr_nudges                      — what HR has already said unprompted
    employment_records             — the leaver's file

═══════════════════════════════════════════════════════════
THE HELP DESK OWNS NO FACTS
═══════════════════════════════════════════════════════════
It answers from data that already exists — leave_balances,
attendance_sessions, payslips, the indexed policy document. None of that
is copied here. If a balance changes tomorrow, yesterday's answer was
still correct for yesterday, and today's question gets today's number.

The one exception is the conversation transcript, which is a record of
what was said, not a second copy of the data.

═══════════════════════════════════════════════════════════
A TRANSCRIPT IS PRIVATE
═══════════════════════════════════════════════════════════
These messages contain salary figures, sick-leave reasons and attendance
history. An employee asks the help desk things they would not put in an
email to the whole company.

So a transcript belongs to ONE employee. The CEO cannot read it — not
through any route in this system. What the CEO sees is the `hr_requests`
row that came out of the conversation, because that is the part addressed
to them.
"""

from sqlalchemy import (
    Column, Integer, String, Date, DateTime, Text, JSON, Boolean,
    ForeignKey, Index,
)
from app.database import Base
from app.utils.encrypted_column import (
    encrypted_chat_json, encrypted_chat_text)
from datetime import datetime


# ══════════════════════════════════════════════
# Table 1: Chat session
# ══════════════════════════════════════════════
class ChatSession(Base):
    """
    One conversation thread.

    `title` comes from the first question, so the sidebar can show
    "Leave balance" rather than "Session 3".
    """
    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_session_employee", "employee_id", "last_active_at"),
        # ══════════════════════════════════════════════
        # UNINDEXED FOREIGN KEY thi
        # ══════════════════════════════════════════════
        # Har doosri tenant table par `company_id` indexed hai —
        # `chat_messages`, `hr_requests`, `hr_cases`, aur
        # `recruitment.py` ka `_company_fk()` to `index=True` khud
        # laga deta hai. Yeh ek reh gayi thi.
        #
        # Query ki raftaar iska asal maqsad NAHI hai: har query
        # `employee_id` par bhi filter karti hai, jo ek shakhs tak
        # narrow kar deta hai — us ke baad `company_id` chand rows par
        # sasta filter hai.
        #
        # Asal wajah FK hai. `company_id` par `ON DELETE RESTRICT` hai,
        # aur Postgres ko har company delete/update par yeh table scan
        # karni parti hai ke koi child row to nahi. Index ke baghair wo
        # poori table ka sequential scan hai.
        Index("ix_chat_sessions_company", "company_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False)
    company_id = Column(Integer, nullable=False)

    # employee | console
    # ──── Why one table and not two ────
    # A CEO's console conversation is the same shape as an employee's:
    # turns, a title, a last-active time. What differs is who owns it and
    # who may list it, and that is a filter rather than a schema. The
    # column is what keeps them from ever appearing in each other's
    # sidebar — `/chat/sessions` asks for "employee", `/hr/sessions` for
    # "console", and neither can see the other's rows.
    kind = Column(String, nullable=False, default="employee")

    # ══════════════════════════════════════════════
    # Encrypted at rest
    # ══════════════════════════════════════════════
    # The title IS the first question, word for word — "Why was my
    # August salary zero?", "I have a problem with my manager". Reading
    # only the sidebar already tells you most of what somebody asked, so
    # encrypting the transcript and leaving this in plain text would
    # have been a door beside a locked one.
    title = Column(encrypted_chat_text(), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    last_active_at = Column(DateTime, default=datetime.utcnow)


# ══════════════════════════════════════════════
# Table 2: Chat message
# ══════════════════════════════════════════════
class ChatMessage(Base):
    """
    One turn of the conversation.

    ═══ WHY A SEPARATE TABLE AND NOT A JSON COLUMN ═══
    A JSON blob on the session would have to be read and rewritten in full
    on every single message. A long thread then rewrites kilobytes to add
    one line, and two browser tabs open on the same thread would overwrite
    each other's turns.

    Rows also let the last N turns be fetched for context without loading
    a year of history.
    """
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_message_session", "session_id", "id"),
    )

    id = Column(Integer, primary_key=True, index=True)

    # ══════════════════════════════════════════════
    # The tenant column
    # ══════════════════════════════════════════════
    # The transcript. This table reached its company only through its parent row. The
    # routes do look the parent up first and that lookup IS scoped, so
    # there was no known way in — but that is a fact about today's
    # routes. A table without `company_id` is one NEITHER wall can
    # protect: the ORM guard skips it, and no row-level-security policy
    # can be written for it.
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )

    session_id = Column(Integer, ForeignKey("chat_sessions.id", ondelete="CASCADE"),
                        nullable=False)

    # employee | hr   — never "assistant"/"bot": an employee reading their
    # own transcript should see who they were talking to, not what it was
    role = Column(String, nullable=False)

    # ══════════════════════════════════════════════
    # Encrypted at rest — see utils/encrypted_column.py
    # ══════════════════════════════════════════════
    # These rows hold salary figures, sick-leave reasons and complaints
    # about named people. A database dump, a stolen backup or a SQL
    # injection that reads this table used to hand over all of it in
    # plain text.
    #
    # `EncryptedText` handles it in the column rather than at the call
    # sites, so nothing here or anywhere else has to remember — and a
    # route added next year cannot get it wrong. `role`, `intent` and
    # `sources` stay readable: they are tool names and labels, and
    # leaving them alone keeps the audit trail queryable.
    text = Column(encrypted_chat_text(), nullable=False)

    # ──── What the reply was built from ────
    # `intent` and `sources` are the audit trail: when someone says "the
    # help desk told me I had 5 days", this row shows which question was
    # asked and which policy lines or tables the answer came from.
    intent = Column(String, nullable=True)
    sources = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


# ══════════════════════════════════════════════
# Table 3: HR request
# ══════════════════════════════════════════════
class HrRequest(Base):
    """
    Something the employee asked FOR, which needs a person to act on it.

    ═══ WHY LEAVE IS NOT IN HERE ═══
    Leave already has a table, a balance, an approval flow, notifications
    and an auto-approve deadline. Routing chat-created leave into a second
    table would mean two places deciding the same thing — and one of them
    would eventually drift.

    So leave from the chat goes through `POST /leave/request`, exactly
    like the Leave tab. This table is for everything ELSE: a salary
    certificate, an advance, a correction to attendance, a complaint, or a
    question the policy simply does not answer.

    That last one matters. A help desk that says "I do not know" and stops
    is a dead end; one that says "I do not know, I have passed this to HR"
    leaves a row here for the CEO to answer.
    """
    __tablename__ = "hr_requests"
    __table_args__ = (
        Index("ix_hr_request_company", "company_id", "status"),
        Index("ix_hr_request_employee", "employee_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False)
    employee_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False)

    # document | advance | correction | complaint | question | other
    kind = Column(String, nullable=False, default="other")

    # ══════════════════════════════════════════════
    # Encrypted at rest — see utils/encrypted_column.py
    # ══════════════════════════════════════════════
    # Yehi wo matn hai jo transcript se nikal kar yahan aata hai:
    # "Issue with Zeeshan", "confidential chat request regarding...".
    # Chat encrypt karke isay chhorna ek taale ke pehlu mein khula
    # darwaza hota — wohi shikayat, sirf doosri table mein.
    #
    # `kind`, `source` aur `status` plain hain: wo labels hain aur un par
    # queries filter karti hain (`chat.py:422` status par).
    subject = Column(encrypted_chat_text(), nullable=False)
    body = Column(encrypted_chat_text(), nullable=True)

    # chat | form  — where it came from, so the CEO knows the context
    source = Column(String, nullable=False, default="chat")

    # open | resolved | rejected
    status = Column(String, nullable=False, default="open")

    # CEO ka jawab — rad karne ki wajah bhi isi mein hoti hai.
    ceo_note = Column(encrypted_chat_text(), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True)


# ══════════════════════════════════════════════
# Table 4: HR case
# ══════════════════════════════════════════════
class HrCase(Base):
    """
    One concern, alive across turns.

    ═══ WHY THIS EXISTS ═══
    Without it the help desk asked "what happened and when did it start",
    was told, and asked the identical question back. Everything it knew
    lived in a prompt that was rebuilt from scratch every message, so
    "remembering" was something we hoped a model would do rather than
    something the system did.

    A real HR keeps a file. This is the file: what has been established
    so far, what is still missing, and how far along it is. The model
    reads it and writes to it; it does not have to hold it in its head.

    ═══ POSTURE IS NOT DECORATION ═══
    `posture` decides two things at once — how the reply is worded, and
    who is allowed to see the case. An `advisory` case leaves no trace
    for anyone; a `confidential` one is the employee's until they say
    otherwise. Storing it on the case means the rule survives the
    conversation, instead of being re-derived (and re-guessed) each turn.
    """
    __tablename__ = "hr_cases"
    __table_args__ = (
        Index("ix_hr_case_employee", "employee_id", "stage"),
        Index("ix_hr_case_company", "company_id", "stage"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False)
    company_id = Column(Integer, nullable=False)

    # One of the playbook's concerns — never a free string from the model.
    # `chat_cases.py` drops anything the playbook does not recognise.
    concern = Column(String, nullable=False)

    # transactional | advisory | confidential | procedural | judgment
    posture = Column(String, nullable=False, default="procedural")

    # gathering → ready → raised → resolved → closed
    stage = Column(String, nullable=False, default="gathering")

    # Employee ka apna sawal, jyun ka tyun — encrypted.
    subject = Column(encrypted_chat_text(), nullable=True)

    # ──── The file itself ────
    # `facts` is what has been established, as {question: answer}.
    # `still_needed` is what the playbook wanted and has not been told.
    #
    # ⚠ YE DONO BHI TRANSCRIPT HI HAIN.
    # `facts` mein employee ke apne alfaz hote hain, us sawal ke saath
    # jo pucha gaya tha:
    #
    #   {"am i absent yesterday?": "yes", "Which date?": "September 5"}
    #
    # Sirf `subject` encrypt karna aur inhein chhor dena bay-maani hota
    # — asal baat inhi mein hai.
    #
    # `concern`, `posture` aur `stage` plain hain, aur ye jaan-boojh kar
    # hai: wo labels hain aur `chat_cases.py:116` `concern` par filter
    # karta hai — encrypt karte to wo query chup-chaap kuch na deti.
    facts = Column(encrypted_chat_json(), nullable=True)
    still_needed = Column(encrypted_chat_json(), nullable=True)

    # Set from the posture, but kept separately: an employee can ask for
    # something to be treated in confidence whatever its concern.
    confidential = Column(Boolean, nullable=False, default=False)

    session_id = Column(Integer, ForeignKey("chat_sessions.id",
                                            ondelete="SET NULL"),
                        nullable=True)
    hr_request_id = Column(Integer, ForeignKey("hr_requests.id",
                                               ondelete="SET NULL"),
                           nullable=True)

    opened_at = Column(DateTime, default=datetime.utcnow)
    last_touched_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)


# ══════════════════════════════════════════════
# Table 5: HR settings
# ══════════════════════════════════════════════
class HrSettings(Base):
    """
    Every number the HR desk decides anything by, in one place.

    ═══ WHY NOT JUST WRITE `if late_days > 6` ═══
    Because six is not a fact about the world, it is this company's
    opinion. A call centre and an architecture studio do not agree on
    what "too many late arrivals" means, and neither of them should have
    to edit Python to say so.

    Every threshold the proactive jobs and the CEO console use is read
    from here. There is no number at the point of decision anywhere in
    the HR desk — if you find one, it is a bug.

    The defaults below are a starting position, not a rule: the CEO can
    change any of them, and a company that never opens the screen still
    gets sensible behaviour.
    """
    __tablename__ = "hr_settings"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, unique=True)

    # ──── Probation ────
    probation_days = Column(Integer, default=90)
    probation_notice_days = Column(Integer, default=7)

    # ──── Leave ────
    # How early to tell someone their unused days are about to lapse
    leave_expiry_notice_days = Column(Integer, default=30)
    # Below this many days left, mention it when they ask about anything
    leave_low_balance_days = Column(Integer, default=2)

    # ──── Attendance patterns ────
    # "N late arrivals within M days" — worth a quiet word, not a warning
    late_pattern_count = Column(Integer, default=6)
    late_pattern_window_days = Column(Integer, default=30)
    absence_pattern_count = Column(Integer, default=3)
    absence_pattern_window_days = Column(Integer, default=30)

    # ──── Cases and requests ────
    # A case nobody has touched for this long gets a nudge
    case_stale_days = Column(Integer, default=5)
    # How long the CEO may sit on a request before it is chased
    request_sla_days = Column(Integer, default=3)
    # N grievances naming the same person → the CEO is told there is a
    # pattern, WITHOUT any transcript or complainant name
    grievance_cluster_count = Column(Integer, default=3)
    grievance_cluster_window_days = Column(Integer, default=90)

    # ──── Switches ────
    # Proactive messages are the most intrusive thing here. A company
    # that does not want them keeps everything else.
    proactive_to_employee = Column(Boolean, default=True)
    proactive_to_ceo = Column(Boolean, default=True)

    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


# ══════════════════════════════════════════════
# Table 6: Nudge log
# ══════════════════════════════════════════════
class HrNudge(Base):
    """
    What HR has already said, unprompted, so it does not say it again.

    ═══ WHY THIS HAS TO EXIST ═══
    The proactive jobs run every half hour. Without a record of what went
    out, "your probation ends in 7 days" arrives forty-eight times a day
    until it does. One well-timed message is HR; the same message on a
    loop is a malfunction, and the employee stops reading any of them.

    `ref` is what makes a nudge unique — the year for an annual leave
    warning, the month for an attendance one, the id for a specific
    request. Whoever sends the nudge decides what "the same nudge" means.
    """
    __tablename__ = "hr_nudges"
    __table_args__ = (
        Index("ix_hr_nudge_lookup", "company_id", "kind", "ref"),
    )

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False)

    # Null for a nudge addressed to the CEO about the company as a whole
    employee_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=True)

    kind = Column(String, nullable=False)
    ref = Column(String, nullable=False)

    sent_at = Column(DateTime, default=datetime.utcnow)


# ══════════════════════════════════════════════
# Table 7: Employment record
# ══════════════════════════════════════════════
class EmploymentRecord(Base):
    """
    What happened to someone's employment — the leaver's file.

    ═══ WHY A ROW HERE AND NOT A MOVED USER ═══
    A leaver's payslips, attendance and leave requests all point at
    `users.id`. Moving that row to a "former_employees" table, or
    deleting it, would orphan exactly the history a company is required
    to keep — the year of payroll proving what somebody was paid, the
    attendance behind it, the leave they took.

    So the user row stays as the person's identity and this table holds
    the facts of the employment itself: when it started, when it ended,
    why, and whether they were settled up. `users.status` still says
    whether they work here; this says the story.

    ═══ WHY "WHY" IS A FIXED LIST ═══
    `end_reason` is one of a known set, not free text, because it is the
    field somebody will one day filter on — "how many people did we let
    go last year" cannot be answered against prose. The prose goes in
    `end_note`, where nothing depends on its wording.

    ═══ WHO CAN READ IT ═══
    The CEO. An employee has no route to this table at all: their own
    joining date already reaches them through `profile`, and another
    person's exit is not theirs to read. `end_note` in particular may
    carry the reason someone was dismissed.
    """
    __tablename__ = "employment_records"
    __table_args__ = (
        Index("ix_employment_company", "company_id", "ended_on"),
        Index("ix_employment_employee", "employee_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False)
    company_id = Column(Integer, nullable=False)

    # Copied at the time, not looked up later: a name can be corrected
    # and a department can be renamed, and a leaver's record should read
    # the way it did on the day they left.
    name_at_exit = Column(String, nullable=True)
    department_at_exit = Column(String, nullable=True)

    joined_on = Column(Date, nullable=True)
    ended_on = Column(Date, nullable=True)

    # terminated | resigned | retired | contract_ended
    end_reason = Column(String, nullable=False, default="terminated")
    end_note = Column(Text, nullable=True)

    # Payroll owes them nothing further once this is true
    final_settlement_done = Column(Boolean, nullable=False, default=False)
    final_settlement_note = Column(Text, nullable=True)

    recorded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)
