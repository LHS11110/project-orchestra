# Gitignore·라이선스 점검 — 2026-10-31

## 결론과 범위

6개 로컬 저장소(FE, BE, DB, Wall, Orchestra, hypervisor-for-agora)의 현재 소스, Git 추적 상태, 로컬 ref의 이력, 주요 빌드/배포물 및 고지 파일을 확인했습니다. 자체 코드는 FE·BE·DB·Wall이 MIT, Orchestra·hypervisor가 Apache-2.0입니다. 제3자 코드는 이 라이선스로 재허가되지 않습니다.

`.gitignore` 누락과 배포 고지 일부를 보완했습니다. **모든 의존성·이미지·배포 방식에 대한 법적 준수를 확정한 감사는 아닙니다.** 아래 남은 검토 항목을 완료하기 전 전체 바이너리/VM의 재배포 준비가 끝났다고 판단하면 안 됩니다.

## Git 점검 및 수정

- 각 저장소에서 `.env`, 개인키, Google OAuth JSON/ADC 인증 파일, `.envrc`·`.netrc`·`.pypirc`, 테스트 결과물·캐시 등이 제외되는지 확인했습니다. 부족한 규칙을 추가했습니다.
- `.env.example`, LICENSE, 소스·문서, Gradle wrapper JAR, systemd socket 소스가 숨겨지지 않는지 확인했습니다.
- BE의 전역 `Makefile`·`*test.html`·`test.html` 규칙을 제거했습니다. 해당 이름의 실제 소스를 숨기던 규칙이며 scratch 디렉터리 규칙은 유지했습니다.
- Docker build context에도 인증 파일 제외 규칙을 추가했습니다. `.gitignore`만으로 Docker context가 보호되지는 않습니다.
- 추적되면서 ignore에도 해당하는 파일은 현재 0건입니다. Gitignore는 이미 추적되거나 과거 커밋에 들어간 비밀값을 제거하지 않습니다.
- 실제 개인키 본문, Google/GitHub/AWS 토큰 패턴 및 현재 로컬 환경의 긴 비밀값과 일치하는 내용을 현재 후보 소스와 로컬 Git 이력에서 발견하지 못했습니다. 테스트 코드의 PRIVATE KEY 헤더 문자열만으로 실제 개인키라고 판정하지 않았습니다.
- 원격에만 존재하는 ref, submodule 이력, 알 수 없는 비밀 형식, 짧은 비밀번호는 이 결과로 보증하지 않습니다. `.env`의 값은 보고서에 포함하지 않았습니다.

재실행: `python3 scripts/audit-repositories.py --root /home/steam/Github --history`

증거: [Git 점검 JSON](audit/git-hygiene-2026-10-31.json).

## 라이선스에서 확인·보완한 사항

| 프로젝트 | 결과 |
| --- | --- |
| FE | 기존 고지는 PixiJS·Automerge·MathJax 중심으로 실제 의존성보다 적었습니다. production npm 의존성 인벤토리와 LICENSE/NOTICE 자동 복사를 추가했습니다. React, Monaco의 ThirdPartyNotices, perfect-freehand, rbush 등도 포함합니다. PDF.js 폰트·ICC·WASM 고지를 보존합니다. 이전 JS chunk를 유지할 때 해당 라이선스도 유지합니다. |
| BE | C++ 배포물에서 프로젝트·uWebSockets·uSockets의 고지와 OS 공유 라이브러리 copyright 파일을 확인했습니다. 사용하지 않는 jwt-cpp가 컴파일된다는 설명과 H2가 production JAR에 없다는 설명을 정정했습니다. Spring은 실제로 100개 JAR를 포함하며 아래 검토가 남았습니다. |
| DB | 프로젝트 MIT와 데이터베이스 벤더 조건을 분리한 고지 문서를 추가했습니다. Docker MSSQL은 Express로 설정돼 있습니다. Redis는 8.6.7, Elasticsearch는 공식 8.19.22 기반, Redis Insight는 3.8.0입니다. |
| Wall | Mix release가 의존성 root LICENSE/NOTICE를 보존하지 않는 점을 보완했습니다. 새 이미지에 16개 의존성의 원문과 mix.lock, 프로젝트 LICENSE를 `/app/licenses/`로 포함합니다. 누락된 의존성 원문이 있으면 빌드를 실패시킵니다. |
| Orchestra | Apache LICENSE·NOTICE와 의존성 안내를 추가하고 Docker 이미지에 포함하도록 수정했습니다. Python 배포판의 `.dist-info` 고지는 유지합니다. ODBC 드라이버의 Microsoft EULA는 별도 조건입니다. |
| Hypervisor | Apache NOTICE와 Cloud Hypervisor·edk2·Ubuntu 게스트·LLVM/JDK 등의 별도 라이선스 및 바이너리 재배포 조건을 정리했습니다. 현재 Git은 다운로드된 VM·펌웨어·바이너리를 제외합니다. |

FE production 고지는 `/licenses/`에 게시했습니다. Wall의 고지 보완은 **새 이미지 빌드본**에서 확인했으며, 감사만을 위해 실행 중인 브로커를 재시작하지는 않았습니다. 소스/새 이미지와 실행 중 컨테이너를 구분해야 합니다.

## 남은 확인 사항

1. **Spring JAR 외부 고지 검토:** production의 100개 embedded JAR 중 23개에서 이름이 LICENSE/NOTICE/COPYING인 파일이 발견되지 않았습니다. 이것만으로 23개 모두 위반이라고 단정할 수는 없습니다. 최상위 Apache/MIT 고지로 충족되는지, upstream NOTICE·저작권·EPL/MPL 등 별도 원문이 필요한지 각각 확인해야 합니다. 특히 H2·Logback·ECJ 등은 전체가 Apache라는 가정을 적용하면 안 됩니다. [정확한 JAR별 목록](audit/spring-jar-notices-2026-10-31.json)을 제공합니다.
2. **폰트·native binary:** MathJax font npm archive는 Apache-2.0 선언만 있고 별도 LICENSE가 없으며 upstream font 저장소/commit은 이번 조회에서 접근되지 않았습니다. MathJax Apache 원문을 보충했지만 실제 폰트의 원저작물·예외 조건까지 확정한 것은 아닙니다. Native canvas 플랫폼 패키지도 부모 MIT 고지를 보충했을 뿐 모든 포함 네이티브 구성 요소를 인증한 것은 아닙니다.
3. **VM/컨테이너 재배포:** Ubuntu/kernel/OpenJDK/FreeTDS 등 GPL/LGPL/EPL 계열 또는 예외 조건은 구체적인 결합·배포 방식에 따라 소스 제공 등의 의무를 검토해야 합니다. 체크섬·다운로드 URL·라이선스 링크만으로 모든 의무를 충족했다고 판단할 수 없습니다. 정확한 바이너리/패키지·소스·빌드 자료 묶음은 이번 감사에서 생성하지 않았습니다.
4. **벤더 서비스 조건:** Redis 8의 제공 라이선스, Elasticsearch 공식 배포물의 ELv2, SQL Server·ODBC·Redis Insight의 벤더 조건은 자체 MIT/Apache와 별개입니다. 현재 내부 애플리케이션 저장소 사용과 데이터베이스 자체를 관리형 서비스로 제공하는 모델을 구분해야 합니다. 이미지 공개·서비스 모델 변경 때 별도 검토가 필요합니다.
5. **사용자 콘텐츠:** 사진·지도·폰트·사용자가 pip/Maven으로 설치한 패키지의 권리는 저장소 라이선스로 보장되지 않습니다. 사용자 콘텐츠와 테스트 데이터의 권리 확인은 별도입니다.

## 검증

- Gitignore 대표 비밀/산출물 경로의 차단 및 소스/예제/라이선스 경로의 비차단 확인.
- FE 전체 Node 테스트 257개 통과. 실제 production build/export 및 공개 라이선스 URL 확인.
- FE 호스트 인벤토리는 설치 플랫폼의 optional 패키지에 따라 production 이미지 인벤토리와 개수가 다를 수 있습니다. 현재 Linux production 공개 인벤토리는 31개이며 각 항목에 고지 경로가 있습니다. 이는 실제 브라우저 번들의 완전한 SBOM이라는 의미는 아닙니다.
- Wall 새 이미지 빌드 성공 및 `/app/licenses/INDEX.txt`에서 16개 의존성 확인.
- Orchestra 새 감사 이미지 빌드와 프로젝트 LICENSE/NOTICE 포함 확인. 해당 이미지 안에서 테스트 38개 통과. 호스트에서는 Locust 미설치로 1건 실패했으나 의존성이 설치된 이미지에서 전체 통과했습니다.
- C++ 실행 이미지의 고지 경로와 공유 라이브러리 copyright 파일 확인.
- Spring 실행 JAR에서 embedded dependency/notice 목록 추출.

## 1차 출처

- [Apache-2.0 배포 조건](https://www.apache.org/licenses/LICENSE-2.0.html)
- [GNU 라이선스 FAQ](https://www.gnu.org/licenses/gpl-faq.html)
- [Redis 라이선스](https://redis.io/legal/licenses/)
- [Elastic 라이선스 FAQ](https://www.elastic.co/licensing/elastic-license/faq)
- [Microsoft SQL Server 라이선스 안내](https://www.microsoft.com/licensing/guidance/SQL)
