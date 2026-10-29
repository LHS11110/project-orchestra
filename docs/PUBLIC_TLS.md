# 외부 HTTPS 인증서: Let's Encrypt + Certbot

현재 외부 진입점 `https://118.34.144.201`은 호스트 Nginx에서 Let's Encrypt 인증서를
제공합니다. 자체 서명 인증서가 아닙니다. 인증서·키는 `/etc/letsencrypt/live/118.34.144.201/`에
두고 저장소에 넣지 않습니다. 호스트 Nginx는 Docker 게이트웨이 `127.0.0.1:8444`로 전달합니다.

## 공인 IP 변경 반영 (2026-10-29)

공인 IP를 `118.34.144.201`로 변경하면서 해당 IP의 공개 인증서를 새로 발급했습니다.
호스트 Nginx 인증서 경로와 Certbot 갱신 서비스의 `--cert-name`도 새 IP를 사용합니다.
Spring의 `CORS_ALLOWED_ORIGINS`, Phoenix의 `BROKER_ALLOWED_ORIGINS`와 FE 개발 프록시·허용 호스트를 함께 변경했습니다.
운영 FE의 `VITE_WS_BASE_URL`은 비워서 브라우저의 현재 HTTPS 접속 주소에서 WSS 주소를 계산합니다.
주소 변경 후 FE를 재빌드·게시하고 Spring과 Phoenix를 재생성해야 설정이 반영됩니다.

새 인증서 발급, TLS 신뢰 검증, 로그인 및 캔버스 WebSocket 연결을 확인했습니다.
갱신 서비스의 새 인증서 대상 실행도 성공했습니다(아직 갱신 시점이 아니므로 재발급 없음).
같은 날 staging 갱신 모의시험은 ACME 서버의 `rateLimited / Service busy` 응답으로 완료되지 않았습니다.
운영 인증서는 발급·적용된 상태이며 모의시험은 제한이 해제된 후 다시 수행할 수 있습니다.

## 발급과 갱신

- 클라이언트: Snap Certbot 5.8.0, 실행 경로 `/snap/bin/certbot`.
- 인증: `webroot`, `/var/www/letsencrypt`.
- 검증 경로: HTTP 80번의 `/.well-known/acme-challenge/`. 나머지 HTTP 요청은 HTTPS로 리디렉션합니다.
- IP 인증서 프로필: `shortlived`, 약 6일 유효.
- 갱신 설정: `/etc/letsencrypt/renewal/118.34.144.201.conf`.
- 갱신 후 훅: `/etc/letsencrypt/renewal-hooks/deploy/90-reload-nginx`.
  `nginx -t` 성공 후 graceful reload를 수행합니다.

기본 `certbot` 명령은 `/usr/local/bin/certbot` → `/snap/bin/certbot` 링크를 통해 Snap의
최신 안정 버전을 실행합니다. `/usr/bin/certbot`은 배포판의 구버전이므로 직접 사용하지 않습니다.
운영 스크립트와 자동 갱신 서비스도 Snap 경로를 명시합니다. IP webroot를 지원하는 최소 버전은 5.4입니다.

```sh
sh scripts/public-tls.sh status 118.34.144.201
sudo sh scripts/public-tls.sh verify-renewal 118.34.144.201
sudo sh scripts/public-tls.sh renew 118.34.144.201
```

`verify-renewal`은 staging으로 ACME 인증과 갱신 후 훅을 검증하며 운영 인증서를 바꾸지 않습니다.
실제 `renew`는 갱신 시점이 된 인증서만 처리합니다. `--force-renewal`을 주기적으로 사용하지 않습니다.

## 자동 갱신 타이머

현재 `certbot.service`는 아래 명령을 실행하도록 설정되어 있습니다.

```ini
[Service]
ExecStart=
ExecStart=/snap/bin/certbot -q renew --cert-name 118.34.144.201 --no-random-sleep-on-renew
```

서비스 override 위치는 `/etc/systemd/system/certbot.service.d/override.conf`입니다.
다른 서버에 적용할 때에는 대상 인증서 이름과 기존 Certbot 관리 범위를 확인하여 설정합니다.
타이머는 6시간마다 확인하고 최대 15분 지연을 분산합니다.

```sh
sudo install -d -m 0755 /etc/systemd/system/certbot.timer.d
sudo install -m 0644 deploy/certbot/certbot.timer.conf /etc/systemd/system/certbot.timer.d/agora.conf
sudo install -m 0755 deploy/certbot/reload-nginx.sh /etc/letsencrypt/renewal-hooks/deploy/90-reload-nginx
sudo systemctl daemon-reload
sudo systemctl enable --now certbot.timer
sudo systemctl restart certbot.timer
systemctl list-timers certbot.timer --all
```

Snap의 별도 `snap.certbot.renew.timer`는 현재 비활성 상태이며 동일 인증서를 중복 관리하지 않습니다.
갱신 실패는 `journalctl -u certbot.service`와 `/var/log/letsencrypt/letsencrypt.log`에서 확인합니다.

## 내부 서비스 인증서

호스트 Nginx → Docker 게이트웨이, Spring·C++·DB 등 내부 TLS는 별도의 내부 CA를 사용합니다.
`agora-nginx`, `agora-spring` 등의 사설 이름과 사설 IP는 공개 인증서의 발급 대상이 아닙니다.
공개 IP 인증서를 내부 이름에 복사하면 호스트 이름 검증이 실패하므로 내부 CA를 제거하지 않습니다.
브라우저는 외부 Let's Encrypt 인증서만 검증하며 내부 CA를 설치할 필요가 없습니다.

공식 안내: [Let's Encrypt IP 인증서와 Certbot](https://letsencrypt.org/2026/03/11/shorter-certs-certbot).
