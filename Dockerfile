# syntax=docker/dockerfile:1

# SPDX-FileCopyrightText: 2026 Ilia Okhrimenko
# SPDX-License-Identifier: MIT

# --- stage 1: downloadable assets -------------------------------------------
# Both downloads are pinned and checksum-verified, so the build is reproducible
# and the repo does not have to carry the 40 MB weights.
#
# Every step is its own RUN: if the network blocks one host, the failing command
# is obvious in the build log. Each host can be overridden, for example:
#   docker compose build --build-arg HF_ENDPOINT=https://hf-mirror.com
#   docker compose build --build-arg FFMPEG_URL=<mirror-url> --build-arg FFMPEG_SHA=<sha256>
FROM python:3.13-slim AS assets

ARG TARGETARCH=amd64

# Static ffmpeg, mirrored on GitHub releases so the build survives hosts that block
# johnvansickle.com. Both the tag and the per-arch checksum are pinned.
ARG FFMPEG_STATIC_TAG=b6.1.1
ARG FFMPEG_URL=""
ARG FFMPEG_SHA=""

# Model weights: fetched from the author's Hugging Face repo at a pinned revision.
ARG HF_ENDPOINT=https://huggingface.co
ARG WOOF_REPO=ConwayResearch/Underdog-Woof
ARG WOOF_REVISION=be702960539777fc3f698e54335276a8391b00f9
ARG WOOF_SHA256=d895fbbe84b8f4f461e3e94ab48b1164353b1988ce8d365f5aa8263d0de1203e

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl xz-utils; \
    rm -rf /var/lib/apt/lists/*

# Optional local overrides: put a static ffmpeg binary in vendor/ffmpeg and/or the
# weights in vendor/woof_int8.npz to skip the downloads entirely (blocked networks).
COPY vendor/ /vendor/

RUN set -eux; \
    if [ -s /vendor/ffmpeg ]; then \
        echo "using vendored ffmpeg from vendor/ffmpeg"; \
        install -m 0755 /vendor/ffmpeg /usr/local/bin/ffmpeg; \
    else \
        case "$TARGETARCH" in \
            amd64) \
                DEFAULT_URL="https://github.com/eugeneware/ffmpeg-static/releases/download/${FFMPEG_STATIC_TAG}/ffmpeg-linux-x64"; \
                DEFAULT_SHA="e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99" \
                ;; \
            arm64) \
                DEFAULT_URL="https://github.com/eugeneware/ffmpeg-static/releases/download/${FFMPEG_STATIC_TAG}/ffmpeg-linux-arm64"; \
                DEFAULT_SHA="6bb182d0d75d23028db82e9e4f723ca69b853d055698486e6984ddb2c06fb8ce" \
                ;; \
            *) echo "no pinned static ffmpeg for TARGETARCH=$TARGETARCH" >&2; exit 1 ;; \
        esac; \
        URL="${FFMPEG_URL:-$DEFAULT_URL}"; \
        SHA="${FFMPEG_SHA:-$DEFAULT_SHA}"; \
        echo "downloading ffmpeg from $URL"; \
        curl -fL --retry 5 --retry-delay 3 --retry-all-errors --connect-timeout 30 "$URL" -o /tmp/ff.bin; \
        echo "$SHA  /tmp/ff.bin" | sha256sum -c -; \
        if tar -tJf /tmp/ff.bin >/dev/null 2>&1; then \
            echo "archive detected, extracting"; \
            tar -xJf /tmp/ff.bin -C /tmp; \
            FFMPEG_BIN="$(find /tmp -maxdepth 4 -type f -name ffmpeg | head -n1)"; \
            test -n "$FFMPEG_BIN"; \
            install -m 0755 "$FFMPEG_BIN" /usr/local/bin/ffmpeg; \
            rm -rf /tmp/ffmpeg-*static*; \
        else \
            echo "single binary detected"; \
            install -m 0755 /tmp/ff.bin /usr/local/bin/ffmpeg; \
        fi; \
        rm -f /tmp/ff.bin; \
    fi; \
    ffmpeg -hide_banner -version | head -n1

RUN set -eux; \
    if [ -s /vendor/woof_int8.npz ]; then \
        echo "using vendored weights from vendor/woof_int8.npz"; \
        cp /vendor/woof_int8.npz /woof_int8.npz; \
    else \
        URL="${HF_ENDPOINT}/${WOOF_REPO}/resolve/${WOOF_REVISION}/woof_int8.npz"; \
        echo "downloading weights from $URL"; \
        curl -fL --retry 5 --retry-delay 3 --retry-all-errors --connect-timeout 30 "$URL" -o /woof_int8.npz; \
    fi; \
    echo "$WOOF_SHA256  /woof_int8.npz" | sha256sum -c -

# --- stage 2: runtime -------------------------------------------------------
FROM python:3.13-slim

LABEL org.opencontainers.image.title="underdog-woof-tts" \
      org.opencontainers.image.description="Minimal Docker HTTP API for the Underdog Woof text-to-speech model. Wrapper code is MIT; the model and its weights are Underdog Non-Commercial 1.0." \
      org.opencontainers.image.source="https://github.com/quqe/underdog-woof-tts" \
      org.opencontainers.image.licenses="MIT AND LicenseRef-Underdog-NonCommercial-1.0"

# One BLAS thread: the model is single-threaded by design, extra threads only spin.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    PORT=3005 \
    DEFAULT_VOICE=juno

WORKDIR /app

COPY --from=assets /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --chown=woof:woof --from=assets /woof_int8.npz ./woof_int8.npz
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=woof:woof underdog_woof ./underdog_woof
COPY --chown=woof:woof app.py LICENSE NOTICE ./
COPY --chown=woof:woof LICENSES ./LICENSES
COPY --chown=woof:woof LICENSING.md ./
COPY --chown=woof:woof web ./web

RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin woof
USER woof

EXPOSE 3005

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3005/health', timeout=2).status == 200 else 1)"

CMD ["python", "app.py"]
