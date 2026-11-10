# Underdog Woof TTS — Docker HTTP API

A thin HTTP wrapper around [Underdog Woof](https://huggingface.co/ConwayResearch/Underdog-Woof):
a 40 MB English text-to-speech model that runs on numpy, on CPU, without a GPU. The service
exposes a single GET endpoint that returns a ready-to-play WAV file.

## Quick start

```bash
docker compose up -d --build      # or: make run

curl -o speech.wav "http://localhost:3005/tts?my%20super%20text"
curl -o fast.wav   "http://localhost:3005/tts?my%20super%20text&rate=1.3"
curl -o mira.wav   "http://localhost:3005/tts?Hello%20there!&voice=mira"
curl               "http://localhost:3005/health"
```

The 40.6 MB model weights are **not stored in this repository**: the build downloads them
from Hugging Face at a pinned revision and verifies their sha256. Internet access is only
needed during `docker compose build`; the container itself runs fully offline.

Open <http://localhost:3005/> in a browser for a minimal test form.

## Build and weights

```
https://huggingface.co/ConwayResearch/Underdog-Woof/resolve/<revision>/woof_int8.npz
```

| Item | Pinned in |
|---|---|
| Weights repository and revision | `ARG WOOF_REPO`, `ARG WOOF_REVISION` in `Dockerfile` |
| Weights checksum | `ARG WOOF_SHA256` (`d895fbbe…`) |
| Static ffmpeg version and checksum | `ARG FFMPEG_VERSION`, `FFMPEG_SHA` per architecture |

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
| `rate` | `1` | Speech rate, 0.5–2.0. `1.3` is 30% faster, pitch preserved |

Response headers: `Content-Type: audio/wav`, `Content-Disposition` with a filename,
`X-Voice`, `X-Rate`, `X-Audio-Seconds`, `X-Generation-Seconds`, `X-Seed`.

Errors are JSON with status 400/404/405/413/503, for example:

```json
{ "error": "empty text", "usage": "GET /tts?Hello%20there or GET /tts?text=Hello+there" }
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
  "rate_range": [0.5, 2.0],
  "uptime_s": 12.3,
  "requests": 4,
  "audio_seconds": 31.7
}
```

## Speech rate (`rate`)

The model itself has no speed control, so `&rate=1.3` time-stretches the finished WAV with
ffmpeg's `atempo` filter. That is WSOLA: duration changes, pitch does not.

| `rate` | 12.0 s of audio becomes |
|---|---|
| 1 | 12.0 s |
| 1.25 | 9.6 s |
| 1.3 | 9.23 s |
| 1.4 | 8.57 s |

Cost: about 0.02 s and 20 MB of transient memory per file, outside the model mutex.
Artefacts are practically inaudible up to 1.4x; beyond that a metallic timbre appears.
Range is 0.5–2.0 (the limit of a single `atempo` pass); values outside it return 400.

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
underdog_woof/         model code from the upstream HF repository (unmodified)
Dockerfile             multi-stage: downloads ffmpeg + weights, python:3.13-slim, non-root
docker-compose.yml     port 3005, resource limits, read-only rootfs
scripts/smoke_test.sh  end-to-end checks against a running container
scripts/fetch_model.py download weights and model code for local runs
```

The weights (`woof_int8.npz`) are not committed: they arrive at build time from Hugging Face,
which keeps this repository small and gives the model author download statistics.

Static ffmpeg comes from `johnvansickle.com`, version 6.0.1 pinned and sha256-verified
(builds for `amd64` and `arm64`). Only the binary itself lands in the image: `ffprobe`,
`ffplay` and man pages are dropped, so the image does not grow by the ~600 MB that
`apt install ffmpeg` would pull in.

Refresh the model locally:

```bash
pip install huggingface_hub
python scripts/fetch_model.py
docker compose up -d --build
```

## Limitations

* **English only.** The API returns 400 if the text contains no Latin letters.
* **Underdog Non-Commercial 1.0 license** — personal, research and non-commercial use;
  published audio must credit "Underdog Woof" / underdog.ai. Commercial use requires a
  license from Underdog. See `LICENSE` and `NOTICE`.
* Mira is noticeably slower than Juno on short phrases: the built-in quality check redraws
  bad takes up to three times, which dominates on short text.
* `rate` is post-processing of finished audio, not model control: intonation and pause
  placement stay the same, only playback speed changes.

## Licensing

This repository mixes two different licenses:

| Part | License |
|---|---|
| Wrapper code: `app.py`, `Dockerfile`, `docker-compose.yml`, `Makefile`, `scripts/*` | MIT (see `LICENSE-MIT`) |
| Model code in `underdog_woof/` and the weights `woof_int8.npz` | Underdog Non-Commercial 1.0 (see `LICENSE`) |

In short: you may reuse the wrapper freely, but the model and anything it generates remain
under Underdog's non-commercial terms. Published audio must credit "Underdog Woof" or
"underdog.ai"; commercial use needs a commercial license from Underdog
(license@conway.tech). Do not train, fine-tune or distill other speech models on its output.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: everything in this repository —
code, comments, documentation, commit messages, issues and pull requests — is written in
**English only**, and model weights are never committed.
