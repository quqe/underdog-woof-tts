#!/usr/bin/env bash
# End-to-end smoke test against a running container. Usage: ./scripts/smoke_test.sh [base_url]
set -euo pipefail
BASE="${1:-http://localhost:3005}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

check() { printf '  %-46s %s\n' "$1" "$2"; }

echo "smoke test: $BASE"

code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/health")
check "/health" "$code"

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

for bad in "" "?&voice=zzz" "?&&text=" "?&rate=0.1" "?&rate=abc" ; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/tts$bad")
    check "/tts$bad" "$code"
done

echo "all checks passed"
