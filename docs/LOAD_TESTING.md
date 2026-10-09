# Locust 노드별 부하 시험

## TLS와 접근 설정

`config/orchestra.example.json`을 복사한 후 시험 배포 주소, 캔버스 ID, item ID, Redis JSON key를 수정합니다. `.env.example`을 `.env`로 복사하고 `chmod 600 .env`를 적용합니다. URL에는 토큰·비밀번호를 넣지 않습니다. HTTP/WS endpoint는 설정 검사에서 거절합니다.

공개 CA 파일은 `certificates/services-ca.pem`, `es-ca.crt`, `redis-ca.crt`, `sql-ca.crt`에 배치하거나 env 경로를 바꿉니다. Docker env 경로는 `/run/certs/...`입니다. 개인키나 JWT 서명키를 제공하지 않습니다. ODBC SQL 검증을 위해 시험 이미지 시작 시 공개 SQL CA를 시스템 신뢰 저장소에 추가한 후 UID 10001로 시험 프로세스를 실행합니다. DB의 자체 생성 인증서 대신 SAN이 맞는 신뢰 가능한 인증서를 사용해야 합니다.

전용 시험 사용자에게 필요한 캔버스 접근 권한을 부여합니다. `ORCHESTRA_TEST_BEARER_TOKEN`은 이 사용자의 유효한 로그인 access token입니다. 캔버스 access JWT는 Locust가 Spring에 매번 새로 요청하며, 사용자 토큰·IP 정책이 있는 배포에서는 동일한 부하 생성 경로에서 로그인해 토큰을 준비합니다. 키를 공유하거나 기존 사용자 IP 검증을 끄지 않습니다. 짧은 시험 중에도 토큰 만료에 주의하십시오.

C++ 내부 API 프로필은 배포와 같은 `CPP_INTERNAL_API_TOKEN`이 필요합니다. 저장소 프로필은 해당 저장소의 전용 읽기 권한 계정을 사용합니다. Sentinel은 데이터 계정과 별도의 reader 자격 증명입니다. 보고서에 드라이버 예외 원문·JWT URL·연결 문자열을 출력하지 않습니다.

## 프로필

| Locust 클래스 | 경로와 실제 작업 | 주로 측정하는 노드 / 한계 |
|---|---|---|
| GatewayUser | Nginx `/`, `/api/auth/health` | Nginx + FE + Spring; 정적 페이지 문서만, 브라우저 JS 실행 안 함 |
| FrontendUser | FE 직접 HTTPS `/` | Vite/프런트 HTTP 서버, 렌더링 FPS 측정 아님 |
| SpringUser | Spring HTTPS `POST /api/auth/me` (token JSON) | 인증된 Spring 사용자 조회 |
| CppUser | Nginx 내부 서버 ID 경로의 canvas/count | Wall 내부 라우팅 + C++; 가벼운 질의 |
| PhoenixUser | Phoenix HTTPS `/health` | 브로커 HTTPS 상태; 별도의 WSS 흐름은 CanvasUser |
| CanvasUser | Spring access → Nginx/Phoenix WSS → 초기 snapshot → C++ CRUD read와 request ID ACK | 실제 캔버스 연결·읽기 경로; 쓰기·브라우저 호스트 선출·P2P 미디어는 시험 안 함 |
| MssqlUser | Wall SQL 별칭 → TDS/TLS → `SELECT 1` | SQL 연결·왕복 지연; 실제 복잡한 SQL 처리량과 다름 |
| ElasticsearchUser | Wall ES 별칭 → HTTPS index `_count` | ES 인증·검색 count 경로 |
| RedisPrimaryUser | Wall Sentinel 탐색 → 현재 primary `PING/ROLE` | 선출·라우팅·Redis 상태; 기존 연결 실패 후 재탐색 |
| RedisJsonUser | primary 확인 + `JSON.GET` | 전용 Redis 캔버스 문서 읽기; key가 없으면 실패 |
| RedisReplica1User / RedisReplica2User | 고정 Redis 슬롯 `PING/ROLE` | 물리 슬롯별 상태; 선출 후 역할은 바뀔 수 있음 |
| SentinelUser | 세 Sentinel에 순환 primary 조회 | 노드별 통계 `sentinel-1/2/3.discovery` |

storage-broker는 독립 업무 API가 없는 Layer 4 프록시입니다. SQL·ES·Redis·Sentinel 프로필을 병행하여 프록시 부하를 생성하고 `orchestra.metrics`에서 해당 컨테이너의 CPU·메모리·네트워크를 확인합니다. 저장소로 직접 접속해 프록시를 제외하는 프로필은 제공하지 않습니다.

Redis 프로필은 인증서의 원래 노드 IP와 Wall dial 주소를 분리합니다. Sentinel이 미등록 primary를 반환하면 실패하며 직접 접속하지 않습니다. `RedisJsonUser`의 `document_key` 기본값은 `canvas:1`입니다. 시험 캔버스가 로드되어 Redis 문서가 존재하는지 먼저 확인하십시오.

## 실행과 결과

```sh
./scripts/run-load.sh GatewayUser --users 5 --spawn-rate 1 --run-time 30s
./scripts/run-load.sh SpringUser --users 20 --spawn-rate 2 --run-time 2m
./scripts/run-load.sh CanvasUser --users 10 --spawn-rate 2 --run-time 1m
./scripts/run-load.sh MssqlUser --users 16 --spawn-rate 2 --run-time 1m
./scripts/run-load.sh ElasticsearchUser --users 10 --spawn-rate 1 --run-time 1m
./scripts/run-load.sh RedisPrimaryUser --users 10 --spawn-rate 2 --run-time 1m
./scripts/run-load.sh SentinelUser --users 3 --spawn-rate 1 --run-time 1m
```

초기값은 5 users, 초당 1 user 증가, 30초입니다. 작은 부하로 기준선을 얻은 후 점진적으로 증가시킵니다. 사용자 수가 RPS와 동일하지는 않으며 기본 대기 시간은 0.1–0.5초입니다. 기본 HTTP/WSS 시간 제한은 5초입니다. 데이터셋·캐시 온도·연결 재사용·호스트 수를 기록해야 결과를 재현할 수 있습니다.

출력은 `results/`의 CSV 및 HTML입니다. 컨테이너는 전용 Docker volume에 보고서를 쓰고 종료 후 호스트로 내보냅니다. 실행마다 고유 접두사를 사용합니다. 평균보다 p50/p95/p99, 성공 RPS, 실패 유형, 재연결·복구 시간을 비교하십시오. 보고서는 응답 본문을 수집하지 않습니다.

기본 종료 기준은 실패율 1% 이하와 p95 1000ms 이하입니다. `ORCHESTRA_MAX_FAILURE_RATIO`, `ORCHESTRA_MAX_P95_MS`로 시험 목적에 맞게 변경합니다. 요청이 하나도 실행되지 않았거나 기준을 초과하면 종료 코드가 실패입니다. 이 기본값은 운영 SLO의 확정값이 아닙니다. 카오스 중 오류가 예상되는 시험은 구간별 결과를 따로 해석합니다.

## 사전 probe와 자원 관측

```sh
docker compose run --rm --build load python -m orchestra.probe --profile cpp
docker compose run --rm load python -m orchestra.probe --profile redis
docker compose run --rm load python -m orchestra.probe --profile mssql
.venv/bin/python -m orchestra.metrics --duration 120 --interval 2 --output results/resources.jsonl
```

probe는 한 번의 읽기 질의로 TLS·인증·라우팅을 확인합니다. 지원 profile은 gateway, frontend, spring, cpp, phoenix, mssql, redis, elasticsearch입니다. 자원 수집은 호스트의 Docker CLI를 사용하며 환경변수를 출력하지 않습니다. Docker CPU/Mem/NetIO/BlockIO/PIDs는 coarse 지표로, 애플리케이션 큐·SQL 풀·Redis 복제 지연·ES GC를 대신하지 않습니다.

## SQL·프로토콜·확장 주의점

SQL은 ODBC Driver 18과 `Encrypt=yes`, `TrustServerCertificate=no`, 원래 서버 호스트명 검증을 사용합니다. SQL Server 2022 Linux는 TLS 1.2이며 다른 프로필의 TLS 1.3 강제를 이 구간에 적용하면 연결되지 않습니다. Driver 의존성은 Docker 이미지에 설치됩니다. CA 신뢰를 끄는 옵션을 쓰지 않습니다.

ODBC의 native blocking I/O는 gevent thread pool로 넘깁니다. `ORCHESTRA_ODBC_THREADS` 기본 16, 범위 1–128입니다. 사용자 수가 thread 수를 초과하면 부하 생성기 내부 대기까지 응답 시간에 포함됩니다. 무리하게 thread 수를 높이기 전에 생성기 CPU·연결 수를 점검하십시오. RESP와 WSS는 제한된 메시지 크기와 request ID 상관관계를 검증하고 실패 시 연결을 재수립합니다.

기본 실행은 headless이며 Locust 평문 HTTP UI나 master/worker의 기본 ZeroMQ TCP 포트를 공개하지 않습니다. 큰 부하는 여러 독립 시험 컨테이너를 동일한 설정으로 실행하고 보고서를 각각 보관하는 방식으로 나눌 수 있습니다. 다중 생성기의 CSV 단순 평균은 올바른 전체 percentile이 아니므로 원시 히스토그램·집계 설계가 필요합니다. 중앙 분산 Locust를 추가하려면 별도의 검증된 TLS 터널과 접근 제어를 먼저 설계해야 합니다.

프로필은 읽기 전용입니다. CanvasUser는 실제 업그레이드·스냅샷·ACK 경로를 이용하지만 JS/Pixi/Automerge/WebRTC를 실행하지 않습니다. 도구 자체의 TLS mock 시험은 요청·응답 및 인증서 검증을 검사하며, 실배포 처리량·데이터 내구성을 입증하지 않습니다.

## 공식 참고 자료

- [Locust 다른 프로토콜과 blocking I/O](https://docs.locust.io/en/stable/testing-other-systems.html)
- [Locust request event 확장](https://docs.locust.io/en/stable/extending-locust.html)
- [Locust 설정](https://docs.locust.io/en/stable/configuration.html), [분산 실행](https://docs.locust.io/en/stable/running-distributed.html)
- [ODBC Driver 18 설치](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server?view=sql-server-ver17)
- [ODBC 암호화·인증서 옵션](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/connection-string-keywords-and-data-source-names-dsns?view=sql-server-ver17)
