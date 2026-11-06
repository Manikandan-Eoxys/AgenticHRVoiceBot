"""
hr_voice_agent_asterisk_pipeline/telephony/__init__.py

Telephony abstraction and Asterisk ARI + AudioSocket implementation.
"""

from .base import BaseTelephonySession
from .call_registry import CallRegistry, CallRecord
from .ari_client import AriClient
from .audiosocket import (
    AudioSocketWriterQueue,
    encode_packet,
    encode_audio_frame,
    encode_hangup,
    read_packet,
    parse_uuid,
    TYPE_HANGUP,
    TYPE_UUID,
    TYPE_SILENCE,
    TYPE_ERROR,
    TYPE_AUDIO,
    TYPE_ERROR_FF,
    HEADER_SIZE,
    FRAME_DURATION_MS,
    BYTES_PER_SAMPLE,
)
from .asterisk_session import AsteriskTelephonySession
from .context import CallContext, DuckJobContext, DuckRunContext
from .dtmf import DtmfBuffer

__all__ = [
    "BaseTelephonySession",
    "CallRegistry",
    "CallRecord",
    "CallContext",
    "DuckJobContext",
    "DuckRunContext",
    "DtmfBuffer",
    "AriClient",
    "AudioSocketWriterQueue",
    "AsteriskTelephonySession",
    "encode_packet",
    "encode_audio_frame",
    "encode_hangup",
    "read_packet",
    "parse_uuid",
    "TYPE_HANGUP",
    "TYPE_UUID",
    "TYPE_SILENCE",
    "TYPE_ERROR",
    "TYPE_AUDIO",
    "TYPE_ERROR_FF",
    "HEADER_SIZE",
    "FRAME_DURATION_MS",
    "BYTES_PER_SAMPLE",
]
