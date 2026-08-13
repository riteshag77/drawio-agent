# Two runtimes, not one. The diagram compiler shells out to
# `node tools/elk_layout/layout.js` for ELK layout, so a Python-only image
# builds fine and then fails at request time on every corridor and network
# view. Start from Node and add Python, rather than the reverse — the Node
# image pins a known-good V8, and Debian's python3 is sufficient here since
# nothing needs a bleeding-edge interpreter.
FROM node:20-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Virtualenv rather than --break-system-packages: Debian marks its Python as
# externally managed, and a venv keeps app deps off the system interpreter.
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

# Dependencies first so a source-only change reuses this layer.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# elkjs is vendored under tools/elk_layout/node_modules, so there is no npm
# install step — copying the tree is enough.
COPY app ./app
COPY config ./config
COPY static ./static
COPY tools ./tools
COPY run.py ./

# Writable state: SQLite stores and captures live here. Mount a volume over it
# in production — the image's copy is empty and disposable.
RUN mkdir -p /app/data /app/logs

# Non-root. Done after the copies so ownership lands correctly in one layer.
RUN useradd --system --create-home --uid 10001 drawio \
    && chown -R drawio:drawio /app
USER drawio

EXPOSE 8000

# Deliberately NOT `python run.py`: that is the development launcher and
# enables uvicorn's reloader. Serve the ASGI app directly instead.
#
# One worker on purpose. State that must be shared across requests — the LLM
# concurrency pool, the in-memory style cache, the rate-limit counters — is
# per-process, so multiple workers would each keep their own and quietly
# multiply the effective MAX_CONCURRENT_LLM. Scale with replicas behind a
# load balancer, and move the stores off SQLite before doing so.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"
