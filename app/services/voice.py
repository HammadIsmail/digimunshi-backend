import os
import uuid
import logging
from typing import Optional
import httpx
import subprocess
import imageio_ffmpeg
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AUDIO_DIR = os.path.join(BASE_DIR, "static", "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

MIME_MAP = {
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".3gp": "audio/3gpp",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
}


def convert_to_wav(audio_bytes: bytes) -> bytes:
    """Convert any incoming audio bytes (M4A, AAC, WebM, MP3) to 16kHz mono WAV PCM for Uplift STT."""
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        proc = subprocess.Popen(
            [ffmpeg_exe, "-y", "-i", "pipe:0", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        wav_bytes, err = proc.communicate(input=audio_bytes, timeout=10.0)
        if proc.returncode == 0 and len(wav_bytes) > 44:
            return wav_bytes
        logger.warning(f"ffmpeg conversion returned non-zero code {proc.returncode}: {err[:200]}")
    except Exception as e:
        logger.warning(f"Audio conversion failed, using original bytes: {e}")
    return audio_bytes


from groq import AsyncGroq


async def transcribe_with_groq_whisper(audio_bytes: bytes, filename: str = "recording.m4a") -> dict:
    """
    Transcribe audio using Groq Whisper Large v3 on LPU.
    Ultra-fast (~200ms) with state-of-the-art Urdu accuracy.
    """
    client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    
    # Ensure correct extension recognized by Whisper
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".m4a", ".wav", ".mp3", ".mp4", ".mpeg", ".mpga", ".webm", ".ogg"]:
        filename = "recording.m4a"

    res = await client.audio.transcriptions.create(
        file=(filename, audio_bytes),
        model="whisper-large-v3",
        language="ur",
        prompt="digiMunshi dukandar khata udhaar Urdu: Ali, Aslam, Khan, rupay, sau, hazar, haan, nahi, theek hai, likh do, clear kar do",
        response_format="json",
        temperature=0.0
    )
    transcript = res.text.strip()
    
    # Filter out common silent hallucination tokens
    if transcript in ["موسیقی", "موسیقی۔", "Music", "[Music]", "..."]:
        transcript = ""

    safe_text = transcript.encode("ascii", "backslashreplace").decode("ascii")
    logger.info(f"Groq Whisper-large-v3 STT [{filename}, {len(audio_bytes)} bytes]: '{safe_text}'")
    return {
        "text": transcript,
        "confidence": 0.98 if transcript else 0.0
    }


async def transcribe_with_uplift(
    audio_bytes: bytes,
    filename: str = "recording.wav",
    content_type: str = "audio/wav",
    domain: Optional[str] = None
) -> dict:
    """Send audio to UpliftAI Speech-to-Text API as fallback."""
    if not (audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE"):
        audio_bytes = convert_to_wav(audio_bytes)
        filename = "recording.wav"
        mime = "audio/wav"
    else:
        ext = os.path.splitext(filename)[1].lower()
        mime = MIME_MAP.get(ext, content_type or "audio/wav")

    if len(audio_bytes) < 1500:
        return {"text": "", "confidence": 0.0}

    files = {"file": (filename, audio_bytes, mime)}
    data = {"model": "scribe", "language": "ur"}
    if domain:
        data["domain"] = domain

    headers = {"Authorization": f"Bearer {settings.UPLIFTAI_API_KEY}"}
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.post(
            f"{settings.UPLIFTAI_API_URL}/transcribe/speech-to-text",
            headers=headers,
            files=files,
            data=data
        )
        response.raise_for_status()
        res_data = response.json()
        transcript = res_data.get("transcript") or res_data.get("text") or ""
        confidence = float(res_data.get("confidence", 0.95))
        safe_text = transcript.encode("ascii", "backslashreplace").decode("ascii")
        logger.info(f"Uplift STT fallback transcript: '{safe_text}', conf={confidence}")
        return {"text": transcript.strip(), "confidence": confidence}


async def transcribe_audio(
    audio_bytes: bytes,
    filename: str = "recording.wav",
    content_type: str = "audio/wav",
    domain: Optional[str] = None
) -> dict:
    """
    Send audio to UpliftAI Speech-to-Text API (Beta).
    Model: 'scribe' (Urdu general vocabulary).
    """
    if not audio_bytes or len(audio_bytes) < 800:
        logger.warning(f"Audio payload too small: {len(audio_bytes) if audio_bytes else 0} bytes")
        return {"text": "", "confidence": 0.0}

    # Ensure audio is standard 16kHz mono WAV so Uplift STT decodes without error
    if not (audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE"):
        audio_bytes = convert_to_wav(audio_bytes)
        filename = "recording.wav"
        mime = "audio/wav"
    else:
        ext = os.path.splitext(filename)[1].lower()
        mime = MIME_MAP.get(ext, content_type or "audio/wav")

    if len(audio_bytes) < 1500:
        logger.warning(f"Converted audio too short ({len(audio_bytes)} bytes), skipping Uplift STT call")
        return {"text": "", "confidence": 0.0}

    # Uplift STT: using full 'scribe' model for Urdu accuracy
    files = {"file": (filename, audio_bytes, mime)}
    data = {
        "model": "scribe",
        "language": "ur"
    }
    if domain:
        data["domain"] = domain

    headers = {
        "Authorization": f"Bearer {settings.UPLIFTAI_API_KEY}"
    }

    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            response = await client.post(
                f"{settings.UPLIFTAI_API_URL}/transcribe/speech-to-text",
                headers=headers,
                files=files,
                data=data
            )
            logger.info(f"Uplift STT [{filename}] status: {response.status_code}")
            response.raise_for_status()
            res_data = response.json()
            transcript = res_data.get("transcript") or res_data.get("text") or ""
            confidence = float(res_data.get("confidence", 0.95))
            safe_text = transcript.encode("ascii", "backslashreplace").decode("ascii")
            logger.info(f"Uplift STT transcript: '{safe_text}', conf={confidence}")
            return {
                "text": transcript.strip(),
                "confidence": confidence
            }
    except Exception as e:
        logger.error(f"Uplift STT transcription failed for {filename} ({len(audio_bytes)} bytes): {e}")
        raise e


async def synthesize_speech(text: str, voice_id: str | None = None) -> str | None:
    """
    Send text to UpliftAI Orator TTS API using Prime Time Anchor voice.
    Docs: https://docs.upliftai.org/orator
    Saves generated MP3 to static audio cache and returns the endpoint URL.
    """
    if not text or not settings.UPLIFTAI_API_KEY:
        return None

    target_voice = voice_id or getattr(settings, "UPLIFTAI_VOICE_ID", "prime-time-anchor")
    headers = {
        "Authorization": f"Bearer {settings.UPLIFTAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "voiceId": target_voice,
        "text": text,
        "outputFormat": "MP3_22050_128"
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{settings.UPLIFTAI_API_URL}/synthesis/text-to-speech",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            audio_bytes = response.content

            if not audio_bytes:
                return None

            audio_id = str(uuid.uuid4())
            audio_path = os.path.join(AUDIO_DIR, f"{audio_id}.mp3")
            with open(audio_path, "wb") as f:
                f.write(audio_bytes)

            return f"/voice/audio/{audio_id}.mp3"
    except Exception as e:
        logger.warning(f"Uplift TTS speech synthesis failed: {e}")
        return None


async def create_realtime_session(participant_name: str = "Shopkeeper", assistant_id: str | None = None) -> dict:
    """
    Create a Realtime Assistant WebRTC session with Uplift AI.
    Docs: https://docs.upliftai.org/assistants/introduction
    """
    target_id = assistant_id or settings.UPLIFTAI_ASSISTANT_ID
    if not target_id:
        raise ValueError("No Uplift Realtime Assistant ID configured")

    headers = {
        "Authorization": f"Bearer {settings.UPLIFTAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "participantName": participant_name
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{settings.UPLIFTAI_API_URL}/realtime-assistants/{target_id}/createSession",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        return response.json()
