"""
hr_voice_agent/tests/test_fsm_goals_rag_integration.py

Unit tests for Phase 6: VoiceController FSM, GlobalMonitor single-instance behavior,
CallContext isolation, Employee Verification gating, RAG, and Coordinator Transfer.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from controllers.agentVoiceController import VoiceController, ControllerState
from controllers.global_monitor_fsm import (
    start_global_monitor,
    global_monitor_running,
    report_agent_event,
)
from telephony.context import CallContext, DuckJobContext, DuckRunContext
from goals.helpers import require_verified, clean_id
from goals.g01_identity_verification import IdentityVerificationService
from goals.g13_coordinator_transfer import CoordinatorTransferService
from services.rag_retriever_service import needs_rag


def test_global_monitor_single_instance():
    """Verifies that Global Monitor FSM starts once and stays running."""
    # Start monitor
    start_global_monitor()
    assert global_monitor_running() is True

    # Calling start a second time should be idempotent (no duplicate threads)
    start_global_monitor()
    assert global_monitor_running() is True


def test_call_context_isolation():
    """Verifies that CallContext instances are strictly isolated across concurrent calls."""
    ctx1 = CallContext(
        call_id="call-uuid-1",
        caller_number="+919876543210",
        channel_id="chan-001",
    )
    ctx2 = CallContext(
        call_id="call-uuid-2",
        caller_number="+919876543211",
        channel_id="chan-002",
    )

    # Verify initial states
    assert ctx1.employee_verified is False
    assert ctx2.employee_verified is False
    assert ctx1.verified_employee_id is None
    assert ctx2.verified_employee_id is None

    # Mark Call 1 verified for employee 1002
    ctx1.mark_verified("1002", "Jane Doe", {"dept": "Engineering"})

    # Assert Call 1 is verified and Call 2 remains unverified
    assert ctx1.employee_verified is True
    assert ctx1.verified_employee_id == "1002"
    assert ctx1.employee_name == "Jane Doe"

    assert ctx2.employee_verified is False
    assert ctx2.verified_employee_id is None
    assert ctx2.employee_name is None


def test_employee_verification_gating():
    """Verifies require_verified gating logic with CallContext."""
    ctx = CallContext(
        call_id="call-test",
        caller_number="100",
        channel_id="chan-1",
    )

    run_ctx = DuckRunContext(session=ctx)

    # 1. Before verification -> gating error
    err = require_verified(run_ctx)
    assert err is not None
    assert "No employee has been verified yet" in err

    # 2. Lock identity to employee 1005
    ctx.mark_verified("1005", "John Smith")

    # 3. Access with matching ID -> Allowed (None error)
    assert require_verified(run_ctx, "1005") is None

    # 4. Access with different ID -> Rejected
    mismatch_err = require_verified(run_ctx, "1009")
    assert mismatch_err is not None
    assert "verified for employee 1005 only" in mismatch_err


def test_coordinator_transfer_asterisk():
    """Verifies CoordinatorTransferService with an Asterisk telephony session."""
    async def _test():
        mock_session = MagicMock()
        mock_session.transfer_call = AsyncMock(return_value=True)
        mock_session.say = MagicMock()
        mock_session.say.return_value.wait_for_playout = AsyncMock(return_value=True)

        job_ctx = DuckJobContext(room_name="test-room", caller_id="100")
        run_ctx = DuckRunContext(session=mock_session)

        service = CoordinatorTransferService(job_ctx=job_ctx)

        # 1. Test successful transfer
        reply = await service.transfer_to_coordinator(context=run_ctx)
        assert "Coordinator connected" in reply
        mock_session.transfer_call.assert_called_once()

        # 2. Test failed transfer (should not disconnect or crash caller)
        mock_session.transfer_call.reset_mock()
        mock_session.transfer_call.return_value = False

        failed_reply = await service.transfer_to_coordinator(context=run_ctx)
        assert "could not be reached right now" in failed_reply or "unavailable" in failed_reply

    asyncio.run(_test())


def test_rag_intent_detection():
    """Verifies RAG intent trigger helper."""
    # Questions that require policy lookup
    assert needs_rag("What is the maternity leave policy?") is True
    assert needs_rag("What are the company rules for annual leave?") is True
    assert needs_rag("Tell me about company health insurance benefits.") is True

    # Simple transactional / smalltalk questions that do not need RAG
    assert needs_rag("Hello good morning") is False
    assert needs_rag("Yes") is False
    assert needs_rag("My employee ID is 1001") is False


def test_voice_controller_state_machine_transitions():
    """Verifies VoiceController FSM initialization and substate transitions."""
    async def _test():
        job_ctx = DuckJobContext(room_name="room_fsm_test", caller_id="100")
        vc = VoiceController(
            call_type="inbound",
            job_ctx=job_ctx,
            call_metadata={"caller_id": "100"},
        )

        assert vc.current_state == ControllerState.UNINITIALIZED

        # Simulate call lifecycle signals
        vc.on_call_start("room_fsm_test")
        assert vc._call_requested is True

        # Participant join requires active session
        vc._active_session = MagicMock()
        vc.on_participant_joined("sip_100")
        assert vc._active_participant_identity == "sip_100"

        vc.on_agent_substate_changed("speaking")
        assert vc._agent_substate == "speaking"

        vc.on_call_end("room_fsm_test")
        assert vc._shutdown_requested is True

    asyncio.run(_test())
