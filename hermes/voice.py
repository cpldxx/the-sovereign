"""Voice — local speech in and out for talking to the Head Agent.

Speech to text   mlx-whisper (Apple-silicon GPU) with VOICE_STT_MODEL, default whisper-large-v3-turbo: ~1.1 s per
                 utterance on an M1 Max, English and Korean. Elsewhere (or if mlx is missing) Hermes' own
                 faster-whisper on the CPU (VOICE_STT_FALLBACK, default "small": ~2-3 s, weaker Korean; large-v3-turbo
                 takes ~7.5 s on CPU). Audio is decoded with PyAV, so no ffmpeg is needed.
Text to speech   VOICE_TTS=say (default on macOS): the system voices, picked per language — VOICE_SAY_EN (Daniel),
                 VOICE_SAY_KO (Yuna); ~0.7 s. VOICE_TTS=hermes: Hermes' configured TTS provider (Edge, ElevenLabs,
                 OpenAI, Piper, … — tts: in .hermes-home/config.yaml).
Nothing leaves this machine with the defaults.
"""

import io
import json
import os
import platform
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

STT_MODEL = os.getenv("VOICE_STT_MODEL", "mlx-community/whisper-large-v3-turbo")
STT_FALLBACK = os.getenv("VOICE_STT_FALLBACK", "small")
TTS = os.getenv("VOICE_TTS", "say" if platform.system() == "Darwin" else "hermes")
SAY_VOICES = {"en": os.getenv("VOICE_SAY_EN", "Daniel"), "ko": os.getenv("VOICE_SAY_KO", "Yuna")}
MAX_SPOKEN = 1500  # characters read aloud
# Biases recognition toward the wake words (without it, "소버린" came out as "소벌린").
STT_PROMPT = os.getenv("VOICE_STT_PROMPT", "Sovereign, 소버린, 자비스.")

_lock = threading.Lock()  # one transcription at a time (the GPU is shared with the local LLM)
_fallback_model = None


def _mlx():
    try:
        import mlx_whisper  # noqa: PLC0415

        return mlx_whisper
    except ImportError:
        return None


def stt_engine() -> str:
    return f"mlx-whisper ({STT_MODEL})" if _mlx() else f"faster-whisper ({STT_FALLBACK}, cpu)"


def transcribe(audio: bytes, language: str | None = None) -> dict:
    """Audio in any common container (webm/opus from browsers, mp4, wav, ogg) → {text, language, seconds}."""
    from faster_whisper.audio import decode_audio  # noqa: PLC0415 — PyAV-based, no ffmpeg

    started = time.monotonic()
    samples = decode_audio(io.BytesIO(audio), sampling_rate=16000)
    with _lock:
        if mlx := _mlx():
            out = mlx.transcribe(samples, path_or_hf_repo=STT_MODEL, language=language,
                                 condition_on_previous_text=False, initial_prompt=STT_PROMPT or None)
            text, lang = out["text"], out.get("language")
        else:
            global _fallback_model
            if _fallback_model is None:
                from faster_whisper import WhisperModel  # noqa: PLC0415

                _fallback_model = WhisperModel(STT_FALLBACK, device="cpu", compute_type="int8")
            segments, info = _fallback_model.transcribe(samples, language=language, beam_size=1, vad_filter=True,
                                                        initial_prompt=STT_PROMPT or None)
            text, lang = " ".join(s.text.strip() for s in segments), info.language
    return {"text": text.strip(), "language": lang, "seconds": round(time.monotonic() - started, 2)}


def warm_up() -> None:
    """Load the speech model now, so the first question isn't the slow one."""
    import numpy as np  # noqa: PLC0415

    if mlx := _mlx():
        with _lock:
            mlx.transcribe(np.zeros(16000, dtype=np.float32), path_or_hf_repo=STT_MODEL)


# ── Speech out ─────────────────────────────────────────────────────────────

_HANGUL = re.compile(r"[가-힣]")


def language_of(text: str) -> str:
    letters = re.findall(r"[A-Za-z가-힣]", text)
    return "ko" if letters and len(_HANGUL.findall(text)) / len(letters) > 0.3 else "en"


def speakable(text: str) -> str:
    """What to read aloud from an answer: no markdown, code, tables, URLs or uids."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^\s*\|.*\|\s*$", " ", text, flags=re.M)              # table rows
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)                 # links → their text
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\(?`?\b(?:e|f|ep|r|rep|prop|play|sens|acti)_[0-9a-f]{8,}\b`?\)?", " ", text)  # uids
    text = re.sub(r"[`*_#>~]+", " ", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_SPOKEN:
        cut = text[:MAX_SPOKEN]
        text = cut[: max(cut.rfind(". "), cut.rfind("다. "), 200) + 1]
    return text


def speak(text: str) -> tuple[bytes, str]:
    """Text → (audio bytes, media type)."""
    text = speakable(text)
    if not text:
        raise ValueError("nothing to say")
    if TTS == "say":
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "speech.wav"
            source = Path(tmp) / "text.txt"
            source.write_text(text)
            subprocess.run(["say", "-v", SAY_VOICES[language_of(text)], "-f", str(source), "-o", str(out),
                            "--file-format=WAVE", "--data-format=LEI16@22050"], check=True, timeout=60)
            return out.read_bytes(), "audio/wav"
    from tools.tts_tool import text_to_speech_tool  # noqa: PLC0415 — Hermes' provider registry

    with tempfile.TemporaryDirectory() as tmp:
        result = json.loads(text_to_speech_tool(text, output_path=str(Path(tmp) / "speech.mp3")))
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "Hermes TTS failed")
        path = Path(result["file_path"])
        media = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg"}.get(path.suffix, "audio/mpeg")
        return path.read_bytes(), media


def tts_engine() -> str:
    return f"say ({SAY_VOICES['en']} / {SAY_VOICES['ko']})" if TTS == "say" else "hermes"
