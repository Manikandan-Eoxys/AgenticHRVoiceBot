"""
test_voice.py

Utility script to test and preview any voice from LiveKit Inference (Cartesia, etc.)
Usage:
    ./.venv/bin/python test_voice.py
    ./.venv/bin/python test_voice.py "Custom text to speak" "<voice_id_or_model>"
"""

import sys
import wave
import asyncio
from dotenv import load_dotenv
load_dotenv()

from livekit.agents import inference, utils


async def synthesize_sample(
    text: str = "Hello! Welcome to HR Assistance. I am Ariana, your HR voice assistant. How can I help you today?",
    model: str = "cartesia/sonic-3",
    voice_id: str = "ec1e269e-9ca0-402f-8a18-58e0e022355a",  # Cartesia "Ariana"
    output_wav: str = "sample_voice.wav",
):
    print(f"🎙️ Provider/Model: {model}")
    print(f"🔊 Voice ID:       {voice_id}")
    print(f"📝 Text:           \"{text}\"")

    async with utils.http_context.open():
        tts = inference.TTS(model=model, voice=voice_id)
        stream = tts.synthesize(text)

        frames = []
        sample_rate = 24000
        channels = 1

        async for frame in stream:
            sample_rate = frame.frame.sample_rate
            channels = frame.frame.num_channels
            frames.append(frame.frame.data.tobytes())

        with wave.open(output_wav, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"".join(frames))

        print(f"✅ Audio generated successfully -> Saved to: {output_wav} ({sample_rate}Hz, 16-bit)\n")


if __name__ == "__main__":
    text_arg = sys.argv[1] if len(sys.argv) > 1 else "Hello! Welcome to HR Assistance. I am Ariana, your HR voice assistant. How can I help you today?"
    voice_arg = sys.argv[2] if len(sys.argv) > 2 else "ec1e269e-9ca0-402f-8a18-58e0e022355a"
    model_arg = sys.argv[3] if len(sys.argv) > 3 else "cartesia/sonic-3"
    asyncio.run(synthesize_sample(text_arg, model_arg, voice_arg))
