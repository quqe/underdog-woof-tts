#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Ilia Okhrimenko
# SPDX-License-Identifier: MIT
"""Minimal HTTP wrapper around Underdog Woof (https://huggingface.co/ConwayResearch/Underdog-Woof).

    GET  /tts?<raw text>              -> audio/wav
    GET  /tts?text=<text>&voice=...   -> audio/wav
    GET  /health                      -> JSON
    GET  /                            -> tiny web form for manual testing
    GET  /v1/models                   -> OpenAI-compatible model list
    POST /v1/audio/speech             -> OpenAI-compatible speech endpoint

Speed control: `rate` (and the OpenAI `speed`) time-stretches the result with ffmpeg's
atempo filter (pitch preserved), chaining passes for rates outside 0.5..2.0.
Response formats other than WAV/PCM are also encoded with ffmpeg.
stdlib http.server only, so the only Python dependency is numpy.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote_plus, urlsplit

import numpy as np

from underdog_woof import SR, VOICES, Woof

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "3005"))
DEFAULT_VOICE = os.environ.get("DEFAULT_VOICE", "juno")
WEIGHTS = os.environ.get("WOOF_WEIGHTS") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "woof_int8.npz")
INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html")
MAX_CHARS = int(os.environ.get("MAX_CHARS", "2000"))
QUEUE_TIMEOUT = float(os.environ.get("QUEUE_TIMEOUT", "120"))
# Each voice costs ~250 MB of RSS. Cache 2 by default (Juno + Mira); set 1 to stay near 300 MB.
MAX_VOICES_LOADED = max(1, int(os.environ.get("MAX_VOICES_LOADED", "2")))
PRECACHE = [v.strip() for v in os.environ.get("PRECACHE_VOICES", DEFAULT_VOICE).split(",") if v.strip()]

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFMPEG_PATH = shutil.which(FFMPEG_BIN)
# atempo accepts 0.5..2.0 per pass; wider rates are split into several passes.
MIN_RATE, MAX_RATE = 0.25, 4.0
TEMPO_TIMEOUT = float(os.environ.get("TEMPO_TIMEOUT", "60"))

# --- OpenAI-compatible /v1/audio/speech --------------------------------------
# The model has two voices; the OpenAI voice names are mapped onto them so that
# existing OpenAI clients keep working without reconfiguration.
VOICE_ALIASES = {
    "alloy": "juno", "echo": "juno", "onyx": "juno",
    "fable": "mira", "nova": "mira", "shimmer": "mira",
    "juno": "juno", "mira": "mira",
}
OPENAI_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
OPENAI_MODELS = (
    "underdog-woof", "underdog-woof-tts", "woof",
    "tts-1", "tts-1-hd", "gpt-4o-mini-tts",
)
DEFAULT_OPENAI_MODEL = OPENAI_MODELS[0]
DEFAULT_OPENAI_VOICE = "alloy"
DEFAULT_OPENAI_FORMAT = "mp3"
# response_format -> (Content-Type, ffmpeg output args); wav and pcm need no encoding.
AUDIO_FORMATS: dict[str, tuple[str, list[str] | None]] = {
    "wav": ("audio/wav", None),
    "pcm": ("audio/pcm", None),
    "mp3": ("audio/mpeg", ["-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3"]),
    "opus": ("audio/opus", ["-c:a", "libopus", "-b:a", "64k", "-f", "ogg"]),
    "aac": ("audio/aac", ["-c:a", "aac", "-b:a", "128k", "-f", "adts"]),
    "flac": ("audio/flac", ["-c:a", "flac", "-f", "flac"]),
}
MAX_BODY = 256 * 1024
# Bodies above MAX_BODY are still read (and dropped) up to this size before rejecting,
# so the client receives the 413 instead of a connection reset mid-upload.
MAX_DRAIN = 8 * 1024 * 1024

STARTED = time.time()
REQUESTS = 0
TTS_SECONDS = 0.0
STATS_LOCK = threading.Lock()

# Woof mutates a module-global RNG and is not thread-safe, so synthesis is serialized:
# requests queue on this lock instead of interleaving inside the model.
MODEL_LOCK = threading.Lock()
MODELS: dict[str, Woof] = {}
LAST_USED: dict[str, float] = {}


class Busy(RuntimeError):
    pass


class FFmpegError(RuntimeError):
    pass


class TempoError(FFmpegError):
    pass


class EncodeError(FFmpegError):
    pass


class RateUnavailable(RuntimeError):
    pass


class ApiError(RuntimeError):
    """A client error for the OpenAI-compatible endpoint: status, message, param, code."""

    def __init__(self, status: int, message: str, param: str | None = None,
                 type_: str = "invalid_request_error", code: str | None = None):
        super().__init__(message)
        self.status = status
        self.param = param
        self.type = type_
        self.code = code


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getnframes() / w.getframerate()


def pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap 16-bit mono PCM in a WAV container with correct RIFF sizes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    return buf.getvalue()


def tempo_filter(rate: float) -> str:
    """Build an atempo chain for any rate: a single pass only accepts 0.5..2.0."""
    factors, remaining = [], rate
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    factors.append(remaining)
    return ",".join(f"atempo={factor:g}" for factor in factors)


def run_ffmpeg(args: list[str], data: bytes, timeout: float,
               error_cls: type[FFmpegError]) -> bytes:
    """Run ffmpeg on stdin and return its stdout; raise `error_cls` on failure."""
    cmd = [FFMPEG_PATH or "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *args]
    try:
        proc = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise error_cls(f"ffmpeg timed out after {timeout:g}s") from exc
    if proc.returncode != 0 or not proc.stdout:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise error_cls(detail[-1] if detail else f"ffmpeg exited with {proc.returncode}")
    return proc.stdout


def pcm_from_wav(wav: bytes) -> bytes:
    """Strip the RIFF header: raw 16-bit mono PCM at the model sample rate."""
    with wave.open(io.BytesIO(wav), "rb") as w:
        return w.readframes(w.getnframes())


def apply_tempo(wav: bytes, rate: float) -> bytes:
    """Time-stretch a WAV with ffmpeg's atempo (pitch preserved). Runs outside MODEL_LOCK.

    ffmpeg writes raw s16le to stdout rather than WAV: a WAV header on a pipe cannot hold real
    chunk sizes (ffmpeg would emit 0xFFFFFFFF placeholders), so we re-wrap the PCM ourselves.
    """
    if not FFMPEG_PATH:
        raise RateUnavailable("ffmpeg is not installed in this image; rate!=1 is unavailable")
    args = [
        "-f", "wav", "-i", "pipe:0",
        "-filter:a", tempo_filter(rate),
        "-ac", "1", "-ar", str(SR), "-acodec", "pcm_s16le", "-f", "s16le", "pipe:1",
    ]
    pcm = run_ffmpeg(args, wav, TEMPO_TIMEOUT, TempoError)
    pcm = pcm[: len(pcm) - (len(pcm) % 2)]
    return pcm_to_wav(pcm)


def encode_audio(wav: bytes, fmt: str) -> bytes:
    """Turn the generated WAV into the requested OpenAI `response_format`."""
    if fmt == "wav":
        return wav
    if fmt == "pcm":
        return pcm_from_wav(wav)
    if not FFMPEG_PATH:
        raise RateUnavailable(f"ffmpeg is not installed in this image; response_format={fmt} is unavailable")
    args = [
        "-f", "wav", "-i", "pipe:0",
        "-ac", "1", "-ar", str(SR),
        *(AUDIO_FORMATS[fmt][1] or []), "pipe:1",
    ]
    return run_ffmpeg(args, wav, TEMPO_TIMEOUT, EncodeError)


def get_model(voice: str) -> Woof:
    """Load a voice on first use and keep it warm. Caller must hold MODEL_LOCK."""
    model = MODELS.get(voice)
    if model is None:
        while len(MODELS) >= MAX_VOICES_LOADED:
            victim = min(MODELS, key=lambda v: LAST_USED.get(v, 0.0))
            del MODELS[victim]
            LAST_USED.pop(victim, None)
            print(f"unloaded voice {victim!r} to stay within MAX_VOICES_LOADED={MAX_VOICES_LOADED}", flush=True)
        model = Woof(weights=WEIGHTS, voice=voice)
        MODELS[voice] = model
    LAST_USED[voice] = time.time()
    return model


def synthesize(text: str, voice: str, seed: int | None) -> tuple[bytes, float]:
    """Text -> WAV bytes. Returns (wav, audio_seconds). Waits at most QUEUE_TIMEOUT for the model."""
    if not MODEL_LOCK.acquire(timeout=QUEUE_TIMEOUT):
        raise Busy("server busy, retry later")
    try:
        wav = get_model(voice)(text, seed=seed)
    finally:
        MODEL_LOCK.release()

    pcm = (np.clip(wav, -1, 1) * 32767).round().astype("<i2").tobytes()
    return pcm_to_wav(pcm), len(pcm) / 2 / SR


KNOWN_PARAMS = ("text", "voice", "seed", "rate")


def parse_request(query: str) -> tuple[str, dict[str, str]]:
    """Accept `?my super text`, `?text=my super text&voice=mira` and `?my super text&voice=mira`.

    Segments are split on `&`; only `text`/`voice`/`seed` are treated as parameters, everything
    else is taken as the text itself, so the query string can be read as plain English.
    """
    raw, params = [], {}
    for part in query.split("&"):
        if not part:
            continue
        key, sep, value = part.partition("=")
        key = unquote_plus(key).lower()
        if sep and key in KNOWN_PARAMS:
            params.setdefault(key, value)
        else:
            raw.append(part)
    text = unquote_plus(params["text"]) if "text" in params else unquote_plus("&".join(raw))
    params.pop("text", None)
    return text, {k: unquote_plus(v) for k, v in params.items()}


def filename_for(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:32]
    return f"{slug or 'speech'}.wav"


def validate_speech_request(payload: dict) -> tuple[str, str, str, float, int]:
    """Validate an OpenAI /v1/audio/speech body; return (text, voice, format, speed, seed)."""
    model = payload.get("model", DEFAULT_OPENAI_MODEL)
    if not isinstance(model, str) or not model.strip():
        raise ApiError(400, "model must be a non-empty string", param="model")
    model = model.strip()
    if model not in OPENAI_MODELS:
        raise ApiError(404, f"model {model!r} does not exist; available: {', '.join(OPENAI_MODELS)}",
                       param="model", code="model_not_found")

    text = payload.get("input")
    if not isinstance(text, str) or not text.strip():
        raise ApiError(400, "input must be a non-empty string", param="input")
    text = text.strip()
    if len(text) > MAX_CHARS:
        raise ApiError(400, f"input too long: {len(text)} chars, limit is {MAX_CHARS}",
                       param="input", code="input_too_long")
    if not re.search(r"[A-Za-z]", text):
        raise ApiError(400, "Underdog Woof is an English-only model; the text must contain "
                            "Latin letters", param="input")

    requested = payload.get("voice", DEFAULT_OPENAI_VOICE)
    if not isinstance(requested, str) or requested.strip().lower() not in VOICE_ALIASES:
        raise ApiError(400, f"unknown voice {requested!r}; available: "
                            f"{', '.join(OPENAI_VOICES + VOICES)}", param="voice")
    voice = VOICE_ALIASES[requested.strip().lower()]

    fmt = payload.get("response_format", DEFAULT_OPENAI_FORMAT)
    if not isinstance(fmt, str) or fmt.strip().lower() not in AUDIO_FORMATS:
        raise ApiError(400, f"unsupported response_format {fmt!r}; available: "
                            f"{', '.join(AUDIO_FORMATS)}", param="response_format")
    fmt = fmt.strip().lower()

    speed = payload.get("speed", 1.0)
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise ApiError(400, "speed must be a number, e.g. 1.3", param="speed")
    speed = float(speed)
    if not (MIN_RATE <= speed <= MAX_RATE):
        raise ApiError(400, f"speed must be between {MIN_RATE} and {MAX_RATE}", param="speed")

    seed = payload.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ApiError(400, "seed must be an integer", param="seed")

    return text, voice, fmt, speed, seed


class Handler(BaseHTTPRequestHandler):
    server_version = "underdog-woof/1.1"
    protocol_version = "HTTP/1.1"
    # Bound per-socket reads and writes: a client that announces a body and never sends it
    # must not pin a worker thread forever. Generation itself is not affected.
    timeout = 30

    # --- helpers -----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str, extra: dict[str, str] | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)

    def _json(self, code: int, payload: dict, extra: dict[str, str] | None = None):
        self._send(code, json.dumps(payload, indent=2).encode(), "application/json; charset=utf-8", extra)

    def _error(self, code: int, message: str, **extra):
        self._json(code, {"error": message, **extra})

    def _openai_error(self, code: int, message: str, param: str | None = None,
                      type_: str = "invalid_request_error", error_code: str | None = None,
                      extra: dict[str, str] | None = None):
        self._json(code, {"error": {
            "message": message,
            "type": type_,
            "param": param,
            "code": error_code,
        }}, extra)

    # --- routes ------------------------------------------------------------
    def do_GET(self):
        global REQUESTS, TTS_SECONDS
        url = urlsplit(self.path)
        path = url.path.rstrip("/") or "/"

        if path == "/":
            try:
                with open(INDEX_PATH, "rb") as fh:
                    body = fh.read()
            except OSError:
                return self._error(500, "web/index.html is missing from the image")
            return self._send(200, body, "text/html; charset=utf-8", {"Cache-Control": "no-cache"})

        if path == "/health":
            return self._json(200, {
                "status": "ok",
                "voices": list(VOICES),
                "loaded": sorted(MODELS),
                "max_voices_loaded": MAX_VOICES_LOADED,
                "max_chars": MAX_CHARS,
                "ffmpeg": bool(FFMPEG_PATH),
                "rate_range": [MIN_RATE, MAX_RATE],
                "openai_compatible": {
                    "speech": "POST /v1/audio/speech",
                    "models": "GET /v1/models",
                    "voices": list(OPENAI_VOICES),
                    "formats": list(AUDIO_FORMATS),
                },
                "license": {
                    "code": "MIT",
                    "model": "Underdog-NonCommercial-1.0",
                    "attribution": "Voice by Underdog Woof",
                    "details": "https://github.com/quqe/underdog-woof-tts/blob/main/LICENSING.md",
                },
                "uptime_s": round(time.time() - STARTED, 1),
                "requests": REQUESTS,
                "audio_seconds": round(TTS_SECONDS, 1),
            })

        if path == "/v1/models":
            return self._models()

        if path == "/v1/audio/speech":
            return self._openai_error(405, "use POST /v1/audio/speech",
                                      extra={"Allow": "POST"})

        if path != "/tts":
            return self._error(404, "not found",
                               hint="GET /tts?<text>, GET /tts?text=<text>, POST /v1/audio/speech")

        text, params = parse_request(url.query)
        text = text.strip()
        voice = params.get("voice", DEFAULT_VOICE).lower()

        if voice not in VOICES:
            return self._error(400, f"unknown voice {voice!r}", voices=list(VOICES))
        if not text:
            return self._error(400, "empty text", usage="GET /tts?Hello%20there or GET /tts?text=Hello+there")
        if len(text) > MAX_CHARS:
            return self._error(413, f"text too long: {len(text)} chars, limit is {MAX_CHARS}", max_chars=MAX_CHARS)
        if not re.search(r"[A-Za-z]", text):
            return self._error(400, "Underdog Woof is an English-only model; the text must contain Latin letters")

        try:
            seed = int(params["seed"]) if "seed" in params else 0
        except ValueError:
            return self._error(400, "seed must be an integer")

        try:
            rate = float(params.get("rate", "1"))
        except ValueError:
            return self._error(400, "rate must be a number, e.g. 1.3")
        if not (MIN_RATE <= rate <= MAX_RATE):
            return self._error(400, f"rate must be between {MIN_RATE} and {MAX_RATE}", rate=rate)

        t0 = time.perf_counter()
        try:
            body, seconds = synthesize(text, voice, seed)
        except Busy as exc:
            return self._error(503, str(exc), queue_timeout_s=QUEUE_TIMEOUT)
        except MemoryError:
            return self._error(503, "out of memory")

        if rate != 1.0:
            try:
                body = apply_tempo(body, rate)
            except RateUnavailable as exc:
                return self._error(501, str(exc))
            except TempoError as exc:
                return self._error(500, f"time-stretch failed: {exc}")
            seconds = wav_seconds(body)
        elapsed = time.perf_counter() - t0

        with STATS_LOCK:
            REQUESTS += 1
            TTS_SECONDS += seconds

        self.log_message('200 voice=%s chars=%d rate=%g audio=%.2fs gen=%.2fs', voice, len(text), rate, seconds, elapsed)
        return self._send(200, body, "audio/wav", {
            "Content-Disposition": f'inline; filename="{filename_for(text)}"',
            "Cache-Control": "no-store",
            "X-Voice": voice,
            "X-Rate": f"{rate:g}",
            "X-Audio-Seconds": f"{seconds:.2f}",
            "X-Generation-Seconds": f"{elapsed:.2f}",
            "X-Seed": str(seed),
        })

    def do_HEAD(self):
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    # --- OpenAI-compatible API ---------------------------------------------
    def _models(self):
        created = int(STARTED)
        self._json(200, {
            "object": "list",
            "data": [
                {"id": model, "object": "model", "created": created, "owned_by": "underdog-woof"}
                for model in OPENAI_MODELS
            ],
        })

    def _drain_body(self, size: int) -> None:
        """Read and drop a rejected request body, bounded by MAX_DRAIN."""
        remaining = size
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                self.close_connection = True
                return
            remaining -= len(chunk)

    def _read_json(self) -> dict:
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            self.close_connection = True
            raise ApiError(411, "chunked request bodies are not supported; send Content-Length")
        length = self.headers.get("Content-Length")
        if length is None:
            self.close_connection = True
            raise ApiError(411, "Content-Length is required")
        try:
            size = int(length)
        except ValueError:
            self.close_connection = True
            raise ApiError(400, "invalid Content-Length header") from None
        if size <= 0:
            raise ApiError(400, "request body is empty")
        if size > MAX_BODY:
            if size <= MAX_DRAIN:
                self._drain_body(size)
            else:
                self.close_connection = True
            raise ApiError(413, f"request body too large: {size} bytes, limit is {MAX_BODY}",
                           code="request_too_large")
        raw = self.rfile.read(size)
        if len(raw) != size:
            self.close_connection = True
            raise ApiError(400, "incomplete request body")
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype not in ("", "application/json"):
            raise ApiError(415, f"unsupported Content-Type {ctype!r}; use application/json")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(400, f"invalid JSON body: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ApiError(400, "request body must be a JSON object")
        return payload

    def _speech(self):
        global REQUESTS, TTS_SECONDS
        try:
            payload = self._read_json()
            text, voice, fmt, speed, seed = validate_speech_request(payload)
        except ApiError as exc:
            return self._openai_error(exc.status, str(exc), exc.param, exc.type, exc.code)

        # Without ffmpeg there is no MP3/Opus/AAC/FLAC; fall back to WAV only when the
        # client did not explicitly ask for a compressed format.
        if fmt not in ("wav", "pcm") and not FFMPEG_PATH:
            if "response_format" in payload:
                return self._openai_error(
                    503, f"response_format={fmt} needs ffmpeg, which is not installed in this image",
                    type_="server_error")
            fmt = "wav"

        t0 = time.perf_counter()
        try:
            wav, seconds = synthesize(text, voice, seed)
            if speed != 1.0:
                wav = apply_tempo(wav, speed)
                seconds = wav_seconds(wav)
            body = encode_audio(wav, fmt)
        except Busy as exc:
            return self._openai_error(503, str(exc), type_="server_error")
        except MemoryError:
            return self._openai_error(503, "out of memory", type_="server_error")
        except RateUnavailable as exc:
            return self._openai_error(503, str(exc), type_="server_error")
        except TempoError as exc:
            return self._openai_error(500, f"time-stretch failed: {exc}", type_="server_error")
        except EncodeError as exc:
            return self._openai_error(500, f"audio encoding failed: {exc}", type_="server_error")
        elapsed = time.perf_counter() - t0

        with STATS_LOCK:
            REQUESTS += 1
            TTS_SECONDS += seconds

        self.log_message('200 POST /v1/audio/speech voice=%s format=%s chars=%d speed=%g '
                         'audio=%.2fs gen=%.2fs',
                         voice, fmt, len(text), speed, seconds, elapsed)
        return self._send(200, body, AUDIO_FORMATS[fmt][0], {
            "Cache-Control": "no-store",
            "X-Voice": voice,
            "X-Rate": f"{speed:g}",
            "X-Format": fmt,
            "X-Audio-Seconds": f"{seconds:.2f}",
            "X-Generation-Seconds": f"{elapsed:.2f}",
            "X-Seed": str(seed),
        })

    def do_POST(self):
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/v1/audio/speech":
            return self._speech()
        if path == "/tts":
            return self._error(405, "only GET is supported for /tts", allow="GET")
        return self._error(404, "not found", hint="POST /v1/audio/speech")

    def _method_not_allowed(self):
        self._error(405, "method not allowed", allow="GET, HEAD, POST")

    do_PUT = do_DELETE = do_PATCH = _method_not_allowed

    def log_message(self, fmt, *args):
        line = f"{time.strftime('%H:%M:%S')} {self.address_string()} {fmt % args}"
        print(line[:240] + ("..." if len(line) > 240 else ""), flush=True)


def main():
    if not os.path.exists(WEIGHTS):
        raise SystemExit(f"weights not found: {WEIGHTS}\nrun scripts/fetch_model.py first")
    for voice in PRECACHE:
        if voice in VOICES:
            t0 = time.perf_counter()
            with MODEL_LOCK:
                get_model(voice)
            print(f"loaded voice {voice!r} in {time.perf_counter() - t0:.2f}s", flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"Underdog Woof listening on http://{HOST}:{PORT}  (voices: {', '.join(VOICES)})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
