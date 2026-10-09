# 개발용·배포용 순차 실행

Orchestra에서 네 Agora 저장소의 기존 Compose와 초기화 도구를 순차적으로 실행합니다. 설정·서비스 정의는 원래 저장소에 유지하며 인증서는 직접 준비합니다. 스크립트 자체는 Python 표준 라이브러리만 사용하므로 Orchestra 패키지나 Locust 설치가 필요하지 않습니다.

## 실행

```sh
# 현재 설정으로 개발용 실행
./scripts/start-dev.sh
# 처음 clone한 환경: 제공한 인증서로 기존 설정 도구 실행 후 개발용 기동
./scripts/start-dev.sh --tls-dir /path/to/certificates

# 배포용: 기존 DB와 정적 FE 빌드로 실행
./scripts/start-deploy.sh
# 처음 구성하는 배포: SQL 스키마·계정, Redis ACL·검색 인덱스, ES 계정·인덱스 초기화 포함
./scripts/start-deploy.sh --initialize --tls-dir /path/to/certificates --public-origin https://your-domain:8443
```

저장소는 같은 상위 디렉터리에 둡니다. 다른 위치에서는 `--workspace /path/to/workspace`로 네 저장소가 있는 상위 경로를 지정합니다. Python 실행 파일은 `ORCHESTRA_PYTHON`으로 지정할 수 있습니다. Docker Engine/Compose, Bash, curl, OpenSSL이 필요하며 C++ submodule은 BE의 안내대로 `git submodule update --init --recursive`로 준비합니다.

`--tls-dir`는 FE의 `setup-projects.py`를 호출하여 FE·BE·DB·Wall 설정을 준비합니다. 인증서를 생성하지 않으며, 기존 설정 도구가 서비스 계정·연결 설정을 동기화합니다. 생략하면 기존 각 저장소의 `.env`와 인증서 경로를 그대로 사용합니다. 기존 Nginx healthcheck가 `https://localhost`를 검증하므로 제공하는 게이트웨이 인증서 SAN에는 실제 도메인과 `localhost`도 포함되어야 합니다. Orchestra의 부하 시험용 `.env`·inventory는 기동 설정으로 사용하지 않습니다.

## 모드 차이

| 항목 | 개발용 start-dev.sh | 배포용 start-deploy.sh |
|---|---|---|
| FE | Vite 컨테이너, 소스 bind mount, HMR | 일회성 빌드·release export, Nginx 정적 파일 |
| Nginx 모드 | development | production |
| DB 초기화 | 기본 실행, `--skip-initialize`로 생략 | 기본 생략, 최초 배포에 `--initialize` |
| 이미지 | 기본 빌드 | 기본 빌드; 사전 준비한 이미지에 `--no-build` |
| 실행 중 Vite | 시작·재생성 | 정적 게이트웨이 정상화 후 관리되는 Vite만 중지 |
| TLS | 기존 CA·호스트명 검증 유지 | 기존 CA·호스트명 검증 유지 |

두 모드 모두 **현재 기본 단일 Docker 엔진 Compose 배치**를 기동합니다. 배포용은 정적 FE 배포 모드이며 SQL Availability Group, 다중 호스트 Redis, ES 클러스터를 새로 만드는 기능은 아닙니다. DB 검증에는 이 배치에 맞는 `development` 프로필을 사용하며 TLS를 해제하지 않습니다. 다중 호스트 운영 구성은 [DB 운영 문서](../../project-agora-DB/ops/README.md)의 별도 배포·검증 절차를 따릅니다. Nginx의 기본 공개 바인딩은 기존 Compose의 loopback HTTPS 8443이며 스크립트가 외부 공개 범위를 확대하지 않습니다.

## 실행 순서

1. 각 저장소 환경 파일 권한·TLS·인증서·Compose, BE와 Wall의 JWT 및 내부 토큰 일치 여부를 검사합니다.
2. Elasticsearch 저장소 권한을 준비합니다.
3. MS SQL → Elasticsearch → Redis primary → replica 1 → replica 2 → Sentinel 1 → 2 → 3을 하나씩 시작하고 healthcheck를 기다립니다. TLS 준비 컨테이너는 해당 노드의 Compose 의존성으로 실행됩니다.
4. 초기화가 선택되었다면 SQL 스키마·계정 → Redis ACL·검색 인덱스·SQL 등록 → ES 계정·인덱스를 적용합니다.
5. Wall storage-broker를 시작하고 `agora-services`를 준비합니다.
6. BE 이미지를 빌드하고 서비스 TLS volume을 준비한 뒤 Spring → C++를 각각 시작·확인합니다. C++의 Spring healthy 의존성을 지킵니다.
7. E5 검색 worker를 빌드·시작하고 최초 메타데이터 projection 동기화를 기다립니다. Nori 플러그인이 포함된 ES 이미지를 사용하며 DB 초기화를 생략해도 검색 alias와 앱 계정 권한은 확인·적용합니다. 그 뒤 BE HTTPS와 ES 로그 계정 인증을 확인하고 Phoenix TLS volume 및 브로커를 준비합니다.
8. 개발용은 필요할 때 Nginx를 **생성만 하여** Compose 소유 `agora-web` 네트워크를 준비한 뒤 Vite를 시작·확인합니다. 배포용은 FE 정적 release를 export합니다.
9. Nginx를 해당 모드로 재생성하고 healthcheck·`nginx -t`를 확인합니다. 배포용은 이 단계 이후 관리되는 Vite를 중지합니다.
10. Nginx 내부에서 CA 검증을 사용해 `/`와 `/api/auth/health`의 HTTPS 성공을 확인합니다.

Redis Insight HTTP 관리 UI, Locust 부하·카오스 도구, 별도 백업 cron 작업은 자동 시작하지 않습니다. ES 초기화를 선택하면 기존 스크립트가 ILM·SLM 보존 및 snapshot 예약 정책도 적용합니다. 저장소 노드는 기본 Compose 구성과 이름을 사용하며 이미 실행 중이면 Compose가 현재 정의와 맞춰 처리합니다. 애플리케이션·브로커·Nginx·개발 FE는 최신 이미지·설정·TLS를 읽도록 재생성합니다. 무중단 롤링 배포 방식은 아닙니다.

## 계획·검사·시간 제한

```sh
./scripts/start-dev.sh --plan
./scripts/start-deploy.sh --plan --initialize --no-build
./scripts/start-dev.sh --check
./scripts/start-deploy.sh --check --no-build
./scripts/start-dev.sh --wait-timeout 600
```

`--plan`은 Docker 명령이나 파일 변경 없이 순서를 출력합니다. `--check`는 기존 설정을 읽고 Docker/Compose 입력 검사를 수행하며 서비스를 시작하지 않습니다. 동시 기동 방지를 위한 짧은 lock만 사용합니다. `--check`와 `--tls-dir`는 함께 사용할 수 없습니다.

노드별 `--wait-timeout` 기본값은 300초이고 범위는 30–1800초입니다. 전체 실행 시간은 이미지 빌드·다운로드와 각 단계의 합으로 더 길 수 있습니다. 기존 storage-broker helper의 대기 시간은 60초이며 개별 명령의 최대 실행 시간은 1시간입니다. `--no-build`에서는 필요한 애플리케이션 이미지를 사전에 검사하고 정적 exporter의 이미지 존재도 export 직전에 확인합니다. 실패하면 그 단계에서 즉시 중단하고 이후 노드를 시작하지 않습니다.

부하 시험 설정에는 개발 FE 노드가 포함되므로 배포 모드에서 Vite가 중지된 상태를 해당 inventory와 맞춰 수정해야 합니다. 기동 완료 검사는 TLS·컨테이너 및 일부 업무 경로 검사이며 캔버스 쓰기 내구성·호스트 선출·실제 부하 결과를 보장하지 않습니다.

## 실패·재실행

완료된 서비스와 데이터 volume은 남겨 원인 분석과 재실행을 할 수 있도록 합니다. 자동 `down`, volume 삭제, 전체 스택 되돌리기를 수행하지 않습니다. 오류에 표시된 단계의 설정·로그를 확인하고 동일 명령을 다시 실행하십시오. 개발용 초기화는 재실행 가능한 기존 DB 스크립트를 사용하며 전체 DB·volume을 비우지는 않지만 기존 초기화 스크립트의 스키마 migration, 계정·권한 변경과 검색 인덱스 갱신은 적용됩니다.

동일 Orchestra 저장소에서 개발용·배포용 동시 기동은 `results/.startup-lock`으로 차단합니다. 다른 clone이나 수동 Compose는 이 lock을 공유하지 않습니다. 정상 종료·일반 예외·Ctrl+C에서는 lock을 제거합니다. SIGKILL 또는 호스트 종료 후 남은 lock은 파일에 적힌 PID와 실제 실행 여부를 확인한 뒤 삭제하십시오. 다른 실행자가 동작 중인 lock을 삭제하면 안 됩니다.

검색 worker의 최초 빌드는 고정 revision 다국어 E5 모델을 다운로드합니다. 기본 CPU 2개·메모리 2 GiB 제한이며 문서 수에 따라 최초 동기화 시간이 길어질 수 있습니다. `--wait-timeout`을 조정하세요. 기존 Spring TLS volume을 재사용하므로 추가 인증서를 자동 생성하지 않습니다. `--no-build`는 `project-agora-search:local`과 `project-agora-elasticsearch:8.19.22-nori` 이미지도 필요합니다. [검색 운영 안내](../../project-agora-DB/elasticsearch/SEARCH.md)를 참고하세요.
