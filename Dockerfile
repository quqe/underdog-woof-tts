# syntax=docker/dockerfile:1

# SPDX-FileCopyrightText: 2026 Ilia Okhrimenko
# SPDX-License-Identifier: MIT

# --- stage 1: downloadable assets -------------------------------------------
# Both downloads are pinned and checksum-verified, so the build is reproducible
# and the repo does not have to carry the 40 MB weights.
FROM python:3.13-slim AS assets

ARG TARGETARCH=amd64
ARG FFMPEG_VERSION=6.0.1

# Model weights: fetched from the author's Hugging Face repo at a pinned revision.
ARG WOOF_REPO=ConwayResearch/Underdog-Woof
ARG WOOF_REVISION=be702960539777fc3f698e54335276a8391b00f9
ARG WOOF_SHA256=d895fbbe84b8f4f461e3e94ab48b1164353b1988ce8d365f5aa8263d0de1203e

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
 && rm -rf /var/lib/apt/lists/* \
 # static ffmpeg: a single 76 MB binary instead of ~600 MB of apt dependencies
 && case "$TARGETARCH" in \
        amd64) FFMPEG_SHA=28268bf402f1083833ea269331587f60a242848880073be8016501d864bd07a5 ;; \
        arm64) FFMPEG_SHA=7dbd8e2f47bd83de591b9d6ea70e67d32d9aa97e7d47ae402b60c2fe3fd4d0ab ;; \
        *) echo "no pinned static ffmpeg for TARGETARCH=$TARGETARCH" >&2; exit 1 ;; \
    esac \
 && curl -fsSL "https://johnvansickle.com/ffmpeg/old-releases/ffmpeg-${FFMPEG_VERSION}-${TARGETARCH}-static.tar.xz" \
        -o /tmp/ff.tar.xz \
 && echo "$FFMPEG_SHA  /tmp/ff.tar.xz" | sha256sum -c - \
 && tar -xJf /tmp/ff.tar.xz -C /tmp \
 && install -m 0755 "/tmp/ffmpeg-${FFMPEG_VERSION}-${TARGETARCH}-static/ffmpeg" /usr/local/bin/ffmpeg \
 && rm -rf /tmp/ff.tar.xz "/tmp/ffmpeg-${FFMPEG_VERSION}-${TARGETARCH}-static" \
 # model weights
 && curl -fsSL "https://huggingface.co/${WOOF_REPO}/resolve/${WOOF_REVISION}/woof_int8.npz" \
        -o /woof_int8.npz \
 && echo "$WOOF_SHA256  /woof_int8.npz" | sha256sum -c -

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

RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin woof
USER woof

EXPOSE 3005

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3005/health', timeout=2).status == 200 else 1)"

CMD ["python", "app.py"]
