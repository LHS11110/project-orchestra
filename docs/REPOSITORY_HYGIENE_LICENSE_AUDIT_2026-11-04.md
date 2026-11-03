# 저장소 민감정보·라이선스 점검 — 2026-11-04

## 결론

6개 프로젝트의 추적 항목 1,372개, 작업 트리의 커밋 후보, Git 인덱스 및 로컬 ref의 이력을 점검했다. 검사한 실제 비밀값·키/토큰 패턴과 일치하는 노출은 발견하지 못했다. 이미 추적되면서 ignore에도 해당하는 파일, 대표 비밀 파일 ignore 누락, 대표 소스 경로의 과도한 ignore도 이번 검사에서 0건이다.

라이선스는 **모든 배포 방식에서 준수가 확정됐다는 결론을 내릴 수 없다.** 소스 라이선스와 고지 보존은 확인했지만 Spring 의존성별 배포 고지, 일부 폰트/참고 이미지의 권리 및 VM/컨테이너를 외부에 재배포할 때의 조건 검토가 남았다. 아래 항목은 명확히 확인한 사실과 미확인을 구분한 결과다.

## 범위와 방법

- Git 작업 파일·현재 인덱스·커밋 후보 및 로컬 `--all` ref의 blob을 검사했다. 인덱스와 작업 파일이 달라도 별도로 검사했다.
- 실제 개인키 본문, Google OAuth secret, GitHub/AWS 토큰 패턴 및 로컬 환경의 긴 현재 비밀값과 일치 여부를 검사했다.
- 추가로 암호화 개인키/PGP 개인키, 봇 토큰, JWT 형태, 자격 증명이 포함된 DB URL, Slack/Stripe 토큰 패턴도 인덱스·작업 트리·로컬 이력에서 검사했다. 추가 발견도 0건이었다.
- `.env.example`은 예제이고 BE의 `credentials.py`는 자격 증명 취급 코드이므로 파일명 후보만으로 실제 비밀 파일이라고 판정하지 않았다.
- 추적된 VM 디스크·DB 덤프·개인키 저장소는 발견하지 않았다. 공개 키/도메인/공인 IP/클라이언트 ID와 실제 비밀값은 구분한다.
- PNG/PDF 등은 텍스트 비밀 패턴 검사가 완전한 이미지 OCR 감사가 아니다. Figma 참고 이미지·참여자 화면·여행 캔버스 스크린샷 일부를 육안 확인했으며 실제 비밀값 노출은 확인하지 못했다.
- 원격에만 있는 ref, reflog/유실 객체, 미지의 비밀 형식과 외부 submodule의 전체 이력은 보증 범위가 아니다. BE submodule 작업 트리의 변경은 없었지만 upstream 전체 이력을 다시 감사한 것은 아니다.
- 실행 중 서비스 설정/데이터는 변경하지 않았다. 점검 결과만 문서화했으며 자동 커밋·푸시·이력 재작성은 수행하지 않았다.

## 프로젝트별 결과

| 프로젝트 | 추적 항목 | 자체 소스 라이선스 | 확인 결과 / 남은 항목 |
| --- | ---: | --- | --- |
| project-agora-FE | 755 | MIT | 배포 npm 인벤토리 61개 항목의 고지 경로가 모두 존재. SDK wheel 0.1.0~0.1.2 모두 MIT LICENSE 포함. MathJax 폰트/네이티브 구성 요소와 Figma 참고 이미지의 별도 권리 검토는 남음. |
| project-agora-BE | 349 | MIT | C++ 이미지에 프로젝트·uWebSockets·uSockets 고지와 시스템 라이브러리 copyright 파일 존재. Spring 배포 JAR 120개 중 29개의 일반 고지 파일명 미발견. 외부 고지 묶음 검토 필요. |
| project-agora-DB | 85 | MIT(구성/스크립트) | 실제 SQL Server 컨테이너의 MSSQL_PID 및 Compose 기본값은 Express. Redis·Elasticsearch·SQL Server 등 벤더 조건은 별도 적용. |
| project-agora-Wall | 50 | MIT | 실행 중 브로커 `/app/licenses/INDEX.txt`에 16개 의존성, 원문 고지와 project-LICENSE 확인. OS/Erlang 이미지 전체의 재배포 감사는 별도. |
| project-orchestra | 71 | Apache-2.0 | LICENSE·NOTICE·THIRD_PARTY_NOTICES 존재, Dockerfile 복사 설정 확인. Microsoft ODBC 등 벤더/OS 조건 별도. |
| hypervisor-for-agora | 62 | Apache-2.0 | LICENSE·NOTICE 존재, 다운로드된 VM/바이너리 추적 없음. VM/커널/GCC/JDK/펌웨어 등을 포함한 외부 배포의 고지·소스 제공 자료 묶음은 이번에 생성하지 않음. |

FE의 61개는 설치된 production npm 의존성의 상위 집합이며 실제 브라우저 번들에 61개 전부 들어간다는 뜻이 아니다. 소스/벤더 문서에 적힌 이전 숫자와 현재 실행 배포물은 구분했다.

## 우선 검토할 항목

### 1. Spring 배포물의 통합 고지

실행 컨테이너 `/app/app.jar`를 읽어 `BOOT-INF/lib`의 120개 JAR를 확인했다. 이 중 29개에서 LICENSE/NOTICE/COPYING 이름의 파일을 찾지 못했다. `ecj-3.45.0.jar`에는 대체 문서 `about.html`이 있다. POM 라이선스 메타데이터는 로컬에서 119개를 대조했고, 이름만으로 위반을 판정하지 않았다. CC0/Unlicense 항목도 있으므로 29개 전체가 같은 고지 의무를 갖는 것은 아니다.

특히 H2(MPL-2.0 또는 EPL-1.0), Logback(EPL-2.0 또는 LGPL-2.1), ECJ(EPL-2.0), JDBC(MIT) 등을 모든 의존성이 Apache라고 처리하면 안 된다. 각 배포 버전의 원문/NOTICE·적용 선택·필요한 소스 안내를 정리해야 한다. Spring 최종 이미지에는 별도 최상위 LICENSE/통합 고지 디렉터리가 현재 없다. JAR 내부에 보존된 고지와 외부 고지 보완 필요성을 함께 검토해야 한다.

정확한 대상과 POM 라이선스: [Spring 인벤토리](audit/spring-jar-notices-2026-11-04.json).

### 2. FE 폰트·문서 이미지

배포 고지의 모든 경로는 존재한다. 다만 MathJax NewCM 폰트 및 native canvas platform 패키지는 독립 원문이 없는 패키지에 부모 라이선스 보충 파일을 제공한 상태다. 이 보충만으로 포함된 폰트/네이티브 구성 요소 전부의 권리 검토가 끝난 것은 아니다. NewCM 패키지는 Apache-2.0을 선언하지만 이번 조회에서도 지정된 upstream 저장소의 LICENSE 원문을 가져오지 못했다.

FE 루트의 `스크린샷 2026-10-27 오전 11.46.25.png`(분해형 유니코드 파일명)는 FigJam 화면과 제3자 튜토리얼·상표를 포함한 참고 이미지다. 앱의 자체 MIT 코드로 재허가되는 이미지로 취급하면 안 된다. 공개 저장소에서 유지할 권한/인용 범위를 확인해야 한다. 이번 점검에서는 이미지를 삭제하거나 편집하지 않았다.

여행 템플릿의 지도·사진은 `docs/travel-template/README.md`, `sources.json` 및 화면에 OSM/ODbL, CC BY-SA 4.0, CC0 출처가 기록돼 있다. 개별 자산의 조건을 유지해야 하며 앱 MIT로 덮어쓰면 안 된다.

### 3. DB/VM 이미지의 사용·재배포 조건

Redis 8은 RSALv2/SSPLv1/AGPLv3 중 선택하는 구조다. Elasticsearch 공식 바이너리는 별도 ELv2 조건을 적용하며 애플리케이션 내부 검색 기능 사용과 데이터베이스 자체를 관리형 서비스로 제공하는 모델을 구분한다. SQL Server Express 설정 확인은 모든 벤더 계약 조건에 대한 법적 승인이라는 의미가 아니다.

공개 Git 소스와 별도로 컨테이너/VM 이미지 자체를 배포할 경우, 정확한 버전별 라이선스·NOTICE·소스 제공/재링크 등 적용 의무를 정리해야 한다. 현재 Git에 VM 디스크가 추적되지는 않는다. 이번 점검은 전체 VM/OS의 완전한 SBOM 및 재배포 자료 생성 작업이 아니다.

## 증거

- [기본 Git/이력 검사](audit/git-hygiene-2026-11-04.json)
- [인덱스·작업 트리 추가 검사](audit/git-index-extra-2026-11-04.json)
- [추가 토큰 패턴의 로컬 이력 검사](audit/git-history-extra-2026-11-04.json)
- [FE 배포 인벤토리](audit/frontend-npm-inventory-2026-11-04.json)
- [Spring JAR/POM 검사](audit/spring-jar-notices-2026-11-04.json)
- [검사 시점 Git HEAD](audit/git-heads-2026-11-04.json)

비밀값 자체는 보고서에 기록하지 않았다. 위 JSON과 파일명 기반 검사에는 실제 비밀 내용이 포함되지 않는다.

## 기준 자료

- [Apache-2.0 재배포 조건](https://www.apache.org/licenses/LICENSE-2.0.html)
- [Redis 라이선스](https://redis.io/legal/licenses/)
- [Elastic ELv2 FAQ](https://www.elastic.co/licensing/elastic-license/faq)
- [Microsoft SQL Server 라이선스 안내](https://www.microsoft.com/licensing/guidance/SQL)
- [GNU GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html)

이번 온라인 재확인에서 Apache·Redis·Elastic 원문/공식 안내를 조회했다. GNU 페이지와 MathJax 폰트 LICENSE 직접 조회는 실패했으며, 해당 범위의 완전한 검증을 주장하지 않는다.
