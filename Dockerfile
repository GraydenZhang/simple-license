# syntax=docker/dockerfile:1
FROM --platform=linux/amd64 python:3.12-slim-bookworm AS builder
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ patchelf && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY requirements-production.txt requirements-build.txt ./
RUN pip install --no-cache-dir -r requirements-production.txt -r requirements-build.txt
COPY app ./app
COPY scripts ./scripts
COPY school_permit.py ./
COPY build_support ./build_support
RUN python build_support/check_public_keys.py
RUN python -m nuitka --mode=standalone --output-dir=/build --output-filename=school-permit \
    --include-package=app --include-package=scripts \
    --include-module=sqlalchemy.dialects.postgresql.psycopg \
    --include-module=sqlalchemy.dialects.sqlite.pysqlite \
    --include-package=psycopg_binary --include-package-data=ua_parser_builtins \
    --include-package-data=tzdata --include-package-data=certifi \
    --include-data-dir=app/templates=app/templates \
    --include-data-dir=app/static=app/static \
    --include-data-dir=app/verify_static=app/verify_static \
    --include-data-files=app/license_public_keys.json=app/license_public_keys.json \
    --nofollow-import-to=pytest,unittest,developer_tools \
    --assume-yes-for-downloads school_permit.py
RUN python build_support/check_distribution.py /build/school_permit.dist

FROM --platform=linux/amd64 debian:bookworm-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk libstdc++6 libgcc-s1 libffi8 libssl3 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --no-create-home --uid 10001 campus \
    && mkdir -p /var/lib/campus-permits && chown campus:campus /var/lib/campus-permits
WORKDIR /opt/school-permit
COPY --from=builder /build/school_permit.dist/ ./
USER 10001:10001
ENV DATA_DIR=/var/lib/campus-permits APPROVAL_HOST=0.0.0.0 VERIFY_HOST=0.0.0.0
EXPOSE 15001 15002
ENTRYPOINT ["/opt/school-permit/school-permit"]
CMD ["serve"]
