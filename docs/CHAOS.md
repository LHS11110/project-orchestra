# 카오스 실험과 복구

## 도구와 범위

Orchestra의 `monkey`는 명시한 Docker 컨테이너 목록에서 난수 seed로 하나씩 선택하는 자체 도구입니다. [Netflix Chaos Monkey](https://github.com/Netflix/chaosmonkey)는 Spinnaker 배포를 전제로 하므로, 현재 Docker 구성에 별도의 Spinnaker를 요구하는 대신 제한된 장애·복구 도구를 제공합니다.

| 장애 | 주입 | 복구 | 확인할 내용 |
|---|---|---|---|
| pause | 프로세스 실행 일시정지 | unpause | 타임아웃, 큐·메모리 제한, 재연결 |
| stop | 정상 종료 시도 후 제한 시간에 중지 | 동일 컨테이너 start | 재시작·복구 시간, 저장 상태 |
| disconnect | 지정 네트워크 연결 제거 | 기존 IP·별칭을 보존해 재연결 | DNS·연결 재수립, 직접 우회 금지 |
| cpu | CPU quota 감소 | 이전 quota 복원 | p95 지연, 백프레셔, 정상화 |

`stop`은 전원 손실이나 `SIGKILL`과 같은 장애가 아닙니다. 디스크 손상, 볼륨 삭제, 패킷 지연·손실, 대규모 동시 종료도 이 도구의 범위가 아닙니다. 이들을 검증한 것으로 해석하면 안 됩니다.

설정의 노드·장애·네트워크 허용 목록, Compose 프로젝트명, 실제 네트워크가 일치하고 대상이 실행·정상 상태일 때만 주입합니다. 1회 장애는 1–300초, monkey는 최대 20회이며 매번 복구 후 다음 대상으로 넘어갑니다. 현재 Docker context를 사용하므로 먼저 대상 context와 설정을 확인하십시오. `lab/staging` 문자열은 권한 통제가 아닌 의도 표시입니다.

## 시작 절차

1. 실제 서비스와 데이터를 복제한 전용 시험 배포를 준비합니다. 운영 계정·활성 캔버스를 사용하지 않습니다.
2. `config/orchestra.json`의 컨테이너·프로젝트·네트워크·엔드포인트를 해당 배포로 맞춥니다.
3. `orchestra inventory`로 대상 일치를 확인하고 read-only probe 및 Locust로 정상 기준선을 기록합니다.
4. `plan`으로 주입 대상을 검토합니다. 별도 터미널에서 Locust와 자원 수집을 유지합니다.
5. 장애 하나를 실행하고 저널과 업무 검증 결과를 함께 평가합니다.

```sh
.venv/bin/orchestra inventory
.venv/bin/orchestra plan --node cpp --fault pause --duration 15
.venv/bin/orchestra run --node cpp --fault pause --duration 15

.venv/bin/orchestra run --node storage-broker --fault disconnect --network agora-services --duration 10
.venv/bin/orchestra run --node spring --fault cpu --cpus 0.25 --duration 30
.venv/bin/orchestra monkey --nodes cpp,phoenix,spring --fault pause --duration 5 --iterations 5 --seed 42
```

동일 `results/`의 lock이 겹치는 실험을 막습니다. 서로 다른 출력 디렉터리나 외부 도구로 같은 배포를 동시에 변경하면 이 보호를 우회하게 되므로 한 배포당 하나의 장애 실행자만 사용하십시오. 장애 중 실패율 기준을 넘어서 Locust가 실패 종료하는 것은 정상적인 관측 결과일 수 있습니다. 기준선·장애 구간·복구 후 구간을 구분해 비교합니다.

## 복구와 저널

변경 전 컨테이너 ID, 프로젝트, 네트워크와 CPU quota를 0600 JSON 저널에 저장합니다. 환경변수나 인증 토큰은 저장하지 않습니다. 일반 종료, 예외, SIGINT/SIGTERM 시에도 `finally`에서 복구합니다. Docker가 변경을 수락한 뒤 응답만 유실되어도 저장한 상태로 복구를 시도합니다.

`SIGKILL`, 실행 호스트 종료, Docker daemon 장애에서는 자동 복구가 실행되지 않을 수 있습니다. 저널·lock을 보존하고 다른 장애를 시작하기 전에 다음 명령으로 복구합니다.

```sh
.venv/bin/orchestra recover --journal results/chaos-저널ID.json
.venv/bin/orchestra inventory
```

복구는 원래 컨테이너 ID를 검사합니다. 컨테이너가 재생성되었다면 새 컨테이너를 임의로 수정하지 않고 실패합니다. 이때 배포 담당자가 현재 상태를 확인하여 수동으로 정상화해야 합니다. 복구가 실패하면 lock을 남겨 다음 실험을 차단합니다. 파일만 삭제해서 장애 상태를 덮지 마십시오.

`restored`는 컨테이너 실행·Docker healthcheck 복귀를 의미합니다. 사용자 요청 성공, Redis primary 선출, 데이터 보존, 모든 참여자의 동기화까지 검증했다는 뜻은 아닙니다. 아래 기준과 Locust·probe를 추가로 확인합니다.

## 구성 요소별 실험과 합격 기준

| 대상 | 권장 첫 실험 | 장애 중 기대 / 복구 후 필수 확인 |
|---|---|---|
| Nginx | pause 5초 | 외부 요청 실패를 정확히 기록; 해제 후 새 HTTPS·WSS 연결 성공 |
| FE | stop 5초 | 새 페이지 접근 실패; 재시작 후 문서·정적 파일 로드. 기존 열린 페이지가 계속 동작할 수도 있음 |
| Spring | pause 10초 | 로그인·접근 토큰 발급 타임아웃; 회복 후 새 사용자·새 캔버스 연결 성공 |
| Phoenix | stop 10초 | WSS 종료·재접속과 비차단 알림; 룸 간 메시지 유출 없음; 새 연결이 최신 스냅샷 수신 |
| C++ | pause 15초 | 질의 타임아웃·큐 제한; 실패가 성공으로 보고되지 않음; 정상화 후 응답 ID와 캔버스 일치 |
| storage-broker | disconnect 서비스 네트워크 | 저장소 전반 요청 실패; 직접 저장소로 우회하지 않음; 복구 후 TLS·인증·질의 성공 |
| SQL | stop 10초 | 메타데이터 요청 실패·제한된 재시도; 재시작 후 SELECT와 로그인·ACL 검증 |
| Elasticsearch | pause 10초 | 스냅샷·검색 실패 처리; 기존 캐시 가능 범위와 신규 캔버스 로드 차이를 확인; 회복 후 문서 최신성 확인 |
| 현재 Redis primary | pause 30초부터 측정 | Sentinel의 실제 장애 판정 시간에 맞춰 선출 관측; 새 primary TLS 경로·ROLE 확인; 복구한 이전 primary의 역할 확인 |
| Redis replica 1/2 | stop 10초 | primary 읽기 유지 여부; 복귀 후 복제 정상·지연 정상화 |
| Sentinel 1/2/3 | 한 개 pause 15초 | 나머지 quorum·primary 탐색 정상; 복귀 후 3개 탐색 결과 수렴 |
| 브라우저 호스트 | 다중 브라우저에서 호스트 탭 종료 | 새 호스트 선출 동안 큐 유지, 카운트 감소 없음, 중복 적용 없음, 송신자 ACK, 신규 접속 최신 상태 |

Redis primary는 이름이 `redis-primary`인 컨테이너와 항상 같지 않습니다. 선출 후 Sentinel이 알려준 실제 슬롯과 `ROLE`을 확인한 뒤 해당 노드를 대상으로 지정하십시오. quorum 손실 시험은 단일 노드 기준선과 데이터 검증이 끝난 뒤 별도 계획으로 수행해야 하며 기본 monkey는 동시 다중 장애를 만들지 않습니다.

데이터 검증은 전용 캔버스에서 수동으로 고유 ID의 변경을 기록하고 ACK 단계별로 관측합니다. 초기 상태, 호스트 ACK, C++ 저장 성공, 새 브라우저의 재조회, 재시작 후 재조회 결과를 비교하십시오. 기본 Locust는 읽기만 수행하므로 쓰기 유실·중복·CRDT 충돌을 자동 판정하지 않습니다. 프레임·메시지 손실, 브라우저 P2P 선출 및 음성 품질도 별도 브라우저 실험이 필요합니다.

## 격리된 자체 검증

```sh
.venv/bin/python tests/docker_lab.py
```

이 스크립트는 고유 이름의 임시 네트워크와 테스트 컨테이너만 생성하여 pause, stop, disconnect, CPU 제한을 실제로 주입하고 복원 상태를 검사한 뒤 제거합니다. 기존 Agora 컨테이너에 장애를 주입하지 않습니다. SIGKILL·프로세스 외부 장애의 자동 복구까지 보장하는 시험은 아닙니다.
