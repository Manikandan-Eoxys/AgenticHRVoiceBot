"""
conversation_memory.py

Stores conversation context during a LiveKit session.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

INTERRUPTION_WORDS = {

    "stop": "cancel",

    "cancel": "cancel",

    "never mind": "cancel",

    "forget it": "cancel",

    "wait": "pause",

    "hold on": "pause",

    "actually": "modify",
}

@dataclass
class ConversationMemory:

    # Employee
    employee_id: Optional[int] = None
    employee_name: Optional[str] = None

    # Current Intent
    current_intent: Optional[str] = None

    # Pending Action
    pending_action: Optional[str] = None

    # Leave
    leave_type: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None

    # Grievance
    grievance_category: Optional[str] = None
    grievance_description: Optional[str] = None
    anonymous: bool = False

    # Calendar
    meeting_title: Optional[str] = None
    meeting_date: Optional[str] = None
    meeting_time: Optional[str] = None
    duration: Optional[int] = None

    # Generic slots
    slots: Dict[str, Any] = field(default_factory=dict)

    # Conversation history
    history: list = field(default_factory=list)

    # -----------------------------------------------------

    def remember(self, key: str, value: Any):

        self.slots[key] = value

    # -----------------------------------------------------

    def recall(self, key: str):

        return self.slots.get(key)

    # -----------------------------------------------------

    def add_history(self, role: str, message: str):

        self.history.append(
            {
                "role": role,
                "message": message
            }
        )

    # -----------------------------------------------------

    def reset_leave(self):

        self.leave_type = None
        self.from_date = None
        self.to_date = None

    # -----------------------------------------------------

    def reset_grievance(self):

        self.grievance_category = None
        self.grievance_description = None
        self.anonymous = False

    # -----------------------------------------------------

    def reset_calendar(self):

        self.meeting_title = None
        self.meeting_date = None
        self.meeting_time = None
        self.duration = None

    # -----------------------------------------------------

    def reset(self):

        self.employee_id = None
        self.employee_name = None
        self.current_intent = None
        self.pending_action = None

        self.reset_leave()
        self.reset_grievance()
        self.reset_calendar()

        self.slots.clear()
        self.history.clear()
    
    # -----------------------------------------------------

    def interruption_type(self, text: str):

        text = text.lower()

        for word, action in INTERRUPTION_WORDS.items():

            if word in text:

                return action

        return None

    # -----------------------------------------------------
        
    def is_interruption(self, text: str):

        text = text.lower().strip()

        return any(word in text for word in INTERRUPTION_WORDS)
    
    # -----------------------------------------------------

    # def cancel_pending(self):

    #     self.pending_action = None
    #     self.current_intent = None

    # -----------------------------------------------------

    def cancel_pending(self):

        self.pending_action = None

        self.current_intent = None

        self.reset_leave()

        self.reset_grievance()

        self.reset_calendar()
