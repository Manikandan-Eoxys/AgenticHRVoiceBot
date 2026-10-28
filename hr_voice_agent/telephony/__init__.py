"""
hr_voice_agent/telephony/__init__.py

Telephony abstraction and Asterisk ARI + AudioSocket implementation.
"""

from hr_voice_agent.telephony.base import BaseTelephonySession
from hr_voice_agent.telephony.call_registry import CallRegistry, CallRecord
from hr_voice_agent.telephony.ari_client import AriClient
from hr_voice_agent.telephony.audiosocket import (
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
from hr_voice_agent.telephony.asterisk_session import AsteriskTelephonySession
from hr_voice_agent.telephony.context import CallContext, DuckJobContext, DuckRunContext
from hr_voice_agent.telephony.dtmf import DtmfBuffer

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
