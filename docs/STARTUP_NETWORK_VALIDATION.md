# Redis 내부 IP 충돌 및 Orchestra 순차 기동 검증

검증일: 2026-11-10 (KST)

RedisInsight는 기존에 `172.20.0.8`을 고정 주소로 사용했다. Redis 노드/Sentinel이 `.2`~`.7`을 사용한 상태에서 동적 주소를 쓰던 저장소 브로커가 먼저 올라오면 Docker가 다음 빈 주소 `.8`을 할당했다. 이후 RedisInsight는 `Address already in use`로 시작하지 못했다. 기존 Orchestra 계획에는 RedisInsight 기동 단계도 없었다. Docker 재시작 시 컨테이너 순서는 Orchestra 실행 순서와 같다고 보장할 수 없으므로 순서 변경만으로 해결하지 않는다.

## 변경

| 서비스 | Redis 네트워크 주소 |
|---|---|
| RedisInsight | `172.20.0.20` 고정 |
| 저장소 브로커 | `172.20.0.21` 고정 |

공유 Redis 네트워크의 모든 프로젝트 서비스가 고정 IP를 쓰도록 하고 Orchestra 사전 검사에서 동적 할당, 중복 고정 IP, 서브넷 불일치 및 범위 밖 주소를 거부한다. RedisInsight의 TLS 헬스체크를 추가하고 개발/운영 계획에 DB 준비 후 RedisInsight를 기다리는 단계를 넣었다.

## 실제 수행 결과

| 검증 | 결과 |
|---|---|
| 단위 테스트 `python -m unittest discover -s tests -v` | 40개 통과; 로컬 TLS/Locust 통합 시험 포함 |
| `tests/startup_network_lab.py` | 기존 `.8` 선점 충돌 재현 성공 |
| 같은 임시 네트워크에서 브로커 → 관리 화면 기동 | 고정 주소로 기동 및 반대 순서 재시작 통과 |
| 같은 임시 네트워크에서 관리 화면 → 브로커 기동 | 고정 주소로 기동 및 반대 순서 재시작 통과 |
| `tests/docker_lab.py` | 동적/고정 IP 각각 pause·stop·disconnect·CPU 장애 주입/복구 8개 통과 |
| `development --no-build --runtime --check` | 설정 사전 검사 통과; 개발 모드로 운영 서비스를 전환하지 않음 |
| `production --no-build --runtime` 실제 실행 | 26개 단계 전체 완료 |
| 실제 서비스 IPAM 상태 | 브로커 `.21`, RedisInsight `.20` 모두 명시적 고정 주소 확인 |
| 실제 서비스 상태 | 15개 Agora 컨테이너 모두 healthy |
| 홈페이지 / 인증 API / RedisInsight HTTPS | 모두 HTTP 200 |

운영 순차 기동은 기존 볼륨을 유지하고 `--initialize` 없이 수행했다. 장애 주입 및 충돌 재현은 운영 컨테이너가 아닌 UUID 이름의 전용 임시 컨테이너/네트워크에서만 수행했고 모두 제거했다. OS/Docker 데몬 재부팅 자체는 이 검증 범위에 포함하지 않았다.

## 재실행

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/startup_network_lab.py
.venv/bin/python tests/docker_lab.py
.venv/bin/python -m orchestra.startup production --no-build --runtime --check
# 실제 서비스를 순차 기동/필요 시 재생성하므로 접속 중 사용자는 재연결될 수 있다.
.venv/bin/python -m orchestra.startup production --no-build --runtime
```

GitHub Actions에는 IPAM 충돌 회귀 시험 실행 단계를 추가했다. 원격 Actions 실행은 수행하지 않았으며 위 결과는 로컬 Docker Engine에서 측정한 결과다.
