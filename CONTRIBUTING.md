# Contributing

## Language rule

**Everything in this repository is written in English**: code, comments, docstrings,
documentation, commit messages, issue titles and bodies, pull request descriptions and
review comments. Do not add Cyrillic or any other non-Latin text, including in fixtures and
test data, unless it is a deliberate test case for rejecting such input.

## Ground rules

* Do not commit model weights (`woof_int8.npz`, ~40 MB). They are downloaded at build time
  from Hugging Face at a pinned revision and verified by sha256.
* Keep the public API stable: `GET /tts`, `GET /health`, `GET /v1/models`,
  `POST /v1/audio/speech`, and the `text`, `voice`, `seed`, `rate` parameters. New
  parameters must be optional and default to current behaviour.
* `rate`/`speed` stays within 0.25–4.0. Values outside 0.5–2.0 are split into several
  `atempo` passes and sound noticeably more processed.
* The wrapper code is MIT; `underdog_woof/` and the weights stay under the Underdog
  Non-Commercial License 1.0. Do not relicense upstream files.

## Before pushing

```bash
make unit
docker compose up -d --build
./scripts/smoke_test.sh http://localhost:3005
```

All checks must pass. If you touched the model loading, request handling or tempo code, also
check memory and behaviour with both voices:

```bash
docker stats --no-stream
curl -o /tmp/mira.wav "http://localhost:3005/tts?A%20short%20check.&voice=mira"
```

If you changed `web/index.html`, the optional headless test walks the page through submit,
generating and error states (needs Node and jsdom):

```bash
npm install --no-save jsdom && node tests/ui.test.js
```

## Publishing an image

Every push to `main` builds and pushes `ghcr.io/quqe/underdog-woof-tts` for `linux/amd64`
and `linux/arm64` (`.github/workflows/publish.yml`). For a stable release, tag the commit:

```bash
git tag -a v1.0.0 -m "v1.0.0"
git push origin v1.0.0
```

That publishes `1.0.0`, `1.0` and `latest`. Note that the package must stay public so users
can pull it without `docker login` (Package settings -> Change visibility -> Public).

## Commit messages

English, imperative mood, short subject line under 72 characters, body wrapped at 72 columns
when needed. Example:

```
Add rate parameter backed by static ffmpeg

atempo runs outside the model mutex; output is raw s16le re-wrapped
into a WAV with correct RIFF sizes.
```

## Attribution

Published audio must credit "Underdog Woof" or "underdog.ai". See `LICENSE` and `NOTICE`.
