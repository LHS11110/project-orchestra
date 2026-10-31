# Third-party notices

Project Orchestra code is Apache-2.0 licensed. Direct load-test dependencies are pinned in `requirements.txt`: Locust, websocket-client and pyodbc. Their transitive Python packages retain their own licenses and installed `.dist-info` LICENSE/NOTICE files. The Dockerfile includes this project's LICENSE and this document in `/app/`.

The image also installs Debian libraries, unixODBC, Microsoft ODBC Driver 18 and a Python runtime. Microsoft's driver is under vendor license terms; `ACCEPT_EULA=Y` is acceptance of those terms, not an open-source license. Preserve vendor/system notices and review binary redistribution separately. Database vendor images used by tests are not relicensed by Orchestra.

The repository contains orchestrator/test source, not a complete license/source bundle for every container used during tests. Record exact image digests and installed package versions when preparing a distributed binary release.
