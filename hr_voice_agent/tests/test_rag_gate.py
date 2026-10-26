"""
tests/test_rag_gate.py

Unit tests for services/rag_gate.py (needs_rag function).
Covers all 6 evaluation rules, speed requirement (<1ms), and edge cases.
"""

import sys
import time
import unittest
from pathlib import Path

# Ensure hr_voice_agent root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.rag_retriever_service import needs_rag


class TestRagGate(unittest.TestCase):

    # -----------------------------------------------------------------------
    # Rule 1: Fewer than 3 words -> False
    # -----------------------------------------------------------------------
    def test_rule1_fewer_than_three_words(self):
        cases = [
            "",
            "   ",
            "hello",
            "policy",
            "leave policy",
            "wfh please",
            "thank you",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertFalse(needs_rag(text))

    # -----------------------------------------------------------------------
    # Rule 2: Only digits, spaces, or dashes -> False
    # -----------------------------------------------------------------------
    def test_rule2_digits_spaces_and_dashes(self):
        cases = [
            "1 0 0 8",
            "123-456-789",
            "1008 2005 3009",
            "999 - 888 - 777",
            "0 1 2 3 4 5",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertFalse(needs_rag(text))

    # -----------------------------------------------------------------------
    # Rule 3: Plain acknowledgements and goodbyes -> False
    # -----------------------------------------------------------------------
    def test_rule3_plain_acknowledgements_and_goodbyes(self):
        cases = [
            "no thank you",
            "yes that's all",
            "that is all thank you",
            "thank you very much",
            "okay thanks goodbye",
            "have a great day",
            "nothing else thank you",
            "bye have a nice day",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertFalse(needs_rag(text))

    # -----------------------------------------------------------------------
    # Rule 4: Caller's own data or actions (handled by DB tools) -> False
    # -----------------------------------------------------------------------
    def test_rule4_caller_own_data_and_actions(self):
        cases = [
            "what is my leave balance",
            "please apply for leave tomorrow",
            "i want to apply for a leave",
            "check the status of my request",
            "please submit my leave request",
            "can you show my payslip for this month",
            "check my attendance for today",
            "how many leaves do I have left",
            "cancel my pending leave request",
            "i want to regularize my attendance",
            "what is my salary this month",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertFalse(needs_rag(text))

    # -----------------------------------------------------------------------
    # Rule 5: Policy or general-knowledge intent keywords -> True
    # -----------------------------------------------------------------------
    def test_rule5_policy_and_general_knowledge_intent(self):
        cases = [
            "what is the leave policy",
            "how many casual leaves do I get",
            "what are the rules for probation",
            "am I allowed to work from home on Fridays",
            "what is the notice period for resignation",
            "is Monday a public holiday",
            "are employees eligible for health insurance",
            "what is the dress code in the office",
            "how do I claim travel reimbursement",
            "what are the maternity leave benefits",
            "how many days of notice do I need to give",
            "what are the standard working hours",
            "what is the company policy on wfh",
            "where can I read the employee handbook",
            "what is the entitlement for paternity leave",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertTrue(needs_rag(text))

    # -----------------------------------------------------------------------
    # Specific Required Contrast:
    # "how many casual leaves do I get" (True) vs "what is my leave balance" (False)
    # -----------------------------------------------------------------------
    def test_contrast_leaves_do_i_get_vs_leave_balance(self):
        self.assertTrue(needs_rag("how many casual leaves do I get"))
        self.assertFalse(needs_rag("what is my leave balance"))

    # -----------------------------------------------------------------------
    # Rule 6: Fallback for unrelated text without policy keywords -> False
    # -----------------------------------------------------------------------
    def test_rule6_unrelated_queries(self):
        cases = [
            "can you tell me a funny joke",
            "what is the capital of France",
            "play some relaxing background music",
            "what time is it in Tokyo right now",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertFalse(needs_rag(text))

    # -----------------------------------------------------------------------
    # Edge Cases & Mixed Case
    # -----------------------------------------------------------------------
    def test_mixed_case_and_edge_cases(self):
        self.assertTrue(needs_rag("HOW MANY CASUAL LEAVES DO I GET"))
        self.assertFalse(needs_rag("WHAT IS MY LEAVE BALANCE"))
        self.assertTrue(needs_rag("pLeAsE tElL mE aBoUt ThE dReSs CoDe"))
        self.assertFalse(needs_rag("NO THANK YOU VERY MUCH"))
        self.assertFalse(needs_rag(None))  # type: ignore
        self.assertFalse(needs_rag(12345))  # type: ignore

    # -----------------------------------------------------------------------
    # Latency: Must run in under a millisecond (< 1.0 ms)
    # -----------------------------------------------------------------------
    def test_performance_sub_millisecond(self):
        sample = "what is the company policy regarding working from home on fridays"
        start = time.perf_counter()
        for _ in range(1000):
            needs_rag(sample)
        elapsed_sec = time.perf_counter() - start
        avg_ms = (elapsed_sec / 1000) * 1000
        # Average must be well under 1.0 millisecond (typically < 0.05 ms)
        self.assertLess(avg_ms, 1.0)


if __name__ == "__main__":
    unittest.main()
