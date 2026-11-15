# perfeng worker image — same contract as the native LocalProcess executor:
#   docker run --rm -v <out>:/out -v <project>:/project:ro -e <SECRET_NAMES> perfeng-worker:0.1.0 \
#     --spec /out/spec.json --out /out --project /project
# Used automatically only when a Docker daemon is present and PERF_DOCKER_IMAGE_READY=1.
# Phase 2: the same image runs as a Kubernetes Job / ECS task / Cloud Run Job / ACA Job.
# Build:  docker build -f docker/worker.Dockerfile -t perfeng-worker:0.1.0 .
# NOTE: not exercised by the current eval suite (no Docker on the dev machine); covered by contract only.
FROM mcr.microsoft.com/playwright:v1.65.1-noble
ARG K6_VERSION=v2.3.0
ARG K6_SHA256=39c3117b6af817592dcd0ce4242105c0a7af10948c2a425306f0be8f7a8a8ab1
RUN curl -fsSL -o /tmp/k6.tgz https://github.com/grafana/k6/releases/download/${K6_VERSION}/k6-${K6_VERSION}-linux-amd64.tar.gz \
 && echo "${K6_SHA256}  /tmp/k6.tgz" | sha256sum -c - \
 && tar -xzf /tmp/k6.tgz -C /tmp && mkdir -p /opt/perf/tools/k6/${K6_VERSION} \
 && mv /tmp/k6-${K6_VERSION}-linux-amd64/k6 /opt/perf/tools/k6/${K6_VERSION}/k6 && rm -rf /tmp/k6*
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY engine ./engine
RUN uv sync --frozen --no-dev
ENV PERF_TOOLS_DIR=/opt/perf/tools PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN cp -r engine/src/perfeng/workers/browser /opt/perf/tools/browser && cd /opt/perf/tools/browser && npm ci --no-audit --no-fund
USER pwuser
ENTRYPOINT ["uv", "run", "--frozen", "--no-dev", "perfeng-worker"]
