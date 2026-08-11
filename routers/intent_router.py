"""
intent_router.py

Simple keyword-based intent classifier.

This runs BEFORE the LLM.

The goal is to reduce the number of tools exposed to
the model.

For example:

User:
"I need leave tomorrow"

↓

Intent.LEAVE

↓

LeaveAgent
"""


import re

from routers.intent import Intent


class IntentRouter:

    def __init__(self):

        self.intent_keywords = {

            ################################################################
            # LEAVE
            ################################################################

            Intent.LEAVE: [

                "leave",
                "vacation",
                "holiday",
                "casual leave",
                "annual leave",
                "sick leave",
                "earned leave",
                "balance",
                "remaining leave",
                "leave balance",
                "apply leave",
                "cancel leave",
                "withdraw leave",
                "leave history",
                "time off",

            ],

            ################################################################
            # EMPLOYEE
            ################################################################

            Intent.EMPLOYEE: [

                "employee",
                "employees",
                "staff",
                "directory",
                "profile",
                "manager",
                "designation",
                "department",
                "phone",
                "email",
                "contact",
                "employee id",
                "search employee",
                "find employee",

            ],

            ################################################################
            # POLICY
            ################################################################

            Intent.POLICY: [

                "policy",
                "policies",
                "attendance",
                "insurance",
                "travel",
                "work from home",
                "wfh",
                "remote work",
                "dress code",
                "maternity",
                "paternity",
                "probation",
                "benefits",
                "eligibility",

            ],

            ################################################################
            # CALENDAR
            ################################################################

            Intent.CALENDAR: [

                "meeting",
                "calendar",
                "appointment",
                "schedule",
                "reschedule",
                "cancel meeting",
                "event",
                "availability",
                "slot",
                "free slot",
                "book meeting",

            ],

            ################################################################
            # GRIEVANCE
            ################################################################

            Intent.GRIEVANCE: [

                "grievance",
                "complaint",
                "harassment",
                "issue",
                "report",
                "escalate",
                "anonymous",
                "workplace issue",

            ],

        }

    # --------------------------------------------------------
    # Normalize Text
    # --------------------------------------------------------

    def _normalize(self, text: str) -> str:

        text = text.lower()

        text = re.sub(r"[^a-z0-9\s]", " ", text)

        text = re.sub(r"\s+", " ", text)

        return text.strip()

    # --------------------------------------------------------
    # Route Intent
    # --------------------------------------------------------

    def route(self, text: str) -> Intent:

        sentence = self._normalize(text)

        scores = {}

        for intent, keywords in self.intent_keywords.items():

            score = 0

            for keyword in keywords:

                if keyword in sentence:

                    score += 1

            scores[intent] = score

        best_intent = max(
            scores,
            key=scores.get,
        )

        if scores[best_intent] == 0:

            return Intent.UNKNOWN

        return best_intent

    # --------------------------------------------------------
    # Debug
    # --------------------------------------------------------

    def explain(self, text: str):

        sentence = self._normalize(text)

        result = {}

        for intent, keywords in self.intent_keywords.items():

            matched = []

            for keyword in keywords:

                if keyword in sentence:

                    matched.append(keyword)

            result[intent.value] = matched

        return result