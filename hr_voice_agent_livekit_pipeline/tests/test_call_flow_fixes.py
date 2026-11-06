"""
tests/test_call_flow_fixes.py

Unit tests verifying the 5 call flow problem fixes:
1. VoiceController FSM loop runs and pumps run_state_machine()
2. on_call_end cleanly transitions ENDING -> READY and resets state
3. Inbound SIP pre-existing participant detection
4. Recorder safety during disconnect before session start
5. Startup checks and Ollama priming run once without redundant repeats
"""

import os
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from controllers.agentVoiceController import VoiceController, ControllerState


class TestCallFlowFixes(unittest.TestCase):

    def test_voice_controller_single_instance_setup_call(self):
        """Problem 5 & Architecture: Ensure setup_call configures the single VoiceController instance."""
        vc = VoiceController()
        vc.setup_call(call_type="leave_verification")
        self.assertEqual(vc.call_type, "leave_verification")
        # services are keyed by meta.id e.g. 'g12_leave_verification_outbound'
        self.assertTrue(
            any("leave_verification" in k for k in vc.services),
            f"Expected leave_verification goal in services, got: {list(vc.services.keys())}"
        )
        tools = vc.get_all_tools()
        self.assertTrue(len(tools) > 0)

        # Reconfigure same instance for inbound
        vc.setup_call(call_type="inbound")
        self.assertEqual(vc.call_type, "inbound")
        self.assertTrue(
            any("leave" in k or "identity" in k for k in vc.services),
            f"Expected inbound goal in services, got: {list(vc.services.keys())}"
        )

    def test_on_call_end_transitions_to_ready(self):
        """Problem 2: Ensure on_call_end sets _shutdown_requested, and FSM ticks transition SESSION_ACTIVE -> ENDING -> READY."""
        async def _test():
            vc = VoiceController()
            vc.current_state = ControllerState.SESSION_ACTIVE
            vc._shutdown_requested = False
            vc._active_participant_identity = "sip_12345"
            vc.stt_status = "listening"

            vc.on_call_end("test-room")
            self.assertTrue(vc._shutdown_requested)

            # Tick 1: SESSION_ACTIVE -> ENDING
            await vc.run_state_machine()
            self.assertEqual(vc.current_state, ControllerState.ENDING)

            # Tick 2: ENDING -> READY
            await vc.run_state_machine()
            self.assertEqual(vc.current_state, ControllerState.READY)
            self.assertEqual(vc._active_participant_identity, "")
            self.assertEqual(vc.stt_status, "idle")
            self.assertIsNone(vc._active_session)

        asyncio.run(_test())

    def test_ensure_fsm_running_creates_voice_controller_task(self):
        """Ensure ensure_fsm_running launches VoiceControllerFSM execution task."""
        async def _test():
            vc = VoiceController()
            vc.current_state = ControllerState.READY
            vc.ensure_fsm_running()

            self.assertIsNotNone(vc._fsm_task)
            self.assertFalse(vc._fsm_task.done())

            # Cancel task for cleanup
            vc._fsm_task.cancel()
            await asyncio.sleep(0.05)
            self.assertTrue(vc._fsm_task.done())

        asyncio.run(_test())

    def test_connecting_advances_only_on_participant(self):
        """CONNECTING state does NOT advance purely on attach_session.
        It advances to SESSION_ACTIVE only when a participant identity is attached."""
        async def _test():
            vc = VoiceController()
            vc.current_state = ControllerState.CONNECTING
            mock_session = MagicMock()
            vc.attach_session(mock_session)

            # Without participant identity, stays in CONNECTING
            await vc.run_state_machine()
            self.assertEqual(vc.current_state, ControllerState.CONNECTING)

            # Once participant joins, advances to SESSION_ACTIVE
            vc.on_participant_joined("sip_caller_42")
            await vc.run_state_machine()
            self.assertEqual(vc.current_state, ControllerState.SESSION_ACTIVE)
            self.assertEqual(vc._active_participant_identity, "sip_caller_42")

        asyncio.run(_test())

    def test_watchdog_latches_fire_once_and_reset_on_transition(self):
        """Fix 5: Watchdog warnings latch into 'slow' / 'stalled' and do not reset on every tick.
        State transition in run_state_machine() resets latches to 'idle'."""
        async def _test():
            vc = VoiceController()
            vc.current_state = ControllerState.THINKING
            vc.llm_timeout = 0.01

            # Tick 1: transitions from idle to generating
            await vc.run_state_machine()
            self.assertEqual(vc.llm_status, "generating")

            # Wait past timeout
            await asyncio.sleep(0.02)

            with patch("controllers.agentVoiceController.logger.warning") as mock_warn:
                # Tick 2: timeout exceeded, latches to 'slow' and logs warning
                await vc.run_state_machine()
                self.assertEqual(vc.llm_status, "slow")
                mock_warn.assert_called_once()

                # Tick 3: subsequent tick should NOT re-log warning
                mock_warn.reset_mock()
                await vc.run_state_machine()
                self.assertEqual(vc.llm_status, "slow")
                mock_warn.assert_not_called()

            # State transition to SPEAKING resets all engine statuses to 'idle'
            vc._agent_substate = "SPEAKING"
            await vc.run_state_machine()
            self.assertEqual(vc.current_state, ControllerState.SPEAKING)
            self.assertEqual(vc.llm_status, "idle")
            self.assertEqual(vc.tts_status, "idle")
            self.assertEqual(vc.stt_status, "idle")

            # Tick in SPEAKING: advances idle -> synthesizing
            vc.tts_timeout = 0.01
            await vc.run_state_machine()
            self.assertEqual(vc.tts_status, "synthesizing")

            await asyncio.sleep(0.02)
            with patch("controllers.agentVoiceController.logger.warning") as mock_warn:
                await vc.run_state_machine()
                self.assertEqual(vc.tts_status, "stalled")
                mock_warn.assert_called_once()

                mock_warn.reset_mock()
                await vc.run_state_machine()
                self.assertEqual(vc.tts_status, "stalled")
                mock_warn.assert_not_called()

        asyncio.run(_test())

    def test_blocklist_detects_preexisting_participants(self):
        """Blocklist check on JobContext inspects pre-existing room remote_participants."""
        from security.call_blocklist import is_call_blocked

        mock_participant = MagicMock()
        mock_participant.identity = "+919360827096"  # In-code blocked number
        mock_participant.name = "Blocked Caller"
        mock_participant.attributes = {}

        mock_ctx = MagicMock()
        mock_ctx.room.remote_participants = {"p1": mock_participant}
        mock_ctx.job = None

        blocked, matched = is_call_blocked(mock_ctx)
        self.assertTrue(blocked)
        self.assertEqual(matched, "+919360827096")

    def test_ollama_priming_parent_process_guard(self):
        """Problem 5: prewarm_resources_once skips prewarm when running in spawned job process."""
        vc = VoiceController()
        calls = []
        original_prime = vc._prime_ollama_model
        vc._prime_ollama_model = lambda: calls.append("called")

        # Case 1: proc.name != "MainProcess"
        mock_proc = MagicMock()
        mock_proc.name = "JobProcess-1"
        with patch("multiprocessing.current_process", return_value=mock_proc):
            vc.prewarm_resources_once()
        self.assertEqual(calls, [], "_prime_ollama_model should NOT be called when proc.name != 'MainProcess'")

        # Case 2: parent_process() is not None
        mock_parent = MagicMock()
        with patch("multiprocessing.parent_process", return_value=mock_parent):
            vc.prewarm_resources_once()
        self.assertEqual(calls, [], "_prime_ollama_model should NOT be called when parent_process is not None")

        vc._prime_ollama_model = original_prime


if __name__ == "__main__":
    unittest.main()
