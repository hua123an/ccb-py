"""Voice input support for ccb-py.

Captures audio from microphone and converts to text using
system STT or external API (Whisper, etc.).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class VoiceInput:
    """Voice-to-text input handler."""

    def __init__(self):
        self._recording = False
        self._backend = self._detect_backend()

    @staticmethod
    def _detect_backend() -> str:
        """Detect available audio/STT backend."""
        # macOS: built-in say/speech recognition
        if os.uname().sysname == "Darwin":
            # Check for whisper CLI
            try:
                subprocess.run(["which", "whisper"], capture_output=True, check=True)
                return "whisper"
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
            # Check for sox (rec command)
            try:
                subprocess.run(["which", "rec"], capture_output=True, check=True)
                return "sox"
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
            return "macos"

        # Linux: check for arecord, parecord, whisper
        for tool in ("whisper", "arecord", "parecord"):
            try:
                subprocess.run(["which", tool], capture_output=True, check=True)
                return tool
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue
        return "none"

    @property
    def available(self) -> bool:
        return self._backend != "none"

    @property
    def backend_name(self) -> str:
        return self._backend

    async def record_and_transcribe(self, duration: int = 10) -> str:
        """Record audio and transcribe to text."""
        if not self.available:
            return "[Voice input not available — install whisper or sox]"

        audio_file = tempfile.mktemp(suffix=".wav")
        try:
            # Record
            await self._record(audio_file, duration)
            # Transcribe
            text = await self._transcribe(audio_file)
            return text
        finally:
            try:
                os.unlink(audio_file)
            except OSError:
                pass

    async def _record(self, output_path: str, duration: int) -> None:
        """Record audio to file."""
        self._recording = True
        try:
            if self._backend == "sox":
                proc = await asyncio.create_subprocess_exec(
                    "rec", "-q", "-r", "16000", "-c", "1", output_path,
                    "trim", "0", str(duration),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            elif self._backend == "arecord":
                proc = await asyncio.create_subprocess_exec(
                    "arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1",
                    "-d", str(duration), output_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            elif self._backend == "macos":
                # Use macOS afrecord or ffmpeg
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "ffmpeg", "-y", "-f", "avfoundation", "-i", ":0",
                        "-t", str(duration), "-ar", "16000", "-ac", "1",
                        output_path,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()
                except FileNotFoundError:
                    raise RuntimeError("No audio recording tool found. Install sox or ffmpeg.")
        finally:
            self._recording = False

    async def _transcribe(self, audio_path: str) -> str:
        """Transcribe audio file to text."""
        if self._backend == "whisper":
            return await self._transcribe_whisper(audio_path)
        # Fall back to API-based transcription
        return await self._transcribe_api(audio_path)

    async def _transcribe_whisper(self, audio_path: str) -> str:
        """Transcribe using local Whisper model."""
        proc = await asyncio.create_subprocess_exec(
            "whisper", audio_path, "--model", "base", "--output_format", "txt",
            "--output_dir", str(Path(audio_path).parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        txt_file = Path(audio_path).with_suffix(".txt")
        if txt_file.exists():
            text = txt_file.read_text().strip()
            txt_file.unlink(missing_ok=True)
            return text
        return stdout.decode().strip()

    async def _transcribe_api(self, audio_path: str) -> str:
        """Transcribe using OpenAI Whisper API."""
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return "[Set OPENAI_API_KEY for voice transcription]"

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                data.add_field("file", open(audio_path, "rb"),
                               filename="audio.wav", content_type="audio/wav")
                data.add_field("model", "whisper-1")
                async with session.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=data,
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get("text", "")
                    return f"[Transcription failed: {resp.status}]"
        except ImportError:
            return "[aiohttp required for API transcription]"

    def stop_recording(self) -> None:
        self._recording = False

    @property
    def is_recording(self) -> bool:
        return self._recording


    # ── Configuration ──

    def set_whisper_model(self, model: str) -> None:
        """Set Whisper model size: tiny, base, small, medium, large."""
        self._whisper_model = model

    def set_language(self, language: str) -> None:
        """Set transcription language (e.g. 'en', 'zh', 'ja')."""
        self._language = language

    def set_api_base(self, url: str) -> None:
        """Set custom STT API base URL."""
        self._api_base = url

    # ── Continuous mode ──

    async def continuous_listen(self, callback: Any, chunk_duration: int = 5, max_silence: int = 2) -> None:
        """Continuously record and transcribe, calling callback with each chunk.

        Stops when 'stop' is returned by callback or stop_recording() is called.
        """
        self._recording = True
        try:
            while self._recording:
                audio_file = tempfile.mktemp(suffix=".wav")
                try:
                    await self._record(audio_file, chunk_duration)
                    text = await self._transcribe(audio_file)
                    if text.strip():
                        result = await callback(text)
                        if result == "stop":
                            break
                finally:
                    try:
                        os.unlink(audio_file)
                    except OSError:
                        pass
        finally:
            self._recording = False

    # ── Push-to-talk ──

    async def push_to_talk(self, max_duration: int = 30) -> str:
        """Record while called, transcribe when done.

        Used for push-to-talk where recording stops on function return.
        """
        return await self.record_and_transcribe(max_duration)

    # ── Info ──

    def info(self) -> dict[str, Any]:
        return {
            "backend": self._backend,
            "available": self.available,
            "recording": self._recording,
            "whisper_model": getattr(self, "_whisper_model", "base"),
            "language": getattr(self, "_language", "auto"),
        }


# Module singleton
_voice: VoiceInput | None = None


def get_voice_input() -> VoiceInput:
    global _voice
    if _voice is None:
        _voice = VoiceInput()
    return _voice
