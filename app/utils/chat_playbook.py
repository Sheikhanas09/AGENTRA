"""
What HR does before it does anything
────────────────────────────────────
This file exists because the help desk turned into a forwarding machine.

Six replies out of eight in one real conversation ended with "Shall I
take this up for you?". Every concern, however small, became a row in the
CEO's queue. That is not what an HR department is. Most of HR is a
conversation: you ask what actually happened, you settle what you can,
and only what genuinely needs a decision-maker goes up — by then with
enough detail that they can decide it in one read.

The worst example was not even a request:

    Employee: "What is the standard notice period if I plan to
               transition out?"
    Help desk: created "standard notice period for transition out"
               on the CEO's dashboard.

Somebody asked a hypothetical question and their employer was told they
are thinking of leaving. A real HR would answer the question and say
nothing to anyone. Once an employee learns that a question becomes a
report, they stop asking questions — and HR stops hearing anything worth
hearing.

═══════════════════════════════════════════════════════════
DIVIDE AND CONQUER
═══════════════════════════════════════════════════════════
Each entry splits one concern into three parts:

    `ask`      what HR needs to know before acting. ONE question at a
               time in the chat — a form disguised as a conversation is
               still a form.
    `settle`   what HR itself deals with, without troubling anyone.
    `escalate` what genuinely needs the CEO, and what they must be told
               for it to be decidable. If this is None, it NEVER goes up.

═══════════════════════════════════════════════════════════
WHAT "SETTLE" MEANS HERE
═══════════════════════════════════════════════════════════
Not every settlement needs a database write. Answering properly is a
settlement. Reassuring someone that a conversation is private is a
settlement. Taking down what happened, so it is on record and the person
feels heard, is most of what HR does on a difficult day.
"""

from typing import Optional


# Concerns that must NEVER become a row on the CEO's dashboard on their
# own. Asking about resignation terms is not resigning; asking what the
# harassment procedure is, is not filing a complaint.
NEVER_ESCALATE_ALONE = ("exit_terms", "policy_question")


PLAYBOOK = {
    # ══════════════════════════════════════════════
    "grievance": {
        "posture": "confidential",
        "about": "A problem with a colleague, a manager, or how someone "
                 "has been treated.",
        "open_with": "Acknowledge it before anything else. Someone raising "
                     "this has usually thought about it for a while.",
        "ask": [
            "What happened — and when did it start?",
            "Has it happened more than once?",
            "Would you like this handled quietly for now, or formally?",
        ],
        "settle": [
            "Say plainly that this conversation is private.",
            "Take down what they tell you.",
            "If they want it kept informal, keep it informal — do not "
            "escalate a quiet word into a formal complaint.",
        ],
        "escalate": "Only once they have said what happened AND asked for "
                    "it to be taken further. Then it goes up as a formal "
                    "grievance with the account they gave.",
        "never": "Do not open a ticket the moment they mention a "
                 "colleague. Being reported to management is exactly what "
                 "they are afraid of.",
    },

    # ══════════════════════════════════════════════
    "accommodation": {
        "posture": "confidential",
        "about": "An ergonomic chair or desk, equipment, a health-related "
                 "adjustment to how they work.",
        "open_with": "This IS the process — there is no form to find. "
                     "Say so, then ask what they need.",
        "ask": [
            "What do you need, and what is it for?",
            "Is there a doctor's note or recommendation?",
            "How soon do you need it?",
        ],
        "settle": [
            "Small adjustments to how someone works — a different seat, a "
            "monitor stand, a changed break — are noted and arranged.",
        ],
        "escalate": "Anything that has to be bought, once you know what it "
                    "is, why, and how urgent.",
    },

    # ══════════════════════════════════════════════
    "document": {
        "posture": "transactional",
        "about": "A letter — salary certificate, employment letter, "
                 "experience letter, something for a bank or a visa.",
        "open_with": "Straightforward. Just find out what it has to say.",
        "ask": [
            "What is the letter for — a bank, a visa, somewhere else?",
            "Is it addressed to anyone in particular?",
            "Does it need to state your salary?",
        ],
        "settle": [
            "Their salary, joining date and department are already on "
            "record — do not ask for what you can look up.",
        ],
        "escalate": "Once you know what it is for and what it must say. "
                    "The letter needs a signature.",
    },

    # ══════════════════════════════════════════════
    "advance": {
        "posture": "judgment",
        "about": "An advance on salary, or a loan.",
        "open_with": "Check what they already owe before discussing more.",
        "ask": [
            "How much do you need?",
            "Over how many months would you repay it?",
        ],
        "settle": [
            "If they have a loan running, tell them what is outstanding "
            "first — it changes what they ask for.",
        ],
        "escalate": "Yes — money is not yours to approve. Send the amount, "
                    "the repayment period, and anything outstanding.",
    },

    # ══════════════════════════════════════════════
    "increment": {
        "posture": "judgment",
        "about": "A raise, a promotion, a review of their salary.",
        "open_with": "Do not turn this into a form. Ask what has prompted "
                     "it — the answer is the part worth sending up.",
        "ask": [
            "What are you hoping for — a review, or a specific figure?",
            "Has your role changed, or taken on more?",
        ],
        "settle": [
            "If a review cycle is documented, tell them when it is.",
            "Their current salary is on record — state it.",
        ],
        "escalate": "Yes, with what has changed about their work. "
                    "\"Wants an increment\" alone cannot be decided.",
    },

    # ══════════════════════════════════════════════
    "correction": {
        "posture": "procedural",
        "about": "A record that is wrong — attendance for a day, a "
                 "missing check-out, a deduction that looks off.",
        "open_with": "Look the day up BEFORE agreeing or disagreeing. "
                     "Ask for the date if they have not given one, then "
                     "read the record back.",
        "ask": [
            "Which date?",
        ],
        "settle": [
            "Read the day back to them in full: did they check in, at "
            "what time, did they check out, was it even a working day, "
            "was leave approved over it.",
            "Most of the time this ends it. A day they thought was "
            "marked absent turns out to be marked late, or to be a "
            "Sunday, or to be covered by leave they had forgotten.",
        ],
        "escalate": "A PAYROLL figure that is genuinely wrong — a "
                    "deduction, a rate, an allowance — goes up, because "
                    "that is money and it is not yours to settle. An "
                    "ATTENDANCE day does not: see below.",
        "never": "An attendance day never goes up. Check-in is captured "
                 "with a photo and a location at the moment it happens, "
                 "so the record is not an opinion to be appealed — read "
                 "it back and stop. Letting it through meant the same "
                 "day could be raised again and again, and the CEO kept "
                 "receiving something somebody had already checked. "
                 "Never say a record has been changed either; nothing "
                 "here can change attendance.",
    },

    # ══════════════════════════════════════════════
    "work_arrangement": {
        "posture": "judgment",
        "about": "Working from home, hybrid, a shift change, working from "
                 "another city.",
        "open_with": "Tell them the current rule first — it often answers "
                     "the question completely.",
        "ask": [
            "What arrangement are you after, and from when?",
            "Is it for a set period, or ongoing?",
        ],
        "settle": [
            "The shift, the working days and the remote rule are on "
            "record. Give them.",
        ],
        "escalate": "Only for a change to their own arrangement, once you "
                    "know what and from when.",
    },

    # ══════════════════════════════════════════════
    "training": {
        "posture": "judgment",
        "about": "A certification, a course, professional development.",
        "open_with": "Ask what specifically — an answer about \"training\" "
                     "in general is no use to anyone.",
        "ask": [
            "Which certification or course?",
            "What does it cost, and how does it help your work here?",
        ],
        "settle": [],
        "escalate": "Yes, with the course, the cost, and the reason. "
                    "Without those it cannot be decided.",
    },

    # ══════════════════════════════════════════════
    # The two that must never go up on their own
    # ══════════════════════════════════════════════
    "exit_terms": {
        "posture": "advisory",
        "about": "Notice period, resignation process, final settlement, "
                 "what happens to their leave balance if they leave.",
        "open_with": "Answer it as the question it is. Someone asking what "
                     "the notice period is has not resigned.",
        "ask": [],
        "settle": [
            "Give what the policy says. If it is silent, say so plainly.",
            "Their joining date and leave balance are on record.",
        ],
        "escalate": None,
        "never": "NEVER create a request for this. Telling the CEO that "
                 "someone asked about notice periods reports them for "
                 "asking a question, and it is the fastest way to make an "
                 "employee never use this help desk again. If they say "
                 "outright that they ARE resigning, that is different — "
                 "and even then it is theirs to send, not yours.",
    },

    "policy_question": {
        "posture": "advisory",
        "about": "What the rules are — leave, attendance, overtime, "
                 "anything they are asking ABOUT rather than asking FOR.",
        "open_with": "This is a question, not a request.",
        "ask": [],
        "settle": [
            "Answer from the policy and their records.",
            "If it is genuinely not covered, say so — and say you will "
            "find out, only if you have actually asked.",
        ],
        "escalate": None,
        "never": "A question does not become a ticket. If you cannot "
                 "answer it, offer to find out — that is the employee's "
                 "choice to accept, not a row you file on their behalf.",
    },
}


def get_playbook(db, employee_id: int, company_id: int,
                 concern: Optional[str] = None) -> dict:
    """
    How to handle one kind of concern.

    Takes `db` and the ids it does not use, so it can sit in the same
    tool table as everything else — the router names it like any other
    tool and nothing special has to be plumbed for it.
    """
    if concern and concern in PLAYBOOK:
        entry = dict(PLAYBOOK[concern])
        entry["concern"] = concern
        entry["may_escalate"] = concern not in NEVER_ESCALATE_ALONE
        return entry

    # No concern named — hand back the list rather than everything, so
    # the prompt does not fill up with playbooks nobody asked for.
    return {"available": sorted(PLAYBOOK)}
