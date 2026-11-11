#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Ilia Okhrimenko
# SPDX-License-Identifier: MIT
"""Unit tests for the wrapper's pure helpers: request validation, ffmpeg filters, WAV glue.

No model, no network, no ffmpeg process. Run from the project root:

    python3 -m unittest discover -s tests -p 'test_*.py'
"""
import io
import sys
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app


class SpeechRequestTests(unittest.TestCase):
    def test_defaults(self):
        text, voice, fmt, speed, seed = app.validate_speech_request({"input": "Hello"})
        self.assertEqual(text, "Hello")
        self.assertEqual(voice, "juno")
        self.assertEqual(fmt, "mp3")
        self.assertEqual(speed, 1.0)
        self.assertEqual(seed, 0)

    def test_input_is_stripped(self):
        text, *_ = app.validate_speech_request({"input": "  Hello there  "})
        self.assertEqual(text, "Hello there")

    def test_every_openai_voice_has_a_model_voice(self):
        for name in app.OPENAI_VOICES:
            _, voice, *_ = app.validate_speech_request({"input": "Hello", "voice": name})
            self.assertIn(voice, app.VOICES)

    def test_voice_is_case_insensitive(self):
        _, voice, *_ = app.validate_speech_request({"input": "Hello", "voice": "NOVA"})
        self.assertEqual(voice, "mira")

    def test_native_voice_names_accepted(self):
        for name in app.VOICES:
            _, voice, *_ = app.validate_speech_request({"input": "Hello", "voice": name})
            self.assertEqual(voice, name)

    def test_model_aliases_accepted(self):
        for model in app.OPENAI_MODELS:
            app.validate_speech_request({"input": "Hello", "model": model})

    def test_unknown_model_is_404(self):
        with self.assertRaises(app.ApiError) as ctx:
            app.validate_speech_request({"input": "Hello", "model": "gpt-4o-transcribe"})
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(ctx.exception.param, "model")
        self.assertEqual(ctx.exception.code, "model_not_found")

    def test_input_required(self):
        for value in (None, "", "   ", 42):
            with self.assertRaises(app.ApiError) as ctx:
                app.validate_speech_request({"input": value})
            self.assertEqual(ctx.exception.param, "input")

    def test_input_too_long(self):
        with self.assertRaises(app.ApiError) as ctx:
            app.validate_speech_request({"input": "a " * (app.MAX_CHARS + 1)})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, "input_too_long")

    def test_english_only(self):
        with self.assertRaises(app.ApiError) as ctx:
            app.validate_speech_request({"input": "Привет"})
        self.assertEqual(ctx.exception.param, "input")

    def test_unknown_voice(self):
        with self.assertRaises(app.ApiError) as ctx:
            app.validate_speech_request({"input": "Hello", "voice": "zzz"})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.param, "voice")

    def test_response_formats(self):
        for fmt in app.AUDIO_FORMATS:
            _, _, got, *_ = app.validate_speech_request({"input": "Hello", "response_format": fmt})
            self.assertEqual(got, fmt)

    def test_unknown_response_format(self):
        with self.assertRaises(app.ApiError) as ctx:
            app.validate_speech_request({"input": "Hello", "response_format": "ogg"})
        self.assertEqual(ctx.exception.param, "response_format")

    def test_speed_bounds(self):
        for value in (app.MIN_RATE, app.MAX_RATE, 1.25):
            _, _, _, speed, _ = app.validate_speech_request({"input": "Hello", "speed": value})
            self.assertEqual(speed, float(value))
        for value in (app.MIN_RATE - 0.01, app.MAX_RATE + 0.01):
            with self.assertRaises(app.ApiError) as ctx:
                app.validate_speech_request({"input": "Hello", "speed": value})
            self.assertEqual(ctx.exception.param, "speed")

    def test_speed_must_be_a_number(self):
        for value in ("1.3", None, True):
            with self.assertRaises(app.ApiError) as ctx:
                app.validate_speech_request({"input": "Hello", "speed": value})
            self.assertEqual(ctx.exception.param, "speed")

    def test_seed_must_be_an_integer(self):
        for value in ("1", 1.5, True):
            with self.assertRaises(app.ApiError) as ctx:
                app.validate_speech_request({"input": "Hello", "seed": value})
            self.assertEqual(ctx.exception.param, "seed")

    def test_unknown_fields_are_ignored(self):
        text, *_ = app.validate_speech_request({
            "input": "Hello", "instructions": "speak softly", "extra": [1, 2],
        })
        self.assertEqual(text, "Hello")


class TempoFilterTests(unittest.TestCase):
    def test_single_pass_for_narrow_rates(self):
        self.assertEqual(app.tempo_filter(1.3), "atempo=1.3")
        self.assertEqual(app.tempo_filter(0.5), "atempo=0.5")
        self.assertEqual(app.tempo_filter(2.0), "atempo=2")

    def test_slowest_rate_chains_two_halves(self):
        self.assertEqual(app.tempo_filter(0.25), "atempo=0.5,atempo=0.5")

    def test_fastest_rate_chains_two_doubles(self):
        self.assertEqual(app.tempo_filter(4.0), "atempo=2,atempo=2")

    def test_intermediate_rates_stay_in_range(self):
        self.assertEqual(app.tempo_filter(3.0), "atempo=2,atempo=1.5")
        self.assertEqual(app.tempo_filter(0.3), "atempo=0.5,atempo=0.6")


class WavHelperTests(unittest.TestCase):
    def test_pcm_roundtrip(self):
        pcm = b"\x00\x01\x02\x03" * 100
        wav = app.pcm_to_wav(pcm)
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(app.pcm_from_wav(wav), pcm)

    def test_wav_seconds(self):
        wav = app.pcm_to_wav(b"\x00\x00" * app.SR)  # one second of silence
        self.assertAlmostEqual(app.wav_seconds(wav), 1.0, places=3)

    def test_pcm_to_wav_header(self):
        wav = app.pcm_to_wav(b"\x00\x00" * 10)
        with wave.open(io.BytesIO(wav), "rb") as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (app.SR, 1, 2))


class LegacyQueryTests(unittest.TestCase):
    def test_raw_text(self):
        text, params = app.parse_request("my%20super%20text")
        self.assertEqual((text, params), ("my super text", {}))

    def test_named_text_with_voice(self):
        text, params = app.parse_request("text=Hello+there&voice=mira&seed=3")
        self.assertEqual(text, "Hello there")
        self.assertEqual(params, {"voice": "mira", "seed": "3"})

    def test_mixed_query_keeps_unknown_parts_as_text(self):
        text, params = app.parse_request("Hello+there&voice=mira&rate=1.3")
        self.assertEqual(text, "Hello there")
        self.assertEqual(params, {"voice": "mira", "rate": "1.3"})

    def test_filename_slug(self):
        self.assertEqual(app.filename_for("Hello, world!"), "Hello_world.wav")
        self.assertEqual(app.filename_for("!!!"), "speech.wav")


if __name__ == "__main__":
    unittest.main()
