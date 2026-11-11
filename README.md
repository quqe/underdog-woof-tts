# Underdog Woof TTS — Docker HTTP API

A thin HTTP wrapper around [Underdog Woof](https://huggingface.co/ConwayResearch/Underdog-Woof):
a 40 MB English text-to-speech model that runs on numpy, on CPU, without a GPU. The service
exposes a single GET endpoint that returns a ready-to-play WAV file, plus an
OpenAI-compatible `POST /v1/audio/speech` so existing OpenAI TTS clients work without changes.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Model license: Underdog NC 1.0](https://img.shields.io/badge/model%20license-Underdog%20NC%201.0-orange.svg)](LICENSES/LicenseRef-Underdog-NonCommercial-1.0.txt)

## License at a glance

| What | License | Commercial use |
|---|---|---|
| Wrapper code: `app.py`, `Dockerfile`, `docker-compose.yml`, `Makefile`, `scripts/`, docs | [MIT](LICENSE) | **Yes** - copy, modify, sell, embed anywhere |
| Model code (`underdog_woof/`) and the weights | [Underdog Non-Commercial 1.0](LICENSES/LicenseRef-Underdog-NonCommercial-1.0.txt) | **No** - needs a license from Underdog |
| Audio this service generates | Yours, under Underdog Non-Commercial 1.0 | **No** - credit "Underdog Woof" required |

Running this service with the Underdog Woof model **commercially requires a commercial
license** (license@conway.tech) even though the wrapper code is MIT. Full details and an FAQ:
[LICENSING.md](LICENSING.md).

## Quick start

On any machine with Docker and Docker Compose:

```bash
# 1. get the code - clone it...
git clone https://github.com/quqe/underdog-woof-tts.git
cd underdog-woof-tts

# ...or download the archive with curl (no git needed, same files, no history)
curl -L https://github.com/quqe/underdog-woof-tts/archive/refs/heads/main.tar.gz | tar xz
cd underdog-woof-tts-main

# 2. build and start (first build downloads ~140 MB: numpy, ffmpeg, weights)
docker compose up -d --build
docker compose ps            # wait for "Up ... (healthy)", about 20 s

# 3. check it answers
curl -s http://localhost:3005/health
curl -o speech.wav "http://localhost:3005/tts?my%20super%20text"
curl -o fast.wav   "http://localhost:3005/tts?my%20super%20text&rate=1.3"
curl -o mira.wav   "http://localhost:3005/tts?Hello%20there!&voice=mira"
curl -o speech.mp3 -H 'Content-Type: application/json' \
  -d '{"model":"tts-1","voice":"nova","input":"Hello from Underdog Woof."}' \
  http://localhost:3005/v1/audio/speech

# 4. full end-to-end check (needs curl; python3 adds duration assertions)
./scripts/smoke_test.sh http://localhost:3005
```

Any release tag works the same way: `/archive/refs/tags/<tag>.tar.gz` unrolls into
`underdog-woof-tts-<tag>/`.

The service listens on port 3005. `docker compose down` stops it, `docker compose logs -f`
follows the logs, and `restart: unless-stopped` brings it back after a reboot.

The 40.6 MB model weights are **not stored in this repository**: the build downloads them
from Hugging Face at a pinned revision and verifies their sha256. Internet access is only
needed during `docker compose build`; the container itself runs fully offline.

Open <http://localhost:3005/> in a browser for a minimal test page. It submits the form with
`fetch`, shows an elapsed-time indicator while the server synthesizes, then plays the WAV
in place - no page reload. Without JavaScript the plain form still works (it is a normal GET
form). Query parameters prefill it: `/?text=Hello&voice=mira&rate=1.3`.

### Web UI

`GET /` serves `web/index.html`: one file, no frameworks, no external assets, so it works on
an air-gapped server. Behaviour:

* `Ctrl+Enter` (or `Cmd+Enter`) speaks, `Enter` inserts a newline.
* While generating: pulsing indicator, elapsed seconds, disabled button, `Cancel` (aborts the
  wait; the server finishes the sentence it is working on).
* On success: native `<audio controls>` appears in place, with voice, rate, audio length and
  generation time, plus a `Download .wav` link.
* Errors from the API (`text too long`, `unknown voice`, ...) are shown as a red notice and
  the form keeps its contents.
* The page is English-only, like the rest of the repository.

## Build and weights

```
https://huggingface.co/ConwayResearch/Underdog-Woof/resolve/<revision>/woof_int8.npz
```

| Item | Pinned in |
|---|---|
| Weights repository and revision | `ARG WOOF_REPO`, `ARG WOOF_REVISION` in `Dockerfile` |
| Weights checksum | `ARG WOOF_SHA256` (`d895fbbe…`) |
| Static ffmpeg release and checksums | `ARG FFMPEG_STATIC_TAG`, per-architecture `DEFAULT_SHA` |

Both can be overridden at build time:

```bash
docker compose build --build-arg WOOF_REVISION=<commit-sha> --build-arg WOOF_SHA256=<sha256>
```

For an offline build, vendor the weights:

```bash
python scripts/fetch_model.py          # writes woof_int8.npz into the project
# then: remove the woof_int8.npz line from .dockerignore
# and in the Dockerfile replace COPY --from=assets /woof_int8.npz ./woof_int8.npz
#                                            with COPY woof_int8.npz ./
```

### Build troubleshooting

The download stage is split into individual steps, so the failing command is always the last
one in the log. To build only that stage and see exactly what broke:

```bash
docker build --progress plain --no-cache --target assets -t woof-assets . 2>&1 | tail -40
```

Check reachability of every host the build needs:

```bash
for u in https://deb.debian.org https://pypi.org/simple/ \
         https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/ffmpeg-linux-x64 \
         https://huggingface.co/ConwayResearch/Underdog-Woof/resolve/be702960539777fc3f698e54335276a8391b00f9/woof_int8.npz; do
  printf '%-110s ' "$u"; curl -sIL --max-time 20 "$u" | awk '/^HTTP/{c=$2} END{print c}'
done
```

If Hugging Face is unreachable, use a mirror:

```bash
docker compose build --build-arg HF_ENDPOINT=https://hf-mirror.com
```

If GitHub is unreachable but `johnvansickle.com` works, point the build there instead. The
step accepts either a tarball or a single binary, but the checksum must match - compute it on
the host first:

```bash
curl -L -o /tmp/ff.tar.xz "https://johnvansickle.com/ffmpeg/old-releases/ffmpeg-6.0.1-amd64-static.tar.xz"
sha256sum /tmp/ff.tar.xz
docker compose build --build-arg FFMPEG_URL="https://johnvansickle.com/ffmpeg/old-releases/ffmpeg-6.0.1-amd64-static.tar.xz" --build-arg FFMPEG_SHA="<sha256>"
```

If no external host is reachable at all, vendor the files. Download them on a machine with
internet and copy them into the build context:

```bash
# on your laptop
curl -L -o ff "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/ffmpeg-linux-x64"
cp ff ffmpeg && chmod +x ffmpeg
scp ffmpeg user@server:/path/to/underdog-woof-tts/vendor/

# on the server: weights too, if Hugging Face is blocked
# (from the HF repo: woof_int8.npz, sha256 d895fbbe84b8f4f461e3e94ab48b1164353b1988ce8d365f5aa8263d0de1203e)
docker compose build --no-cache
```

The build logs `using vendored ffmpeg from vendor/ffmpeg` when it takes this path. See
[vendor/README.md](vendor/README.md).

Behind an HTTP proxy, the Docker daemon needs its own proxy configuration
(`/etc/systemd/system/docker.service.d/http-proxy.conf`), not just shell variables.

## API

### `GET /tts?<text>`

Everything after `?` is the text. Returns `audio/wav` (24 kHz, mono, 16-bit).

```bash
curl -o out.wav "http://localhost:3005/tts?my%20super%20text"
```

### `GET /tts?text=<text>&voice=&seed=&rate=`

Same endpoint with explicit parameters. A mixed form such as `/tts?my%20text&voice=mira`
also works: anything that is not `text`/`voice`/`seed`/`rate` is treated as speech text.

| Parameter | Default | Description |
|---|---|---|
| `text` | — | Text to speak. If omitted, the whole query string is used |
| `voice` | `juno` | `juno` or `mira` |
| `seed` | `0` | Integer. Same seed + same text gives identical audio |
| `rate` | `1` | Speech rate, 0.25–4.0. `1.3` is 30% faster, pitch preserved |

Response headers: `Content-Type: audio/wav`, `Content-Disposition` with a filename,
`X-Voice`, `X-Rate`, `X-Audio-Seconds`, `X-Generation-Seconds`, `X-Seed`.

Errors are JSON with status 400/404/405/413/503, for example:

```json
{ "error": "empty text", "usage": "GET /tts?Hello%20there or GET /tts?text=Hello+there" }
```

### `POST /v1/audio/speech` (OpenAI-compatible)

Drop-in replacement for OpenAI's speech endpoint: point an OpenAI SDK at
`http://localhost:3005/v1`, and the model name can stay `tts-1`.

```bash
curl -o out.mp3 -H 'Content-Type: application/json' \
  -d '{"model":"tts-1","voice":"nova","input":"Hello there."}' \
  http://localhost:3005/v1/audio/speech
```

| Field | Default | Description |
|---|---|---|
| `model` | `underdog-woof` | `underdog-woof`, `underdog-woof-tts`, `woof`, `tts-1`, `tts-1-hd`, `gpt-4o-mini-tts`; unknown values return 404 |
| `input` | — | Text to speak, same `MAX_CHARS` limit as `/tts` (2000 by default) |
| `voice` | `alloy` | OpenAI names are aliases for the two model voices, see below |
| `response_format` | `mp3` | `wav`, `pcm`, `mp3`, `opus`, `aac`, `flac` |
| `speed` | `1` | 0.25–4.0, same atempo engine as `/tts&rate=` |
| `instructions` | — | Accepted and ignored (the model has no style control) |
| `seed` | `0` | Extension: integer, same seed + same text gives identical audio |

The model has two voices, so the OpenAI names are mapped onto them:

| OpenAI voice | Model voice |
|---|---|
| `alloy`, `echo`, `onyx` | `juno` |
| `fable`, `nova`, `shimmer` | `mira` |

`juno` and `mira` are accepted directly as well. The resolved model voice is logged and
echoed in the `X-Voice` response header.

Response formats:

| `response_format` | `Content-Type` | Notes |
|---|---|---|
| `wav` | `audio/wav` | 24 kHz mono 16-bit |
| `pcm` | `audio/pcm` | raw 24 kHz mono 16-bit, no header |
| `mp3` | `audio/mpeg` | 128 kbit/s |
| `opus` | `audio/opus` | Ogg container, 64 kbit/s |
| `aac` | `audio/aac` | ADTS, 128 kbit/s |
| `flac` | `audio/flac` | lossless |

Every format except `wav` and `pcm` is encoded with ffmpeg, a few tens of milliseconds per
file. If an image was built without ffmpeg, compressed formats return 503; a request that
does not set `response_format` falls back to WAV.

The official OpenAI Python SDK works as usual:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:3005/v1", api_key="not-needed")
with client.audio.speech.with_streaming_response.create(
    model="tts-1", voice="nova", input="Hello from Underdog Woof.", response_format="mp3"
) as response:
    response.stream_to_file("hello.mp3")
```

Errors use the OpenAI envelope:

```json
{
  "error": {
    "message": "unknown voice 'zzz'; available: alloy, echo, fable, onyx, nova, shimmer, juno, mira",
    "type": "invalid_request_error",
    "param": "voice",
    "code": null
  }
}
```

`404` = unknown model, `413` = body over 256 KB, `415` = not `application/json`,
`500` = ffmpeg failure, `503` = busy or ffmpeg missing. Errors never include partial audio.

### `GET /v1/models`

OpenAI-compatible model list (abridged); every listed id is an alias for the same model:

```json
{
  "object": "list",
  "data": [
    { "id": "underdog-woof", "object": "model", "created": 1762819200, "owned_by": "underdog-woof" },
    { "id": "tts-1", "object": "model", "created": 1762819200, "owned_by": "underdog-woof" }
  ]
}
```

### `GET /health`

```json
{
  "status": "ok",
  "voices": ["juno", "mira"],
  "loaded": ["juno"],
  "max_voices_loaded": 2,
  "max_chars": 2000,
  "ffmpeg": true,
  "rate_range": [0.25, 4.0],
  "openai_compatible": {
    "speech": "POST /v1/audio/speech",
    "models": "GET /v1/models",
    "voices": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
    "formats": ["wav", "pcm", "mp3", "opus", "aac", "flac"]
  },
  "license": {
    "code": "MIT",
    "model": "Underdog-NonCommercial-1.0",
    "attribution": "Voice by Underdog Woof",
    "details": "https://github.com/quqe/underdog-woof-tts/blob/main/LICENSING.md"
  },
  "uptime_s": 12.3,
  "requests": 4,
  "audio_seconds": 31.7
}
```

## Speech rate (`rate` / `speed`)

The model itself has no speed control, so `&rate=1.3` (or OpenAI's `"speed": 1.3`)
time-stretches the finished WAV with ffmpeg's `atempo` filter. That is WSOLA: duration
changes, pitch does not.

| `rate` | 12.0 s of audio becomes |
|---|---|
| 0.5 | 24.0 s |
| 1 | 12.0 s |
| 1.25 | 9.6 s |
| 1.3 | 9.23 s |
| 1.4 | 8.57 s |
| 2 | 6.0 s |

Cost: about 0.02 s and 20 MB of transient memory per file, outside the model mutex.
Artefacts are practically inaudible up to 1.4x; beyond that a metallic timbre appears.
The range is 0.25–4.0. A single `atempo` pass only handles 0.5–2.0, so extreme values are
split into several passes (0.25x = two 0.5x passes, 4x = two 2x passes) and sound
noticeably more processed; values outside the range return 400.

## Resources

Measured on a 2-core / 4-thread i5-1135G7 with `--cpus=1`:

| State | Process RSS |
|---|---|
| One voice loaded (Juno) | ~225 MB |
| Peak on a long text (~2 min of audio) | ~320 MB |
| Both voices loaded (Juno + Mira) | ~610 MB |

* Synthesis is **single-threaded**: the model uses exactly one core and BLAS threads do not
  help (the image sets `OMP_NUM_THREADS=1`, otherwise numpy just burns CPU).
* One worker gives ~2x realtime on Juno (12 s of audio in ~6 s), ~4x on medium-length text
  without quality retries.
* Requests inside a container are serialized by a mutex (the model is not thread-safe), so
  extra vCPUs are wasted. Scale horizontally instead:

```bash
docker run -d -p 3006:3005 --memory=512m --cpus=1 underdog-woof-tts:1.0
```

Recommended limits:

| Scenario | vCPU | RAM |
|---|---|---|
| Juno only | 1 | 512 MB |
| Juno + Mira at the same time | 1 | 1 GB |
| Tight mode (voice switching, `MAX_VOICES_LOADED=1`) | 1 | 512 MB |

512 MB is the floor: at 256 MB the container is OOM-killed on the first request.

Image size is 456 MB: `python:3.13-slim` + numpy + 40 MB of weights + 75 MB of static
ffmpeg. Without tempo support, drop the `assets` stage and the `COPY --from=assets` lines
from the `Dockerfile` and you get 348 MB.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `PORT` | `3005` | Port inside the container |
| `DEFAULT_VOICE` | `juno` | Voice used when `voice` is not given |
| `PRECACHE_VOICES` | `juno` | Voices to load at startup (comma-separated) |
| `MAX_VOICES_LOADED` | `2` | How many voices stay in memory; extra ones are evicted LRU |
| `MAX_CHARS` | `2000` | Maximum text length (413 above it) |
| `QUEUE_TIMEOUT` | `120` | Seconds to wait for the model before returning 503 |
| `FFMPEG_BIN` | `ffmpeg` | ffmpeg binary path (used for `rate != 1`) |
| `TEMPO_TIMEOUT` | `60` | ffmpeg call timeout, seconds |
| `WOOF_WEIGHTS` | `./woof_int8.npz` | Path to the weights file |

## Project layout

```
app.py                 HTTP server (stdlib http.server, no frameworks)
web/index.html         single-file web UI (inline CSS + JS, no external assets)
underdog_woof/         model code from the upstream HF repository (unmodified)
Dockerfile             multi-stage: downloads ffmpeg + weights, python:3.13-slim, non-root
docker-compose.yml     port 3005, resource limits, read-only rootfs
LICENSE                MIT, applies to the wrapper code
LICENSES/              upstream Underdog Non-Commercial License 1.0 (verbatim)
LICENSING.md           license FAQ: what you may and may not do
REUSE.toml             machine-readable license mapping for every file
scripts/smoke_test.sh  end-to-end checks against a running container
scripts/fetch_model.py download weights and model code for local runs
tests/test_app.py      unit tests for validation/ffmpeg/WAV helpers (stdlib unittest)
tests/ui.test.js       optional headless check of the web page (jsdom)
```

The weights (`woof_int8.npz`) are not committed: they arrive at build time from Hugging Face,
which keeps this repository small and gives the model author download statistics.

Static ffmpeg is a johnvansickle.com build mirrored to GitHub releases
(`eugeneware/ffmpeg-static`, tag pinned) - one statically linked binary, checksums pinned per
architecture. Only the binary itself lands in the image: `ffprobe`, `ffplay` and man pages are
dropped, so the image does not grow by the ~600 MB that `apt install ffmpeg` would pull in.

Refresh the model locally:

```bash
pip install huggingface_hub
python scripts/fetch_model.py
docker compose up -d --build
```

## Tests

```bash
make unit                                        # 28 unit tests: numpy only, no model, no network
./scripts/smoke_test.sh http://localhost:3005    # end-to-end; needs a running service (or: make test)
```

The smoke test covers the plain GET API, every `response_format`, `speed`/`rate`, and the
OpenAI error envelope. It needs `curl`; `python3` adds duration assertions.

## Limitations

* **English only.** The API returns 400 if the text contains no Latin letters.
* **The OpenAI endpoint has two real voices.** `alloy`/`echo`/`onyx` map to Juno, `fable`/
  `nova`/`shimmer` to Mira; `instructions` is accepted and ignored. `seed` is an extension
  (same seed + same text = identical audio).
* **Underdog Non-Commercial 1.0 license** — personal, research and non-commercial use;
  published audio must credit "Underdog Woof" / underdog.ai. Commercial use requires a
  license from Underdog. See `LICENSE` and `NOTICE`.
* Mira is noticeably slower than Juno on short phrases: the built-in quality check redraws
  bad takes up to three times, which dominates on short text.
* `rate` is post-processing of finished audio, not model control: intonation and pause
  placement stay the same, only playback speed changes.

## Licensing

| Part | License |
|---|---|
| Wrapper code: `app.py`, `Dockerfile`, `docker-compose.yml`, `Makefile`, `scripts/*`, docs | MIT, see `LICENSE` |
| Model code in `underdog_woof/` and the weights `woof_int8.npz` | Underdog Non-Commercial 1.0, see `LICENSES/` |

The short version: reuse the wrapper freely, including commercially. The model does not come
with those rights - its audio may be published only with credit ("Underdog Woof" /
"underdog.ai"), and any commercial use needs a license from Underdog
(license@conway.tech). Do not train, fine-tune or distill other speech models on its output.
See [LICENSING.md](LICENSING.md) for the full FAQ.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: everything in this repository —
code, comments, documentation, commit messages, issues and pull requests — is written in
**English only**, and model weights are never committed.
