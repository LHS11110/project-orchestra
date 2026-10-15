# 데이터베이스 내구성 실측 — 2026-10-15

운영 DB에는 장애나 설정 변경을 주입하지 않았습니다. 동일 이미지와 주요 영속화 설정의
격리 컨테이너·전용 Docker 볼륨으로 저장 확인 후 데이터 보존을 확인했습니다.
원시 결과: [database-durability-results-2026-10-15.json](database-durability-results-2026-10-15.json).

## 현재 실행 설정

| DB | 확인한 영속화·복제 설정 |
|---|---|
| Redis 8.6.7 | AOF 활성화, `appendfsync=everysec`, `no-appendfsync-on-rewrite=no`, `min-replicas-to-write=0` |
| SQL Server 2022 CU27 Express | `FULL` 복구 모델, `DELAYED_DURABILITY=DISABLED`, `PAGE_VERIFY=CHECKSUM` |
| Elasticsearch 8.19.22 / Nori | translog `REQUEST`, 현재 canvas 인덱스 shard 1 / replica 0 |

Redis 저장 성공 응답은 현재 C++ 경로에서 복제본 또는 AOF fsync 완료를 기다리지 않습니다.
Redis 복제본 두 대와 Sentinel 구성 자체가 모든 성공 응답의 무손실을 뜻하지 않습니다.

## 저장 확인 후 재시작

각 DB에 세 단계마다 1,000개를 추가해 누적 3,000개를 저장했습니다.
정상 종료·재기동, SIGKILL 후 재기동, SIGKILL 후 같은 볼륨으로 컨테이너 재생성을
각각 한 번 수행하고, 매 단계 누적 데이터의 식별자와 본문을 확인했습니다.
Redis는 JSON.SET의 응답, SQL은 개별 COMMIT 후 출력, ES는 bulk 개별 항목 성공을 확인했습니다.
ES에는 장애 직전 명시적인 flush/refresh를 요청하지 않았습니다.

| DB | 정상 재기동 후 누적 보존 | SIGKILL 후 누적 보존 | 컨테이너 교체 후 누적 보존 | 본문 불일치 |
|---|---:|---:|---:|---:|
| Redis | 1,000 / 1,000 | 2,000 / 2,000 | 3,000 / 3,000 | 0 |
| SQL Server | 1,000 / 1,000 | 2,000 / 2,000 | 3,000 / 3,000 | 0 |
| Elasticsearch | 1,000 / 1,000 | 2,000 / 2,000 | 3,000 / 3,000 | 0 |

SQL의 별도 시험에서는 트랜잭션 내부에만 존재하는 1개 행을 확인한 뒤 SIGKILL했습니다.
복구 후 그 행은 0개로 롤백됐고 기존 커밋 3,000개와 본문은 보존됐습니다.

| DB | 정상 종료 시작→조회 가능 | SIGKILL 시작→조회 가능 | 컨테이너 교체 포함→조회 가능 |
|---|---:|---:|---:|
| Redis | 0.362초 | 0.338초 | 1.024초 |
| SQL Server | 6.395초 | 6.793초 | 7.155초 |
| Elasticsearch | 26.835초 | 25.304초 | 22.268초 |

DB 프로세스 시작·복구·준비 상태 탐지의 합입니다. 앱 로그인·브로커 재접속을 포함한 서비스 RTO가 아닙니다.
실험 자원은 DB당 CPU 2개, Redis 메모리 512MiB, SQL 3GiB(엔진 상한 1GiB),
ES 2GiB(heap 512MiB)로 제한했습니다. 운영 배포의 성능 수치로 사용하지 않습니다.
SQL 정상 종료는 엔진의 `SHUTDOWN`으로 checkpoint와 종료를 수행했습니다.
초기 Docker stop(유예 30초)은 SIGKILL로 전환되어 정상 종료 표본에서 제외했습니다.
해당 이미지 PID 1의 셸 래퍼가 자식으로 SQL 프로세스를 시작하는 것도 확인했습니다.
Redis/ES와 SQL의 실행 ID는 JSON의 `databaseRunIds`에 구분했습니다.

## Redis 복제 단절 시 누락 재현

1. 주 노드와 임시 복제본에 기본 3,000개가 일치함을 확인했습니다.
2. 복제본을 중지해 복제가 불가능한 상태를 만들었습니다.
3. 주 노드가 추가 JSON.SET 250개에 성공 응답하는 것을 확인했습니다.
4. 주 노드를 SIGKILL한 뒤, 기존 복제본을 시작해 수동 승격했습니다.
5. 승격 노드는 기존 3,000개를 보존했지만 추가 250개는 없었습니다. 본문 불일치는 0개였습니다.

이는 새 주 노드에서 성공 응답 데이터의 누락을 재현한 결과입니다. 이전 주 노드의 볼륨까지
파괴하거나 해당 노드를 새 주 노드에 재동기화한 것은 아니므로 물리적으로 복구 불가능하다고
판정한 결과는 아닙니다. 현재 운영의 두 복제본과 Sentinel을 그대로 재현한 자동 선출 시험도 아닙니다.
복제가 불가능한 동안에도 쓰기를 허용하는 현 정책의 위험 조건을 임시 복제본 한 대로 모델링했습니다.

## 측정 범위와 해석

- SIGKILL 후에도 호스트 OS의 페이지 캐시는 남습니다. 전원 차단·커널 크래시·디스크 컨트롤러
  장애에서의 fsync 내구성을 검증한 결과가 아닙니다.
- 같은 볼륨의 재사용을 확인했으며, 물리 디스크 손실·전체 호스트 장애·백업 복원·비트 손상은 시험하지 않았습니다.
- DB별 세 사례 각 1회 관측입니다. 데이터 3,000건을 독립적인 장애 실험 3,000회로 해석하지 않습니다.
- SQL/ES는 이 배포에서 단일 노드입니다. 프로세스 복구와 노드·저장장치 손실 시 데이터 보존은 다른 문제입니다.
- DB의 저장 확인과 Canvas 앱 전체의 내구성은 구분합니다. 브로커·클라이언트 ACK 유실과 재전송,
  Redis→ES 스냅샷 경계의 앱 통합 장애 시험은 이번 DB 시험에 포함하지 않았습니다.
- 외부 포트를 게시하지 않았습니다. SQL/ES는 컨테이너 루프백만 사용하고 Redis는 전용 internal
  네트워크를 사용했습니다. 운영 TLS·권한 동작을 재검증한 시험은 아닙니다.
- 모든 실험 컨테이너·볼륨·네트워크 및 임시 SQL 자격 증명 파일은 제거했습니다.

## 재실행

이미 준비된 Docker 이미지와 Python 표준 라이브러리만 필요합니다. 운영 자원 이름을 입력받지 않고
매번 무작위 `orchestra-durability-*` 이름과 실행 라벨을 생성하며, 정리 시 두 조건을 모두 검증합니다.

```sh
python3 tests/database_durability_lab.py --records 1000 --output results/database-durability.json
# 특정 DB만 실행
python3 tests/database_durability_lab.py --database mssql --records 1000 --output results/mssql-durability.json
```

이는 기존 `orchestra run --fault stop`과 별개의 전용 내구성 실험입니다.
`SIGKILL`은 이 스크립트가 생성한 임시 DB에만 사용하며 기존 Agora 컨테이너에는 사용하지 않습니다.
