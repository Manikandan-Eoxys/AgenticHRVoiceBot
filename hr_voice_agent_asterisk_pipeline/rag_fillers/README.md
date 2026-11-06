# RAG Filler Audio Clips

This directory stores pre-recorded audio filler clips played to the caller while external RAG policy retrieval is taking place.

## Audio Specifications

To ensure smooth playback and seamless conversation flow, all audio files must meet the following criteria:

- **Format**: WAV container
- **Encoding**: 16-bit Linear PCM (`signed 16-bit little-endian`)
- **Sample Rate**: 24,000 Hz or 48,000 Hz (matching the agent's TTS output rate)
- **Channels**: 1 (Mono)
- **Voice**: Must match the exact voice and persona of the active TTS engine (e.g., ElevenLabs / Deepgram voice).
- **Duration**: Approximately 1.0 to 2.5 seconds per clip.

## Recommended File Names & Transcripts

| Filename | Transcript |
| :--- | :--- |
| `let_me_look_that_up.wav` | "Let me look that up for you." |
| `one_moment.wav` | "One moment while I check that." |
| `let_me_check.wav` | "Let me check our policy on that." |
| `just_a_moment.wav` | "Just a moment, looking into that for you." |
| `checking_records.wav` | "Let me find that information for you." |

Custom clips added to this directory with `.wav` extensions will automatically be loaded by `services.rag_filler.load_clips()`.
