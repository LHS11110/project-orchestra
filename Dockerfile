FROM python:3.13-slim-bookworm
WORKDIR /app
# ODBC is a real SQL driver dependency; HTTPS package repositories only.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl unixodbc libgssapi-krb5-2 util-linux \
 && curl --fail --silent --show-error --location https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb -o /tmp/microsoft.deb \
 && dpkg -i /tmp/microsoft.deb \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
 && rm -rf /var/lib/apt/lists/* /tmp/microsoft.deb \
 && groupadd --gid 10001 orchestra && useradd --uid 10001 --gid 10001 --create-home orchestra
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml locustfile.py ./
COPY orchestra ./orchestra
COPY scripts/container-entrypoint.sh /usr/local/bin/orchestra-entrypoint
RUN pip install --no-cache-dir --no-deps . && chmod 755 /usr/local/bin/orchestra-entrypoint
ENTRYPOINT ["orchestra-entrypoint"]
CMD ["locust", "-f", "locustfile.py", "--headless", "--users", "1", "--spawn-rate", "1", "--run-time", "30s", "GatewayUser"]
