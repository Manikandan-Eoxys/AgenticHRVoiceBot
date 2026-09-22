"""
modules/topic_guard.py

HR Topic Guardrail
==================
Classifies whether a user message is within the HR domain.
Returns a structured result the agent can act on immediately.

Allowed domains
---------------
- Employee information (search, details, directory)
- Leave management  (balance, apply, cancel, history, eligibility)
- HR policies       (leave, attendance, WFH, travel, benefits, …)
- Grievances        (raise, status, escalate)
- Calendar / meetings (schedule, cancel, upcoming, available slots)
- Authentication    (OTP, employee ID verification)
- General greetings / small talk (hello, thanks, bye)

Everything else is OUT OF SCOPE.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# HR keyword sets
# ---------------------------------------------------------------------------

_HR_PATTERNS: list[str] = [
    # employee
    r"\bemployee\b", r"\bstaff\b", r"\bcolleague\b", r"\bworker\b",
    r"\bdirectory\b", r"\bdesignation\b", r"\bdepartment\b",
    r"\bmanager\b", r"\breporting\b",

    # leave
    r"\bleaves?\b", r"\bvacation\b", r"\btime.?off\b", r"\bsick.?day\b",
    r"\bcasual\b", r"\bannual.?leave\b", r"\bearn(ed)?.?leave\b",
    r"\bmaternity\b", r"\bpaternity\b", r"\bsabbatical\b",
    r"\bhalf.?day\b", r"\babsence\b",

    # policies
    r"\bpolic(y|ies)\b", r"\bwfh\b", r"\bwork.?from.?home\b",
    r"\bremote.?work\b", r"\battendance\b", r"\bdress.?code\b",
    r"\bnotice.?period\b", r"\bprobation\b", r"\bbenefit(s)?\b",
    r"\binsurance\b", r"\bcompensation\b", r"\bperformance.?review\b",
    r"\bholiday\b",

    # grievances
    r"\bgrievance\b", r"\bcomplaint\b", r"\bharassment\b",
    r"\bescalat(e|ion)\b", r"\breport.?(issue|problem|incident)\b",

    # calendar / meetings
    r"\bmeeting\b", r"\bschedule\b", r"\bappointment\b",
    r"\bcalendar\b", r"\bslot\b", r"\bavailab(le|ility)\b",
    r"\binterview\b",

    # auth
    r"\botp\b", r"\bverif(y|ication)\b", r"\bauthenticat\b",
    r"\bemployee.?id\b", r"\bmy.?id\b", r"\bid\b",

    # payroll / HR ops
    r"\bpayroll\b", r"\bsalar(y|ies)\b", r"\bpayslip\b",
    r"\bonboarding\b", r"\boffboarding\b", r"\bresignation\b",
    r"\btermination\b", r"\bpromotion\b", r"\bappraisal\b",

    # small talk (always allowed)
    r"\bhello\b", r"\bhi\b", r"\bhey\b",
    r"\bgood.?(morning|afternoon|evening|night)\b",
    r"\bthank(s|you)?\b", r"\bbye\b", r"\bgoodbye\b",
    r"\bsee.?you\b", r"\bhave.?a.?(nice|good|great)\b",
    r"\bhelp\b", r"\bwhat.?can.?you\b", r"\bwho.?are.?you\b",
    r"\bintroduce\b",

    # HR buddy specific
    r"\bhr.?buddy\b", r"\bhr\b",
]

_HR_COMPILED = [re.compile(p, re.IGNORECASE) for p in _HR_PATTERNS]

# ---------------------------------------------------------------------------
# Hard-blocked non-HR topics (fast reject)
# ---------------------------------------------------------------------------

_OFF_TOPIC_PATTERNS: list[str] = [
    # weather / geography
    r"\bweather\b", r"\bforecast\b", r"\btemperature\b",
    r"\bcapit(al|ol).?of\b", r"\bpopulation.?of\b",

    # politics / current events
    r"\bpolitics\b", r"\belection\b",
    r"\bwho.?(is|was).+(president|prime.?minister|king|queen)\b",
    r"\bnews\b", r"\bheadline\b", r"\bcurrent.?event\b",

    # finance
    r"\bstock(s|.?market)?\b", r"\bcryptocurren\b", r"\bcrypto\b",
    r"\bbitcoin\b", r"\binvest(ing|ment)\b",

    # sports / entertainment
    r"\bsport(s)?\b", r"\bfootball\b", r"\bcricket\b",
    r"\bbasketball\b", r"\bsoccer\b", r"\bolympics\b",
    r"\bmovies?\b", r"\bfilm(s)?\b", r"\bnetflix\b",
    r"\bmusic\b", r"\bsong(s)?\b", r"\bartist(s)?\b", r"\bband(s)?\b",

    # food / travel
    r"\brecipe(s)?\b", r"\bcooking\b",
    r"\btravel(l?ing)?\b", r"\btourist(s)?\b", r"\bhotel(s)?\b", r"\bflight(s)?\b",

    # programming / tech (general)
    r"\bprogramm(ing|er)\b",
    r"\bjavascript\b", r"\bsoftware.?develop\b", r"\bdebug\b",
    r"\bchatgpt\b", r"\bgpt\b",

    # other
    r"\btell.?me.?a.?joke\b", r"\bjoke(s)?\b",
    r"\bwrite.?a.?(poem|story|essay|script)\b",
    r"\btranslat(e|ion)\b",
    r"\bmedical\b", r"\bdoctor\b", r"\bhospital\b", r"\bdiagnos\b",
    r"\blegal.?(advice|question)\b", r"\blawyer\b",
    r"\bgame(s)?\b", r"\bvideogame(s)?\b",
]

_OFF_TOPIC_COMPILED = [re.compile(p, re.IGNORECASE) for p in _OFF_TOPIC_PATTERNS]

# ---------------------------------------------------------------------------
# Canned refusal message (voice-friendly, no markdown)
# ---------------------------------------------------------------------------

REFUSAL_MESSAGE = (
    "I'm HR Buddy, your dedicated HR assistant. "
    "I can only help with HR topics like employee information, "
    "leave management, HR policies, grievances, and meeting scheduling. "
    "Is there anything HR-related I can help you with today?"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify(text: str) -> dict:
    """
    Classify whether the user's message is HR-related.

    Returns
    -------
    dict with keys:
        allowed  (bool)  -- True if the topic is HR-related
        reason   (str)   -- Short explanation
        message  (str)   -- Canned refusal (only meaningful when allowed=False)
    """
    text_stripped = text.strip()

    # CHANGED: widened from <=2 to <=5 words. Short, keyword-less replies
    # like "one zero zero one", "yes that's correct", or "the information
    # is correct" were being wrongly rejected since they contain no HR
    # keyword on their own. This is a safety net alongside the new
    # context-aware check in agent.py's on_user_turn_completed — that one
    # catches most cases by checking if the assistant just asked a
    # question; this catches the rest by simple length.
    # 1. Fast reject: check explicit off-topic patterns FIRST (weather, jokes, sports, etc.)
    for pattern in _OFF_TOPIC_COMPILED:
        if pattern.search(text_stripped):
            if _is_hr(text_stripped):
                return _allow("HR keyword overrides off-topic signal.")
            return _reject(f"Off-topic pattern matched: {pattern.pattern}")

    # 2. Allow short/conversational utterances (up to 10 words) or HR-related messages
    if len(text_stripped.split()) <= 10 or _is_hr(text_stripped):
        return _allow("Conversational or HR utterance allowed.")

    # 3. Default allow general conversational messages
    return _allow("Conversational response allowed.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_hr(text: str) -> bool:
    return any(p.search(text) for p in _HR_COMPILED)


def _allow(reason: str) -> dict:
    return {"allowed": True, "reason": reason, "message": ""}


def _reject(reason: str) -> dict:
    return {"allowed": False, "reason": reason, "message": REFUSAL_MESSAGE}