ARG BASE_IMAGE=retracesoftware-test
FROM ${BASE_IMAGE}

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        apt-transport-https \
        ca-certificates \
        curl \
        gnupg \
        unixodbc \
        unixodbc-dev >/dev/null \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update -qq \
    && ACCEPT_EULA=Y apt-get install -y -qq --no-install-recommends msodbcsql18 >/dev/null \
    && rm -rf /var/lib/apt/lists/*
