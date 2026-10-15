# 검증 기록

2026-10-10에 아래 검증을 수행했습니다. 운영 배포의 처리량이나 장애 내구성 결과가 아닌 도구 자체의 격리 검증입니다.

| 검증 | 결과 | 범위 |
|---|---|---|
| Python unittest 23개 | 통과 | 허용 목록·복구·lock·중단·재생성 거절·RESP 제한·설정·TLS·Locust |
| 실제 Docker 장애 4종 | 통과 | 전용 임시 컨테이너 pause, stop, 네트워크 단절, CPU quota 감소 및 복구 |
| Locust HTTPS/WSS mock | 통과 | 7 HTTP/캔버스 프로필, 초기 스냅샷, 다른 request ID 무시, 올바른 ACK |
| 실제 TLS RESP mock | 통과 | 인증·Sentinel primary 탐색·PING·ROLE·JSON.GET; 잘못된 호스트명 거절 |
| TLS 1.2 endpoint 거절 | 통과 | HTTPS 프로필의 TLS 1.3 강제 |
| Docker 이미지 빌드·실행 | 통과 | ODBC Driver 18 설치, UID 10001 전환, 공개 CA 신뢰 설정 |
| Docker Locust mock | 통과 | 외부 배포와 분리된 네트워크, WSS ACK, CSV·HTML 및 volume 쓰기 |
| 쉘 문법·문서 로컬 링크 | 통과 | 실행 스크립트와 참조 경로 |

임시 네트워크·컨테이너·volume은 검증 후 제거했습니다. 기존 Agora 컨테이너에는 장애·실제 부하를 주입하거나 재배포하지 않았습니다. SQL의 실제 쿼리·전체 서비스 데이터 보존·브라우저 호스트 선출은 이 격리 시험의 검증 대상이 아닙니다.

## 현재 배포와 소스의 차이

같은 날짜에 예시 inventory를 기존 실행 컨테이너와 읽기 전용으로 비교했습니다. Phoenix(`agora-broker`)와 storage-broker(`agora-storage-broker`)는 실행 중이지 않았고, Nginx·Spring·C++ 네트워크는 새로운 `agora-services` 구조와 달랐습니다. 따라서 예시 설정을 복사하는 것만으로 현재 실행 환경에서 모든 프로필이 성공하지는 않습니다.

이 결과는 시점별 관측이며 이후 상태를 보장하지 않습니다. 먼저 격리 배포에 최신 Wall·BE 설정과 인증서를 적용하고, 실제 배치로 inventory를 수정하여 probe가 성공한 뒤 부하·장애 시험을 실행하십시오.

## 재현

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/docker_lab.py
docker build -t project-orchestra:local .
.venv/bin/python tests/container_load_lab.py
```

GitHub Actions는 단위·TLS 시험, 임시 Docker 장애 시험 및 이미지 기반 Locust smoke test를 실행합니다. CI에 운영 토큰·인증서·Docker socket 경로를 전달하지 않습니다. GitHub runner의 Docker CLI로 해당 runner의 임시 테스트 자원만 다룹니다.

## 순차 기동 도구 검증

개발용·배포용 기동 스크립트를 추가한 뒤 전체 unittest 32개가 통과했습니다. 추가 9개는 저장소별 순서·health 대기, TLS volume 단계, Spring→C++ 의존성, 개발 FE 시작 순서, 정적 export 후 Nginx 전환, 실패 시 후속 단계 차단·lock 해제, 계획의 무변경 실행을 검사합니다.

현재 설정에서 `start-dev.sh --check`와 `start-deploy.sh --check`가 통과했고 두 모드의 `--plan`과 쉘 문법도 확인했습니다. 실제 Agora 컨테이너를 기동·재시작하는 명령은 이 검증에서 실행하지 않았습니다.

## 실제 개발 배포 검증 — 2026-10-13

동일 호스트의 기존 인증서·환경 파일·데이터 volume을 사용하여 Orchestra `./scripts/start-dev.sh`를 실제 실행했습니다. 기본 DB 초기화, 이미지 빌드, 저장소·Spring·C++·검색 worker·Phoenix·Vite·Nginx의 26개 순차 단계를 완료했습니다. `./scripts/start-dev.sh --skip-initialize --no-build`의 23개 재기동 단계도 완료했습니다. 새 호스트의 빈 DB를 구성하는 시험은 수행하지 않았습니다.

공개 HTTPS 경로에서 홈페이지, API health, `/@vite/client`, `/src/main.jsx`가 200을 반환했습니다. Vite HMR WebSocket은 101로 연결됐으며, 임시 소스 모듈을 변경했을 때 해당 모듈의 HMR update 이벤트를 수신했습니다. 임시 모듈은 제거했습니다. 캔버스 2009의 접속 API는 200, canvas·RTC WSS는 각각 101을 반환했습니다. 테스트 토큰과 서비스 비밀 값은 보고서에 기록하지 않았습니다.

호스트 Nginx의 일반 `/` 프록시에 WebSocket 전환 헤더가 없어 HMR이 200 응답으로 실패하는 문제를 수정했습니다. Orchestra의 개발용 최종 probe에는 Vite 클라이언트 경로 검사를 추가했습니다. startup 단위 시험 11개와 core 시험 18개가 통과했습니다. 이 검증은 HTTP/WSS 및 HMR 메시지 수준이며 실제 브라우저 렌더링이나 부하·장애 시험은 포함하지 않습니다.

검증 후 `./scripts/start-deploy.sh --no-build`의 23개 단계로 운영 모드에 복원했습니다. Vite 중지와 Nginx production 모드를 확인했으며, 공개 홈페이지·API는 200, 캔버스·RTC WSS는 101로 다시 확인했습니다.
