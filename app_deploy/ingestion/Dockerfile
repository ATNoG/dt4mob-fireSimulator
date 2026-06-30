# ── Stage 1: Build ForeFire binary ──────────────────────────────────────────
FROM ubuntu:22.04 AS forefire-builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake \
    g++ \
    make \
    git \
    libnetcdf-dev \
    libnetcdf-c++4-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth=1 https://github.com/forefireAPI/forefire.git /forefire

WORKDIR /forefire

RUN sh cmake-build.sh

RUN cmake --install build --prefix /forefire/install

RUN test -f /forefire/install/bin/forefire && echo "ForeFire binary OK"

# ── Stage 2: Python ingestion runtime ────────────────────────────────────────
FROM python:3.11-slim

# System deps: GDAL, PostgreSQL client libs, NetCDF runtime, compiler
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin \
    libgdal-dev \
    python3-gdal \
    libpq-dev \
    libnetcdf-dev \
    libnetcdf-c++4-dev \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy ForeFire binary and shared libraries from install prefix
COPY --from=forefire-builder /forefire/install/bin/ /usr/local/bin/
COPY --from=forefire-builder /forefire/install/lib/ /usr/local/lib/
RUN ldconfig

# Set GDAL/PROJ environment variables for the system GDAL
ENV GDAL_DATA=/usr/share/gdal
ENV PROJ_LIB=/usr/share/proj
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal
ENV FOREFIRE_BIN=/usr/local/bin/forefire

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

#CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
CMD ["python", "main.py"]
