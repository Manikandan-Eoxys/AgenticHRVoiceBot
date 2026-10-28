"""
hr_voice_agent/tests/test_audiosocket_echo.py

Unit and integration tests for AudioSocket Echo Server.
"""

import asyncio
import os
import sys
import struct
import uuid
import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from audiosocket_echo import (
    AudioSocketEchoServer,
    encode_packet,
    parse_uuid,
    read_packet,
    TYPE_HANGUP,
    TYPE_UUID,
    TYPE_SILENCE,
    TYPE_ERROR,
    TYPE_AUDIO,
    TYPE_ERROR_FF,
    HEADER_SIZE,
)


def test_encode_packet():
    # Audio frame with 320 bytes payload
    payload = b"\x01\x02" * 160
    packet = encode_packet(TYPE_AUDIO, payload)
    assert len(packet) == HEADER_SIZE + len(payload)
    msg_type, length = struct.unpack(">BH", packet[:HEADER_SIZE])
    assert msg_type == TYPE_AUDIO
    assert length == 320
    assert packet[HEADER_SIZE:] == payload

    # Empty payload packet (e.g. hangup)
    hangup_pkt = encode_packet(TYPE_HANGUP, b"")
    assert len(hangup_pkt) == HEADER_SIZE
    msg_type, length = struct.unpack(">BH", hangup_pkt)
    assert msg_type == TYPE_HANGUP
    assert length == 0


def test_parse_uuid():
    # 1. 16-byte binary UUID
    test_uuid = uuid.uuid4()
    parsed = parse_uuid(test_uuid.bytes)
    assert parsed == test_uuid

    # 2. 36-byte ASCII string UUID
    parsed_str = parse_uuid(str(test_uuid).encode("ascii"))
    assert parsed_str == test_uuid

    # 3. Invalid lengths and payloads
    assert parse_uuid(b"too_short") is None
    assert parse_uuid(b"x" * 16) is not None  # Any 16 bytes is valid RFC4122 binary representation
    assert parse_uuid(b"x" * 36) is None      # Not valid hex UUID string
    assert parse_uuid(b"") is None


def test_read_packet():
    async def _test():
        reader = asyncio.StreamReader()
        reader.feed_data(encode_packet(TYPE_AUDIO, b"pcm_sample_data"))
        reader.feed_eof()

        msg_type, payload = await read_packet(reader)
        assert msg_type == TYPE_AUDIO
        assert payload == b"pcm_sample_data"

        # Test incomplete read
        incomplete_reader = asyncio.StreamReader()
        incomplete_reader.feed_data(b"\x10\x00")  # Only 2 bytes, needs 3
        incomplete_reader.feed_eof()

        with pytest.raises(asyncio.IncompleteReadError):
            await read_packet(incomplete_reader)

    asyncio.run(_test())


def test_echo_server_full_call():
    async def _test():
        server = AudioSocketEchoServer(host="127.0.0.1", port=0)
        await server.start()
        assert server.server is not None
        port = server.server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)

            # 1. Handshake with 16-byte UUID
            call_id = uuid.uuid4()
            writer.write(encode_packet(TYPE_UUID, call_id.bytes))
            await writer.drain()

            # 2. Send 5 audio frames and verify echoed response
            for i in range(5):
                pcm = bytes([i % 256] * 320)
                writer.write(encode_packet(TYPE_AUDIO, pcm))
                await writer.drain()

                resp_type, resp_payload = await read_packet(reader)
                assert resp_type == TYPE_AUDIO
                assert resp_payload == pcm

            # 3. Send Hangup
            writer.write(encode_packet(TYPE_HANGUP, b""))
            await writer.drain()

            # Wait for server to close connection
            data = await reader.read(100)
            assert data == b""  # Connection closed (EOF)
            writer.close()
            await writer.wait_closed()

        finally:
            await server.stop()

    asyncio.run(_test())


def test_echo_server_invalid_handshake():
    async def _test():
        server = AudioSocketEchoServer(host="127.0.0.1", port=0)
        await server.start()
        port = server.server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            # Send audio directly without initial UUID
            writer.write(encode_packet(TYPE_AUDIO, b"invalid_start"))
            await writer.drain()

            # Server should disconnect client immediately
            data = await reader.read(100)
            assert data == b""
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

    asyncio.run(_test())


def test_echo_server_error_packet():
    async def _test():
        server = AudioSocketEchoServer(host="127.0.0.1", port=0)
        await server.start()
        port = server.server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            call_id = uuid.uuid4()
            writer.write(encode_packet(TYPE_UUID, call_id.bytes))
            await writer.drain()

            # Send Error (0xFF)
            writer.write(encode_packet(TYPE_ERROR_FF, b"\x01"))
            await writer.drain()

            data = await reader.read(100)
            assert data == b""
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

    asyncio.run(_test())


def test_echo_server_concurrency():
    async def _test():
        server = AudioSocketEchoServer(host="127.0.0.1", port=0)
        await server.start()
        port = server.server.sockets[0].getsockname()[1]

        async def run_client(client_id: int):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            call_id = uuid.uuid4()
            writer.write(encode_packet(TYPE_UUID, call_id.bytes))
            await writer.drain()

            for f in range(3):
                pcm = bytes([client_id] * 160 + [f] * 160)
                writer.write(encode_packet(TYPE_AUDIO, pcm))
                await writer.drain()

                resp_type, resp_payload = await read_packet(reader)
                assert resp_type == TYPE_AUDIO
                assert resp_payload == pcm

            writer.write(encode_packet(TYPE_HANGUP, b""))
            await writer.drain()
            await reader.read(100)
            writer.close()
            await writer.wait_closed()

        try:
            # Run 5 concurrent clients
            await asyncio.gather(*(run_client(i) for i in range(5)))
        finally:
            await server.stop()

    asyncio.run(_test())
