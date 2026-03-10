# --------- Builder Stage ---------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# Set environment variables for uv
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Builder stage working directory
WORKDIR /app

# 1. Copy ONLY the requirements file (for best layer caching)
# If only code changes, this layer is skipped.
COPY requirements.txt /app/requirements.txt

# 2. Create the virtual environment
# We explicitly create the venv first at /app/.venv.
RUN uv venv /app/.venv

# 3. Install dependencies from requirements.txt
# We use the GLOBAL 'uv' executable, which automatically targets the newly created /app/.venv.
# This fixes the "not found" error you were getting.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r requirements.txt


# 3b. Install dependencies for CadQuery (Separate Venv)
COPY requirements-cad.txt /app/requirements-cad.txt
RUN uv venv /app/.venv-cad
RUN --mount=type=cache,target=/root/.cache/uv \
    VIRTUAL_ENV=/app/.venv-cad uv pip install -r requirements-cad.txt

# 4. Copy the rest of the project source code
COPY . /app

# --------- Final Stage ---------
FROM python:3.12-slim-bookworm

# Install system dependencies required for CadQuery/OCP
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libxrender1 \
    build-essential \
    libncurses-dev \
    libreadline-dev \
    openscad curl jq \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user for security
# Using fixed GID and UID for consistency
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --shell /bin/bash --create-home app

# Copy the virtual environment from the builder stage
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/.venv-cad /app/.venv-cad

# Copy the application source code
# Note: The source code is copied from /app (builder) to /code (final)
COPY --from=builder --chown=app:app /app /code

# Ensure the virtual environment's executables are in the PATH
ENV PATH="/app/.venv/bin:$PATH"

# Create generated files directory and set ownership
RUN mkdir -p /app/generated_files && chown -R app:app /app/generated_files


# Switch to the non-root user and set the working directory
USER app
WORKDIR /code

# -------- Entry Point --------
# This CMD is run from /code, allowing 'src.app.main:app' to be resolved.
# NOTE: Render requires port 10000 for web services. Change 8000 to 10000 if deploying there.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--reload", "--reload-exclude", "generated_files/*"]
