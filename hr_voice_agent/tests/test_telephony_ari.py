"""
hr_voice_agent/tests/test_telephony_ari.py

Unit and mock tests for Phase 2: ARI client, CallRegistry, and AsteriskTelephonySession.
"""

import asyncio
import os
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from telephony.base import BaseTelephonySession
from telephony.call_registry import CallRegistry
from telephony.ari_client import AriClient
from telephony.audiosocket import (
    encode_packet,
    encode_audio_frame,
    encode_hangup,
    TYPE_AUDIO,
    TYPE_HANGUP,
    TYPE_UUID,
)
from telephony.asterisk_session import AsteriskTelephonySession


def test_call_registry_mappings():
    async def _test():
        registry = CallRegistry()

        call_uuid = str(uuid.uuid4())
        channel_id = "chan_101"
        caller_num = "100"

        # 1. Register pending call
        rec = await registry.register_pending(call_uuid, channel_id, caller_num)
        assert rec.call_uuid == call_uuid
        assert rec.channel_id == channel_id
        assert rec.caller_number == caller_num

        # 2. Resolve AudioSocket connection
        resolved = await registry.resolve_audiosocket(call_uuid)
        assert resolved is not None
        assert resolved.channel_id == channel_id

        # 3. Associate bridge and external media
        await registry.associate_bridge_and_media(channel_id, "bridge_1", "ext_chan_1")
        rec_updated = await registry.get_record_by_channel(channel_id)
        assert rec_updated.bridge_id == "bridge_1"
        assert rec_updated.external_channel_id == "ext_chan_1"

        # 4. Remove call
        removed = await registry.remove_call(channel_id)
        assert removed.channel_id == channel_id
        assert await registry.get_record_by_channel(channel_id) is None
        assert await registry.resolve_audiosocket(call_uuid) is None

    asyncio.run(_test())


def test_call_registry_concurrency():
    async def _test():
        registry = CallRegistry()

        # Simulate 10 concurrent calls
        calls = [
            (str(uuid.uuid4()), f"chan_{i}", f"caller_{100+i}")
            for i in range(10)
        ]

        async def reg_call(u, c, num):
            await registry.register_pending(u, c, num)

        await asyncio.gather(*(reg_call(u, c, num) for u, c, num in calls))

        # Check all are individually resolvable
        for u, c, num in calls:
            rec = await registry.resolve_audiosocket(u)
            assert rec is not None
            assert rec.channel_id == c
            assert rec.caller_number == num

        # Remove half
        for u, c, _ in calls[:5]:
            await registry.remove_call(c)

        for u, c, _ in calls[:5]:
            assert await registry.resolve_audiosocket(u) is None

        for u, c, _ in calls[5:]:
            assert await registry.resolve_audiosocket(u) is not None

    asyncio.run(_test())


def test_ari_client_rest_operations():
    async def _test():
        client = AriClient(
            base_url="http://test-asterisk:8088/ari",
            username="test",
            password="pwd",
        )
        await client.connect()

        # Mock http responses
        with patch.object(client._http_session, "post") as mock_post, \
             patch.object(client._http_session, "delete") as mock_delete:

            # 1. Answer channel
            mock_resp = AsyncMock()
            mock_resp.status = 204
            mock_post.return_value.__aenter__.return_value = mock_resp

            ans_ok = await client.answer_channel("chan_123")
            assert ans_ok is True
            mock_post.assert_called_with("http://test-asterisk:8088/ari/channels/chan_123/answer")

            # 2. Create mixing bridge
            mock_bridge_resp = AsyncMock()
            mock_bridge_resp.status = 200
            mock_bridge_resp.json = AsyncMock(return_value={"id": "b_123"})
            mock_post.return_value.__aenter__.return_value = mock_bridge_resp

            bridge_id = await client.create_mixing_bridge(name="test_bridge")
            assert bridge_id == "b_123"

            # 3. Add channel to bridge
            mock_add_resp = AsyncMock()
            mock_add_resp.status = 204
            mock_post.return_value.__aenter__.return_value = mock_add_resp

            add_ok = await client.add_channel_to_bridge("b_123", ["chan_1", "ext_1"])
            assert add_ok is True

            # 4. Hangup channel
            mock_del_resp = AsyncMock()
            mock_del_resp.status = 204
            mock_delete.return_value.__aenter__.return_value = mock_del_resp

            hangup_ok = await client.hangup_channel("chan_123")
            assert hangup_ok is True

            # 5. Destroy bridge
            destroy_ok = await client.destroy_bridge("b_123")
            assert destroy_ok is True

        await client.close()

    asyncio.run(_test())


def test_asterisk_telephony_session_lifecycle():
    async def _test():
        reader = asyncio.StreamReader()
        writer_transport = MagicMock()
        writer_protocol = MagicMock()
        writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, asyncio.get_event_loop())

        mock_ari = MagicMock(spec=AriClient)
        mock_ari.hangup_channel = AsyncMock(return_value=True)
        mock_ari.destroy_bridge = AsyncMock(return_value=True)
        mock_ari.remove_channel_from_bridge = AsyncMock(return_value=True)
        mock_ari.add_channel_to_bridge = AsyncMock(return_value=True)
        mock_ari.transfer_channel = AsyncMock(return_value=True)
        mock_ari.send_dtmf = AsyncMock(return_value=True)

        session = AsteriskTelephonySession(
            call_id="call-uuid-123",
            channel_id="chan-456",
            caller_number="9876543210",
            reader=reader,
            writer=writer,
            ari_client=mock_ari,
            bridge_id="bridge-789",
            external_channel_id="ext-012",
        )

        assert session.is_active is True
        assert session.call_id == "call-uuid-123"
        assert session.caller_number == "9876543210"

        # Test DTMF callback registration & dispatch
        received_digits = []
        session.on_dtmf(lambda d: received_digits.append(d))
        session.dispatch_dtmf("5")
        assert received_digits == ["5"]

        # Test transfer while call is active
        transfer_ok = await session.transfer_call("1001")
        assert transfer_ok is True
        mock_ari.transfer_channel.assert_called_with("chan-456", "PJSIP/1001")

        # Feed audio frame into reader, followed by hangup
        pcm_frame = b"\x11\x22" * 160
        reader.feed_data(encode_audio_frame(pcm_frame))
        reader.feed_data(encode_hangup())

        frames_collected = []
        async for frame in session.audio_frames():
            frames_collected.append(frame)

        assert len(frames_collected) == 1
        assert frames_collected[0] == pcm_frame

        # Test hangup and idempotent teardown
        await session.hangup()
        assert session.is_active is False
        mock_ari.hangup_channel.assert_any_call("chan-456")
        mock_ari.hangup_channel.assert_any_call("ext-012")
        mock_ari.destroy_bridge.assert_called_with("bridge-789")

        # Calling hangup a second time should be idempotent (no extra calls)
        call_count_before = mock_ari.hangup_channel.call_count
        await session.hangup()
        assert mock_ari.hangup_channel.call_count == call_count_before

    asyncio.run(_test())
