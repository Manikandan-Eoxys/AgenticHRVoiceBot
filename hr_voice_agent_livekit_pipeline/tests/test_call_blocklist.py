"""
tests/test_call_blocklist.py

Unit tests for Call Rejection and Number Blocklist functionality using unittest.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure root directory is in sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from security.call_blocklist import (
    normalize_number,
    _get_number_variations,
    add_blocked_number,
    remove_blocked_number,
    is_number_blocked,
    get_all_blocked_numbers,
    extract_caller_identifiers,
    is_call_blocked,
    BLOCKED_NUMBERS,
)


class MockParticipant:
    def __init__(self, identity: str, name: str = "", attributes: dict = None):
        self.identity = identity
        self.name = name
        self.attributes = attributes or {}


class MockRoom:
    def __init__(self, name: str, remote_participants: dict = None):
        self.name = name
        self.remote_participants = remote_participants or {}


class MockJob:
    def __init__(self, room: MockRoom, participant: MockParticipant = None, metadata: str = ""):
        self.room = room
        self.participant = participant
        self.metadata = metadata


class MockJobRequest:
    def __init__(self, job: MockJob, job_id: str = "job_123"):
        self.id = job_id
        self.job = job
        self.rejected = False
        self.accepted = False

    async def reject(self):
        self.rejected = True

    async def accept(self):
        self.accepted = True


class MockJobContext:
    def __init__(self, room: MockRoom, job: MockJob):
        self.room = room
        self.job = job


class TestCallBlocklist(unittest.TestCase):

    def test_normalize_number(self):
        self.assertEqual(normalize_number("+91 98765-43210"), "+919876543210")
        self.assertEqual(normalize_number("sip:+919876543210"), "+919876543210")
        self.assertEqual(normalize_number("sip_9876543210"), "9876543210")
        self.assertEqual(normalize_number("100@192.168.35.128:5060"), "100")
        self.assertEqual(normalize_number("  (080) 3145-1001  "), "08031451001")
        self.assertEqual(normalize_number(""), "")
        self.assertEqual(normalize_number(None), "")

    def test_number_variations(self):
        vars_in = _get_number_variations("+919876543210")
        self.assertIn("+919876543210", vars_in)
        self.assertIn("919876543210", vars_in)
        self.assertIn("9876543210", vars_in)

        vars_ext = _get_number_variations("101")
        self.assertIn("101", vars_ext)

    def test_add_and_remove_blocked_number(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_json = Path(tf.name)

        try:
            # Initial state: empty
            temp_json.write_text("[]", encoding="utf-8")
            self.assertFalse(is_number_blocked("+919999988888", json_path=temp_json))

            # Add number
            added = add_blocked_number("+919999988888", json_path=temp_json)
            self.assertTrue(added)
            self.assertTrue(is_number_blocked("+919999988888", json_path=temp_json))
            # Should also match without + and without 91
            self.assertTrue(is_number_blocked("9999988888", json_path=temp_json))
            self.assertTrue(is_number_blocked("sip_+919999988888", json_path=temp_json))

            # Remove number
            removed = remove_blocked_number("+919999988888", json_path=temp_json)
            self.assertTrue(removed)
            self.assertFalse(is_number_blocked("+919999988888", json_path=temp_json))
        finally:
            if temp_json.exists():
                temp_json.unlink()

    def test_extract_caller_identifiers(self):
        p = MockParticipant(
            identity="sip_9876543210",
            name="Test Caller",
            attributes={"sip.phoneNumber": "+919876543210", "sip.callerId": "9876543210"}
        )
        ids = extract_caller_identifiers(p)
        self.assertIn("sip_9876543210", ids)
        self.assertIn("Test Caller", ids)
        self.assertIn("+919876543210", ids)
        self.assertIn("9876543210", ids)

    def test_is_call_blocked_with_job_request(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_json = Path(tf.name)

        try:
            temp_json.write_text(json.dumps(["+919876543210", "101"]), encoding="utf-8")

            # 1. Blocked SIP participant
            p_blocked = MockParticipant(
                identity="sip_101",
                attributes={"sip.phoneNumber": "101"}
            )
            room_blocked = MockRoom("test-room", {"p1": p_blocked})
            job_blocked = MockJob(room=room_blocked, participant=p_blocked)
            req_blocked = MockJobRequest(job=job_blocked)

            blocked, matched = is_call_blocked(req_blocked, json_path=temp_json)
            self.assertTrue(blocked)
            self.assertIn(matched, ("sip_101", "101"))

            # 2. Allowed participant
            p_allowed = MockParticipant(
                identity="sip_+918888877777",
                attributes={"sip.phoneNumber": "+918888877777"}
            )
            room_allowed = MockRoom("test-room-2", {"p2": p_allowed})
            job_allowed = MockJob(room=room_allowed, participant=p_allowed)
            req_allowed = MockJobRequest(job=job_allowed)

            blocked, matched = is_call_blocked(req_allowed, json_path=temp_json)
            self.assertFalse(blocked)
            self.assertIsNone(matched)
        finally:
            if temp_json.exists():
                temp_json.unlink()

    def test_is_call_blocked_with_job_context(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_json = Path(tf.name)

        try:
            temp_json.write_text(json.dumps(["+917777766666"]), encoding="utf-8")

            # Blocked remote participant inside JobContext room
            p_blocked = MockParticipant(
                identity="caller-1",
                attributes={"sip.phoneNumber": "+917777766666"}
            )
            room = MockRoom("test-room-3", {"p": p_blocked})
            job = MockJob(room=room)
            ctx = MockJobContext(room=room, job=job)

            blocked, matched = is_call_blocked(ctx, json_path=temp_json)
            self.assertTrue(blocked)
            self.assertEqual(matched, "+917777766666")
        finally:
            if temp_json.exists():
                temp_json.unlink()


if __name__ == "__main__":
    unittest.main()
