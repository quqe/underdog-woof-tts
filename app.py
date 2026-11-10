#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Ilia Okhrimenko
# SPDX-License-Identifier: MIT
"""Minimal HTTP wrapper around Underdog Woof (https://huggingface.co/ConwayResearch/Underdog-Woof).

    GET /tts?<raw text>              -> audio/wav
    GET /tts?text=<text>&voice=...   -> audio/wav
    GET /health                      -> JSON
    GET /                            -> tiny web form for manual testing

Speed control: `&rate=1.3` time-stretches the result with ffmpeg's atempo filter
(pitch preserved). stdlib http.server only, so the only Python dependency is numpy.
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
MAX_CHARS = int(os.environ.get("MAX_CHARS", "2000"))
QUEUE_TIMEOUT = float(os.environ.get("QUEUE_TIMEOUT", "120"))
# Each voice costs ~250 MB of RSS. Cache 2 by default (Juno + Mira); set 1 to stay near 300 MB.
MAX_VOICES_LOADED = max(1, int(os.environ.get("MAX_VOICES_LOADED", "2")))
PRECACHE = [v.strip() for v in os.environ.get("PRECACHE_VOICES", DEFAULT_VOICE).split(",") if v.strip()]

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFMPEG_PATH = shutil.which(FFMPEG_BIN)
# atempo accepts 0.5..2.0 per pass; outside that range speed changes go artefact-heavy anyway.
MIN_RATE, MAX_RATE = 0.5, 2.0
TEMPO_TIMEOUT = float(os.environ.get("TEMPO_TIMEOUT", "60"))

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


class TempoError(RuntimeError):
    pass


class RateUnavailable(RuntimeError):
    pass


INDEX_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Underdog Woof TTS</title>
<style>body{font:16px system-ui;max-width:42rem;margin:3rem auto;padding:0 1rem}
textarea{width:100%;min-height:6rem;font:inherit;padding:.5rem}
select,button{font:inherit;padding:.4rem .8rem;margin-top:.6rem}
code{background:#f4f4f5;padding:.15rem .3rem;border-radius:4px}</style></head>
<body><h1>Underdog Woof TTS</h1>
<form action="/tts" method="get">
<textarea name="text" placeholder="Type English text here...">Hey guys! I think I've found the coolest model for text-to-speech conversion!</textarea>
<div><select name="voice"><option>juno</option><option>mira</option></select>
<select name="rate"><option>1</option><option>1.25</option><option>1.3</option><option>1.4</option></select>
<button type="submit">Speak</button></div>
</form>
<p>Or straight from the shell:<br>
<code>curl -o speech.wav "http://localhost:__PORT__/tts?my%20super%20text"</code><br>
<code>curl -o fast.wav "http://localhost:__PORT__/tts?my%20super%20text&amp;rate=1.3"</code></p>
<footer>Voice by <a href="https://underdog.ai">Underdog Woof</a> &middot;
wrapper code MIT &middot; model under the
<a href="https://github.com/quqe/underdog-woof-tts/blob/main/LICENSING.md">Underdog Non-Commercial License</a></footer>
</body></html>
"""


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


def apply_tempo(wav: bytes, rate: float) -> bytes:
    """Time-stretch a WAV with ffmpeg's atempo (pitch preserved). Runs outside MODEL_LOCK.

    ffmpeg writes raw s16le to stdout rather than WAV: a WAV header on a pipe cannot hold real
    chunk sizes (ffmpeg would emit 0xFFFFFFFF placeholders), so we re-wrap the PCM ourselves.
    """
    if not FFMPEG_PATH:
        raise RateUnavailable("ffmpeg is not installed in this image; rate!=1 is unavailable")
    cmd = [
        FFMPEG_PATH, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "wav", "-i", "pipe:0",
        "-filter:a", f"atempo={rate:g}",
        "-ac", "1", "-ar", str(SR), "-acodec", "pcm_s16le", "-f", "s16le", "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, input=wav, capture_output=True, timeout=TEMPO_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise TempoError(f"ffmpeg timed out after {TEMPO_TIMEOUT:g}s") from exc
    if proc.returncode != 0 or not proc.stdout:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise TempoError(detail[-1] if detail else f"ffmpeg exited with {proc.returncode}")
    pcm = proc.stdout[: len(proc.stdout) - (len(proc.stdout) % 2)]
    return pcm_to_wav(pcm)


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


class Handler(BaseHTTPRequestHandler):
    server_version = "underdog-woof/1.0"
    protocol_version = "HTTP/1.1"

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

    def _json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload, indent=2).encode(), "application/json; charset=utf-8")

    def _error(self, code: int, message: str, **extra):
        self._json(code, {"error": message, **extra})

    # --- routes ------------------------------------------------------------
    def do_GET(self):
        global REQUESTS, TTS_SECONDS
        url = urlsplit(self.path)
        path = url.path.rstrip("/") or "/"

        if path == "/":
            body = INDEX_HTML.replace("__PORT__", str(PORT)).encode()
            return self._send(200, body, "text/html; charset=utf-8")

        if path == "/health":
            return self._json(200, {
                "status": "ok",
                "voices": list(VOICES),
                "loaded": sorted(MODELS),
                "max_voices_loaded": MAX_VOICES_LOADED,
                "max_chars": MAX_CHARS,
                "ffmpeg": bool(FFMPEG_PATH),
                "rate_range": [MIN_RATE, MAX_RATE],
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

        if path != "/tts":
            return self._error(404, "not found", hint="GET /tts?<text> or GET /tts?text=<text>")

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

    def do_POST(self):
        self._error(405, "only GET is supported", allow="GET")

    do_PUT = do_DELETE = do_PATCH = do_POST

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
