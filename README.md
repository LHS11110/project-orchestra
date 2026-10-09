# Project Orchestra

Agora 인프라의 구조, 순차 기동, 장애 실험, 노드별 부하 시험을 관리하는 저장소입니다. 실제 요청 처리나 데이터 저장에 참여하는 서버가 아니라 운영·검증 도구입니다.

- [개발용·배포용 실행](docs/STARTUP.md): 노드별 순차 기동, 준비 상태 확인, FE 모드 전환.
- [인프라 아키텍처와 역할](docs/ARCHITECTURE.md): 저장소별 책임, 통신 경로, 메모리 계층, 호스트 동기화, TLS, 현재 병목.
- [카오스 실험](docs/CHAOS.md): 단일 노드 장애, 제한된 무작위 장애, 복구 절차와 검증 기준.
- [검증 기록](docs/VALIDATION.md): 격리 시험 결과와 현재 실행 배포의 차이.
- [Locust 부하 시험](docs/LOAD_TESTING.md): 노드별 프로필, 인증·인증서 설정, 보고서와 자원 측정.

## 전체 앱 실행

```sh
./scripts/start-dev.sh --tls-dir /path/to/certificates
./scripts/start-deploy.sh --initialize --tls-dir /path/to/certificates
# 설정이 준비된 후에는 인증서 경로 옵션 생략 가능
./scripts/start-dev.sh --plan
./scripts/start-deploy.sh --check
```

개발용은 Vite, 배포용은 Nginx 정적 FE를 사용합니다. 저장소 → 저장소 브로커 → Spring → C++ → Phoenix → FE → Nginx 순서로 준비 상태를 기다리며 시작합니다. [실행 옵션·초기화·배포 범위](docs/STARTUP.md)를 확인하세요.

## 준비

네 Agora 저장소와 Orchestra를 같은 상위 디렉터리에 둡니다. 인증서는 해당 저장소의 안내에 따라 직접 준비합니다. 개발용·배포용 실행 스크립트는 기존 Compose를 순차 기동하고, 선택된 경우 기존 DB 초기화 도구를 호출합니다.

```text
workspace/
├── project-agora-FE/
├── project-agora-BE/
├── project-agora-DB/
├── project-agora-Wall/
└── project-orchestra/
```

Python 3.11 이상과 Docker CLI/Compose가 필요합니다. Locust의 SQL 프로필은 Microsoft ODBC Driver 18을 필요로 하므로 제공된 Docker 이미지 사용을 권장합니다. 호스트에서 카오스 명령을 실행하며, 부하 테스트 컨테이너에는 Docker 소켓을 제공하지 않습니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp config/orchestra.example.json config/orchestra.json
cp .env.example .env
chmod 600 .env
mkdir -p certificates results
```

`config/orchestra.json`을 **격리된 시험 환경**의 실제 컨테이너 이름, Compose 프로젝트명, 네트워크, 캔버스 ID에 맞게 수정합니다. 예시는 현재 Agora 소스의 기본 배치를 설명하며, 실행 중인 배포와 같다는 보장은 없습니다. `environment` 값만 `lab`으로 바꾸어도 배포가 격리되는 것은 아닙니다. Docker context 또한 확인해야 합니다.

`.env`에는 시험 계정과 공개 CA 경로를 설정하고, `certificates/`에는 공개 CA만 넣습니다. 개인키, JWT 서명키, 실제 사용자 토큰은 커밋하지 않습니다. 설정·인증서·결과는 기본적으로 Git에서 제외됩니다.

```sh
docker context show
.venv/bin/orchestra inventory
.venv/bin/orchestra plan --node cpp --fault pause --duration 10
```

`inventory`는 읽기 전용 검사, `plan`은 Docker 호출도 하지 않는 계획 출력입니다. 대상과 복구 조건을 확인한 뒤에만 장애를 실행합니다.

```sh
.venv/bin/orchestra run --node cpp --fault pause --duration 10
.venv/bin/orchestra monkey --nodes cpp,phoenix --fault pause --duration 5 --iterations 3 --seed 42
```

부하 테스트는 HTTPS/WSS 및 TLS 저장소 프로토콜을 사용하며, HTTP 관리 화면이나 평문 분산 Locust 포트를 열지 않습니다.

```sh
./scripts/run-load.sh GatewayUser --users 5 --spawn-rate 1 --run-time 30s
./scripts/run-load.sh CanvasUser --users 10 --spawn-rate 2 --run-time 1m
```

스크립트는 Docker 이미지를 빌드하고 CSV/HTML 보고서를 `results/`로 내보냅니다. 기본 시험은 읽기 전용입니다. 장애 실험과 부하 시험을 같은 격리 환경에서 병행하고, [검증 기준](docs/CHAOS.md)을 함께 확인해야 서비스 복원력을 판단할 수 있습니다.

## 개발 검증

```sh
.venv/bin/python -m pip install -e '.[load]'
.venv/bin/python -m unittest discover -s tests -v
# Docker를 사용할 수 있는 격리된 환경에서만 실행; 전용 임시 컨테이너를 생성·삭제합니다.
.venv/bin/python tests/docker_lab.py
docker build -t project-orchestra:local .
.venv/bin/python tests/container_load_lab.py
```

Apache 2.0 라이선스입니다. 이 저장소의 `monkey`는 Docker 환경용 자체 장애 도구이며 Netflix Chaos Monkey를 설치하거나 실행하는 기능은 아닙니다.
