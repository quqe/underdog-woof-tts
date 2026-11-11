#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Ilia Okhrimenko
# SPDX-License-Identifier: MIT
# End-to-end smoke test against a running container. Usage: ./scripts/smoke_test.sh [base_url]
set -euo pipefail
BASE="${1:-http://localhost:3005}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

check() { printf '  %-46s %s\n' "$1" "$2"; }

echo "smoke test: $BASE"

code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/health")
check "/health" "$code"

curl -sf -o "$TMP/page.html" -D "$TMP/page.h" "$BASE/"
check "GET /" "$(grep -i '^content-type' "$TMP/page.h" | tr -d '\r')"
grep -q "<audio" "$TMP/page.html" || { echo "  index page has no <audio> element"; exit 1; }
check "  index page markup" "$(wc -c < "$TMP/page.html") bytes, has <audio>"

curl -sf -o "$TMP/raw.wav" -D "$TMP/raw.h" "$BASE/tts?my%20super%20text"
check "GET /tts?my%20super%20text" "$(grep -i '^content-type' "$TMP/raw.h" | tr -d '\r')"

curl -sf -o "$TMP/named.wav" -D "$TMP/named.h" "$BASE/tts?text=Please+call+me+back+at+415-555-0199.&voice=juno&seed=1"
check "GET /tts?text=...&voice=juno" "$(grep -i '^content-type' "$TMP/named.h" | tr -d '\r')"

curl -sf -o "$TMP/mira.wav" "$BASE/tts?Hello%20from%20Mira!&voice=mira"
check "GET /tts?...&voice=mira" "ok"

for f in raw named mira; do
    size=$(stat -c %s "$TMP/$f.wav")
    head=$(head -c 4 "$TMP/$f.wav")
    [ "$head" = "RIFF" ] || { echo "  $f.wav is not a RIFF/WAV file"; exit 1; }
    check "  $f.wav" "${size} bytes, valid WAV header"
done

# rate: 1.3x must produce a valid WAV that is 1.3x shorter, and keep 24 kHz mono s16
curl -sf -o "$TMP/base.wav" "$BASE/tts?Speed%20check%20for%20the%20rate%20parameter."
curl -sf -o "$TMP/fast.wav" -D "$TMP/fast.h" "$BASE/tts?Speed%20check%20for%20the%20rate%20parameter.&rate=1.3"
check "GET /tts?...&rate=1.3" "$(grep -i '^x-rate' "$TMP/fast.h" | tr -d '\r')"

if command -v python3 >/dev/null; then
    python3 - "$TMP/base.wav" "$TMP/fast.wav" <<'PY'
import sys, wave

def info(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate(), w.getframerate(), w.getnchannels(), w.getsampwidth()

base, fast = info(sys.argv[1]), info(sys.argv[2])
ratio = base[0] / fast[0]
assert fast[1:] == (24000, 1, 2), f"unexpected format {fast[1:]}"
assert abs(ratio - 1.3) < 0.06, f"rate=1.3 gave ratio {ratio:.3f}"
print(f"  rate=1.3 duration {base[0]:.2f}s -> {fast[0]:.2f}s (x{ratio:.2f}), 24 kHz mono s16")
PY
fi

for bad in "" "?&voice=zzz" "?&&text=" "?&rate=0.1" "?&rate=4.1" "?&rate=abc" ; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/tts$bad")
    check "/tts$bad" "$code"
done

# --- OpenAI-compatible API ---------------------------------------------------
curl -sf -o "$TMP/models.json" -D "$TMP/models.h" "$BASE/v1/models"
check "GET /v1/models" "$(grep -i '^content-type' "$TMP/models.h" | tr -d '\r')"
grep -q '"object": "list"' "$TMP/models.json" || { echo "  /v1/models is not an OpenAI list"; exit 1; }
grep -q '"id": "underdog-woof"' "$TMP/models.json" || { echo "  /v1/models has no underdog-woof"; exit 1; }
grep -q '"id": "tts-1"' "$TMP/models.json" || { echo "  /v1/models has no tts-1 alias"; exit 1; }

post_speech() {  # <local file> <json body>
    curl -sf -o "$TMP/$1" -D "$TMP/$1.h" \
        -H 'Content-Type: application/json' -d "$2" "$BASE/v1/audio/speech"
    check "POST /v1/audio/speech -> $1" "$(grep -i '^content-type' "$TMP/$1.h" | tr -d '\r')"
}

post_speech v1.wav '{"input":"OpenAI compatibility check.","voice":"nova","response_format":"wav"}'
head -c 4 "$TMP/v1.wav" | grep -q RIFF || { echo "  v1.wav is not a WAV file"; exit 1; }
grep -qi '^x-voice: mira' "$TMP/v1.wav.h" || { echo "  voice nova did not map to mira"; exit 1; }

post_speech v1.mp3 '{"model":"tts-1","input":"MP3 check.","response_format":"mp3"}'
[ "$(stat -c %s "$TMP/v1.mp3")" -gt 1000 ] || { echo "  v1.mp3 is suspiciously small"; exit 1; }

post_speech v1.pcm '{"input":"PCM check.","response_format":"pcm"}'
if head -c 4 "$TMP/v1.pcm" | grep -q RIFF; then
    echo "  v1.pcm still has a RIFF header"; exit 1
fi
[ $(( $(stat -c %s "$TMP/v1.pcm") % 2 )) -eq 0 ] || { echo "  v1.pcm is not 16-bit aligned"; exit 1; }

post_speech v1.flac '{"input":"FLAC check.","response_format":"flac"}'
[ "$(head -c 4 "$TMP/v1.flac")" = "fLaC" ] || { echo "  v1.flac has no fLaC marker"; exit 1; }

post_speech v1.opus '{"input":"Opus check.","response_format":"opus"}'
[ "$(head -c 4 "$TMP/v1.opus")" = "OggS" ] || { echo "  v1.opus is not an Ogg container"; exit 1; }

post_speech v1.aac '{"input":"AAC check.","response_format":"aac"}'
[ "$(od -An -tx1 -N1 "$TMP/v1.aac" | tr -d ' \n')" = "ff" ] || { echo "  v1.aac has no ADTS sync word"; exit 1; }

# speed: same chained atempo path as &rate=, must shorten the audio
post_speech v1base.wav '{"input":"Speed check through the OpenAI endpoint.","response_format":"wav"}'
post_speech v1fast.wav '{"input":"Speed check through the OpenAI endpoint.","response_format":"wav","speed":1.3}'

if command -v python3 >/dev/null; then
    python3 - "$TMP/v1base.wav" "$TMP/v1fast.wav" <<'PY'
import sys, wave

def duration(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()

base, fast = duration(sys.argv[1]), duration(sys.argv[2])
ratio = base / fast
assert abs(ratio - 1.3) < 0.06, f"speed=1.3 gave ratio {ratio:.3f}"
print(f"  /v1 speed=1.3 duration {base:.2f}s -> {fast:.2f}s (x{ratio:.2f})")
PY
fi

expect_v1_error() {  # <label> <expected status> <json body>
    code=$(curl -s -o "$TMP/v1err.json" -w '%{http_code}' \
        -H 'Content-Type: application/json' -d "$3" "$BASE/v1/audio/speech")
    [ "$code" = "$2" ] || { echo "  $1: expected HTTP $2, got $code"; exit 1; }
    grep -q '"message"' "$TMP/v1err.json" || { echo "  $1: error body has no message"; exit 1; }
    check "POST /v1/audio/speech $1" "$code"
}

expect_v1_error "unknown voice" 400 '{"input":"Hello","voice":"zzz"}'
expect_v1_error "unknown format" 400 '{"input":"Hello","response_format":"ogg"}'
expect_v1_error "speed out of range" 400 '{"input":"Hello","speed":0.1}'
expect_v1_error "missing input" 400 '{"voice":"alloy"}'
expect_v1_error "unknown model" 404 '{"input":"Hello","model":"nope"}'
expect_v1_error "malformed JSON" 400 '{"input":'

code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/v1/audio/speech")
[ "$code" = "405" ] || { echo "  GET /v1/audio/speech: expected 405, got $code"; exit 1; }
check "GET /v1/audio/speech" "$code"

echo "all checks passed"
