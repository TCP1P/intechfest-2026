FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PORT_LISTENER=1094
ENV TIMEOUT_DEVICE=180

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        build-essential \
        g++ \
        libunicorn-dev \
        python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY main.py reporter.py runner.cpp start.sh /app/

RUN g++ -O2 -std=c++20 -Wall -Wextra -Wpedantic \
        -o /app/babel-runner /app/runner.cpp -lunicorn \
    && useradd --system --uid 1337 --no-create-home babel \
    && chmod 0555 /app/babel-runner /app/start.sh \
    && chmod 0444 /app/main.py /app/reporter.py

USER babel
EXPOSE 1094
HEALTHCHECK --interval=10s --timeout=4s --start-period=8s --retries=3 \
  CMD ["python3", "/app/main.py", "--health"]
CMD ["/app/start.sh"]
