ARG BASE_IMAGE=invalid.invalid/rythme:required
FROM ${BASE_IMAGE}

ARG BASE_IMAGE_ID
ARG RYTHME_VERSION
ARG SOURCE_REVISION
ARG RSCTF_GAME_ID
ARG RSCTF_CHALLENGE_ID

ENV RYTHME_BEATMAP_PROXY=false \
    RYTHME_ENROLLMENT_DURATION=60s \
    RYTHME_READY_DURATION=30s \
    RYTHME_SUBMISSION_DURATION=12s \
    RYTHME_RESULTS_DURATION=20s \
    RYTHME_CROWN_THRESHOLD=2100000 \
    RYTHME_RAW_SCORE_FLOOR=700000 \
    RSCTF_BASE_URL=http://rsctf-control:8080 \
    RSCTF_GAME_ID=${RSCTF_GAME_ID} \
    RSCTF_CHALLENGE_ID=${RSCTF_CHALLENGE_ID}

LABEL org.opencontainers.image.title="Rythme RSCTF health target"
LABEL org.opencontainers.image.description="Event-scoped RSCTF target for the durable Rythme arena"
LABEL org.opencontainers.image.version="${RYTHME_VERSION}"
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
LABEL org.opencontainers.image.source="https://github.com/TCP1P/intechfest-2026/tree/main/events/main-event/challenges/Koth/Reverse/rythme"
LABEL tf.1pc.rsctf.rythme.base="${BASE_IMAGE_ID}"
