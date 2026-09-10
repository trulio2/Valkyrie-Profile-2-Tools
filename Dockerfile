# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517 AS build

ARG PYTHON_VERSION=3.11.16
ARG PYTHON_RELEASE=31661435402
ARG PYINSTALLER_VERSION=6.22.2
ARG VERSION=dev-local

ENV DEBIAN_FRONTEND=noninteractive \
    AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache \
    PATH=/opt/hostedtoolcache/Python/3.11.16/x64/bin:${PATH} \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
    binutils ca-certificates curl tcl8.6 tk8.6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/actions-python
RUN curl --fail --location --retry 3 \
    "https://github.com/actions/python-versions/releases/download/${PYTHON_VERSION}-${PYTHON_RELEASE}/python-${PYTHON_VERSION}-linux-24.04-x64.tar.gz" \
    --output python.tar.gz \
    && tar -xzf python.tar.gz \
    && bash setup.sh \
    && python -c "import sys, tkinter; assert sys.version_info[:3] == (3, 11, 16); tkinter.Tcl()"

RUN python -m pip install "pyinstaller==${PYINSTALLER_VERSION}"

WORKDIR /src
COPY . .

RUN python -m unittest discover -s tests -q \
    && for pack in translations/[!_]*/; do \
    python vp2_translate.py check-pack "$pack"; \
    done \
    && pyinstaller data/vp2_tools.spec \
    --workpath workspace/internal/build \
    --clean --noconfirm \
    && ./dist/ValkyrieProfile2-Tools --self-check

RUN set -eux; \
    mkdir roundtrip; \
    name="ValkyrieProfile2-Tools-${VERSION}-linux-x64"; \
    mv "dist/ValkyrieProfile2-Tools" "dist/${name}"; \
    chmod +x "dist/${name}"; \
    tar -czf "${name}.tar.gz" -C dist "${name}"; \
    tar -xzf "${name}.tar.gz" -C roundtrip; \
    test -x "roundtrip/${name}"; \
    "roundtrip/${name}" --self-check

FROM scratch AS artifact
COPY --from=build /src/ValkyrieProfile2-*.tar.gz /
