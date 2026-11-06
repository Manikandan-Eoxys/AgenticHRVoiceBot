"""
hr_voice_agent/tests/test_dtmf_and_startup.py

Unit tests for Phase 7 (DTMF collection & buffer) and Phase 8 (startup validation).
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from telephony.dtmf import DtmfBuffer
from asterisk_main import validate_config


def test_dtmf_buffer_length_trigger():
    completed_ids = []
    buf = DtmfBuffer(expected_length=4, on_id_complete=lambda emp_id: completed_ids.append(emp_id))

    # Feed 3 digits
    buf.on_digit("1")
    buf.on_digit("0")
    buf.on_digit("0")
    assert len(completed_ids) == 0

    # 4th digit triggers completion immediately
    buf.on_digit("8")
    assert len(completed_ids) == 1
    assert completed_ids[0] == "1008"
    assert buf.current_digits == ""


def test_dtmf_buffer_hash_delimiter():
    completed_ids = []
    buf = DtmfBuffer(expected_length=4, delimiter="#", on_id_complete=lambda emp_id: completed_ids.append(emp_id))

    # Feed 2 digits and '#'
    buf.on_digit("4")
    buf.on_digit("2")
    buf.on_digit("#")

    assert len(completed_ids) == 1
    assert completed_ids[0] == "42"


def test_dtmf_buffer_inter_digit_timeout():
    async def _test():
        completed_ids = []
        buf = DtmfBuffer(
            expected_length=4,
            inter_digit_timeout=0.08,
            on_id_complete=lambda emp_id: completed_ids.append(emp_id),
        )

        buf.on_digit("1")
        buf.on_digit("2")

        # Wait for timeout to expire
        await asyncio.sleep(0.12)
        assert len(completed_ids) == 1
        assert completed_ids[0] == "12"

    asyncio.run(_test())


def test_dtmf_wait_for_id():
    async def _test():
        buf = DtmfBuffer(expected_length=4)

        async def _feed_digits():
            await asyncio.sleep(0.04)
            for d in "1015":
                buf.on_digit(d)

        feed_task = asyncio.create_task(_feed_digits())
        captured_id = await buf.wait_for_id(timeout=2.0)
        await feed_task

        assert captured_id == "1015"

    asyncio.run(_test())


def test_asterisk_config_validation():
    # When config is present
    with patch("asterisk_main.ARI_URL", "http://127.0.0.1:8088/ari"), \
         patch("asterisk_main.ARI_USER", "asterisk"), \
         patch("asterisk_main.ARI_PASSWORD", "secret"), \
         patch("asterisk_main.DEEPGRAM_API_KEY", "dg-test-key"):
        assert validate_config() is True

    # When key is missing
    with patch("asterisk_main.DEEPGRAM_API_KEY", ""):
        assert validate_config() is False
