"""
hr_voice_agent/audiosocket_echo.py

Standalone AudioSocket Echo Server
==================================
Implements Asterisk AudioSocket protocol for audio loopback testing.

Wire Format:
  [Type (1 byte)] [Payload Length (2 bytes, big-endian uint16)] [Payload (N bytes)]

Protocol Message Types:
  0x00: Hangup
  0x01: UUID (16-byte binary UUID or 36-byte ASCII string)
  0x02: Silence / Keep-Alive
  0x03: Error (Alternative)
  0x10: Audio (Signed 16-bit Little-Endian Mono Linear PCM, default 8kHz)
  0xFF: Error (AudioSocket Standard)
"""

from __future__ import annotations

import asyncio
import logging
import signal
import struct
import sys
import uuid
from typing import Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("audiosocket_echo")

# Protocol constants
TYPE_HANGUP   = 0x00
TYPE_UUID     = 0x01
TYPE_SILENCE  = 0x02
TYPE_ERROR    = 0x03
TYPE_AUDIO    = 0x10
TYPE_ERROR_FF = 0xFF

HEADER_SIZE = 3  # 1 byte type + 2 bytes big-endian length
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 4040
SAMPLE_RATE = 8000
FRAME_MS = 20
BYTES_PER_SAMPLE = 2
FRAME_SIZE = int(SAMPLE_RATE * (FRAME_MS / 1000.0) * BYTES_PER_SAMPLE)  # 320 bytes


def encode_packet(msg_type: int, payload: bytes = b"") -> bytes:
    """Encodes an AudioSocket packet with a 3-byte header."""
    return struct.pack(">BH", msg_type, len(payload)) + payload


def parse_uuid(payload: bytes) -> Optional[uuid.UUID]:
    """
    Validates and extracts UUID from initial handshake payload.
    Supports both 16-byte RFC 4122 binary representation and 36-byte ASCII string.
    """
    try:
        if len(payload) == 16:
            return uuid.UUID(bytes=payload)
        elif len(payload) == 36:
            return uuid.UUID(payload.decode("ascii"))
        return None
    except Exception:
        return None


async def read_packet(reader: asyncio.StreamReader) -> Tuple[int, bytes]:
    """
    Reads one complete AudioSocket packet from the stream.
    Returns (msg_type, payload).
    Raises asyncio.IncompleteReadError on socket EOF.
    """
    header = await reader.readexactly(HEADER_SIZE)
    msg_type, length = struct.unpack(">BH", header)
    if length > 0:
        payload = await reader.readexactly(length)
    else:
        payload = b""
    return msg_type, payload


class AudioSocketEchoServer:
    """Standalone AudioSocket Echo Server supporting concurrent connections."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self._active_connections = 0
        self._closing = False

    async def handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        self._active_connections += 1
        conn_id = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        logger.info(f"🔌 [{conn_id}] AudioSocket connection opened (active: {self._active_connections})")

        call_uuid: Optional[uuid.UUID] = None
        frames_echoed = 0
        bytes_echoed = 0

        try:
            # 1. First packet MUST be UUID (Type 0x01)
            msg_type, payload = await read_packet(reader)
            if msg_type != TYPE_UUID:
                logger.error(
                    f"❌ [{conn_id}] Invalid initial packet type: 0x{msg_type:02x} (expected 0x{TYPE_UUID:02x})"
                )
                return

            call_uuid = parse_uuid(payload)
            if not call_uuid:
                logger.error(f"❌ [{conn_id}] Invalid UUID payload (length={len(payload)})")
                return

            logger.info(f"🆔 [{conn_id}] Handshake OK: Call UUID = {call_uuid}")

            # 2. Main Audio Echo Loop
            while not self._closing:
                msg_type, payload = await read_packet(reader)

                if msg_type == TYPE_AUDIO:
                    # Echo identical PCM back to client
                    response = encode_packet(TYPE_AUDIO, payload)
                    writer.write(response)
                    await writer.drain()
                    frames_echoed += 1
                    bytes_echoed += len(payload)

                elif msg_type == TYPE_HANGUP:
                    logger.info(f"📴 [{conn_id}] Hangup received from client (UUID: {call_uuid})")
                    break

                elif msg_type in (TYPE_ERROR, TYPE_ERROR_FF):
                    err_code = payload[0] if payload else -1
                    logger.warning(
                        f"⚠️ [{conn_id}] Error frame received: type=0x{msg_type:02x}, code={err_code} (UUID: {call_uuid})"
                    )
                    break

                elif msg_type == TYPE_SILENCE:
                    # Heartbeat / silence: reply with silence packet
                    writer.write(encode_packet(TYPE_SILENCE, b""))
                    await writer.drain()

                else:
                    logger.warning(f"⚠️ [{conn_id}] Unrecognized message type: 0x{msg_type:02x}")

        except asyncio.IncompleteReadError:
            logger.info(f"🔌 [{conn_id}] Stream ended (EOF / client disconnected) (UUID: {call_uuid})")
        except (ConnectionResetError, BrokenPipeError):
            logger.info(f"🔌 [{conn_id}] Connection reset by peer (UUID: {call_uuid})")
        except Exception as e:
            logger.error(f"❌ [{conn_id}] Handler exception: {e} (UUID: {call_uuid})", exc_info=True)
        finally:
            self._active_connections = max(0, self._active_connections - 1)
            logger.info(
                f"🛑 [{conn_id}] Session closed (UUID: {call_uuid}). "
                f"Echoed {frames_echoed} frames ({bytes_echoed} bytes). "
                f"Active connections remaining: {self._active_connections}"
            )
            try:
                if not writer.is_closing():
                    writer.close()
                    await writer.wait_closed()
            except Exception:
                pass

    async def start(self) -> None:
        """Starts the AudioSocket TCP server."""
        self.server = await asyncio.start_server(
            self.handle_connection,
            self.host,
            self.port,
            reuse_address=True,
        )
        addrs = ", ".join(str(sock.getsockname()) for sock in self.server.sockets)
        logger.info(f"🚀 AudioSocket Echo Server listening on {addrs}")

    async def stop(self) -> None:
        """Stops the server gracefully."""
        self._closing = True
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("🛑 AudioSocket Echo Server stopped.")


async def main() -> None:
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            logger.error(f"Invalid port argument: {sys.argv[1]}")
            sys.exit(1)

    server = AudioSocketEchoServer(host=DEFAULT_HOST, port=port)
    await server.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await stop_event.wait()
    await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
