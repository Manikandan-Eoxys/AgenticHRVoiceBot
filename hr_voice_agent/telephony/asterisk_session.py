"""
hr_voice_agent/telephony/asterisk_session.py

Asterisk Telephony Session Implementation
=========================================
Implements BaseTelephonySession backed by Asterisk ARI and AudioSocket.
Manages bidirectional audio streaming, pacing, barge-in, DTMF, transfer, and call teardown.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from hr_voice_agent.telephony.base import BaseTelephonySession
from hr_voice_agent.telephony.ari_client import AriClient
from hr_voice_agent.telephony.audiosocket import (
    AudioSocketWriterQueue,
    read_packet,
    TYPE_AUDIO,
    TYPE_HANGUP,
    TYPE_SILENCE,
    TYPE_ERROR,
    TYPE_ERROR_FF,
)

logger = logging.getLogger("telephony.asterisk_session")


class AsteriskTelephonySession(BaseTelephonySession):
    """
    Asterisk Telephony Session connecting ARI Call Control and AudioSocket Audio I/O.
    """

    def __init__(
        self,
        call_id: str,
        channel_id: str,
        caller_number: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        ari_client: AriClient,
        bridge_id: Optional[str] = None,
        external_channel_id: Optional[str] = None,
        sample_rate: int = 8000,
    ) -> None:
        super().__init__(call_id=call_id, caller_number=caller_number)
        self.channel_id = channel_id
        self.reader = reader
        self.writer = writer
        self.ari_client = ari_client
        self.bridge_id = bridge_id
        self.external_channel_id = external_channel_id
        self.sample_rate = sample_rate

        self._pacer = AudioSocketWriterQueue(
            writer=self.writer,
            sample_rate=self.sample_rate,
        )
        self._pacer.start()
        self._cleanup_lock = asyncio.Lock()
        self._is_active = True
        self._cleaned_up = False

    async def audio_frames(self) -> AsyncIterator[bytes]:
        """
        Yields incoming mono 16-bit linear PCM frames (0x10) from AudioSocket.
        Exits when caller hangs up, disconnects, or sends an error packet.
        """
        logger.debug(f"🎧 [Call {self.call_id}] Inbound audio frame streaming started.")
        while self._is_active:
            try:
                msg_type, payload = await read_packet(self.reader)

                if msg_type == TYPE_AUDIO:
                    if payload:
                        yield payload

                elif msg_type == TYPE_HANGUP:
                    logger.info(f"📴 [Call {self.call_id}] Hangup received from AudioSocket.")
                    break

                elif msg_type in (TYPE_ERROR, TYPE_ERROR_FF):
                    logger.warning(f"⚠️ [Call {self.call_id}] Error frame received (type: 0x{msg_type:02x})")
                    break

                elif msg_type == TYPE_SILENCE:
                    continue

                else:
                    logger.debug(f"ℹ️ [Call {self.call_id}] Unhandled message type: 0x{msg_type:02x}")

            except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
                logger.info(f"🔌 [Call {self.call_id}] AudioSocket connection closed.")
                break
            except Exception as e:
                logger.error(f"❌ [Call {self.call_id}] Inbound audio reader error: {e}")
                break

        self._is_active = False

    async def send_audio(self, pcm: bytes) -> None:
        """Enqueues outbound PCM audio to be paced and streamed to Asterisk."""
        if not self._is_active:
            return
        self._pacer.enqueue_pcm(pcm)

    def interrupt_speech(self) -> None:
        """Barge-in: instantly purges queued outbound audio."""
        self._pacer.interrupt()

    async def wait_for_playout(self, timeout: Optional[float] = None) -> bool:
        """Waits until all buffered outbound audio frames have finished playback."""
        return await self._pacer.wait_for_playout(timeout=timeout)

    async def send_dtmf(self, digits: str) -> bool:
        """Sends DTMF tones on the caller channel via ARI."""
        if not self._is_active:
            return False
        return await self.ari_client.send_dtmf(self.channel_id, digits)

    async def transfer_call(self, target: str) -> bool:
        """
        Transfers the caller channel to the destination endpoint.
        Format can be 'PJSIP/<target>' or numeric string.
        """
        if not self._is_active:
            return False

        endpoint = target if "/" in target else f"PJSIP/{target}"
        logger.info(f"🔀 [Call {self.call_id}] Transferring caller channel {self.channel_id} to {endpoint}...")

        # Purge audio
        self.interrupt_speech()

        # Remove caller from mixing bridge so they hear ringing from new channel
        if self.bridge_id:
            await self.ari_client.remove_channel_from_bridge(self.bridge_id, self.channel_id)

        # Redirect caller channel in ARI
        success = await self.ari_client.transfer_channel(self.channel_id, endpoint)
        if not success:
            logger.error(f"❌ [Call {self.call_id}] Transfer failed. Re-adding to bridge.")
            if self.bridge_id:
                await self.ari_client.add_channel_to_bridge(self.bridge_id, self.channel_id)
        return success

    async def hangup(self) -> None:
        """
        Idempotent teardown of AudioSocket connection, channels, and bridge.
        """
        async with self._cleanup_lock:
            if self._cleaned_up:
                return
            self._cleaned_up = True
            self._is_active = False
            logger.info(f"🛑 [Call {self.call_id}] Tearing down telephony session...")

            # 1. Stop audio pacer
            await self._pacer.close()

            # 2. Hang up externalMedia channel
            if self.external_channel_id:
                await self.ari_client.hangup_channel(self.external_channel_id)

            # 3. Hang up caller channel
            if self.channel_id:
                await self.ari_client.hangup_channel(self.channel_id)

            # 4. Destroy mixing bridge
            if self.bridge_id:
                await self.ari_client.destroy_bridge(self.bridge_id)

            logger.info(f"✅ [Call {self.call_id}] Telephony session teardown complete.")
