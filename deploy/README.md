# NCP 배포 가이드 (Phase 0)

이 문서는 [BLUEPRINT.md](../BLUEPRINT.md) 10장에 따른 최소 배포 절차다.
**서버 생성·네트워크 설정은 NCP 콘솔 접근 권한이 있는 사용자가 직접 수행해야 한다** —
이 리포지토리의 코드/설정 파일은 여기까지만 준비되어 있다.

## 1. NCP Server 생성 (콘솔에서 수동 수행)

1. NCP 콘솔 → Server → 서버 생성
   - 이미지: Ubuntu Server 22.04
   - 스펙: Standard 2vCPU / 8GB (발표 트래픽 기준 최소 사양)
   - 인증키: 신규 또는 기존 키 페어 사용, `.pem` 파일 안전하게 보관
2. 공인 IP 신청 및 연결
3. ACG(방화벽) 규칙 추가:
   - `22` (SSH) — 관리 목적 IP만 허용
   - `80` (HTTP) — 전체 허용 (`0.0.0.0/0`)

## 2. 서버 초기 설정 (SSH 접속 후)

```bash
ssh -i <keyfile>.pem root@<공인IP>

apt-get update && apt-get upgrade -y
apt-get install -y docker.io docker-compose-plugin git
systemctl enable --now docker
```

## 3. 코드 배포

```bash
git clone <repository-url> legal-screening-agent
cd legal-screening-agent

# .env 파일은 git에 포함되지 않으므로 서버에서 직접 작성한다
nano .env
```

`.env`에 필요한 값 (레포의 `.env`와 동일한 키를 채운다):

```
GEMINI_API_KEY=...
CLOVA_API_KEY=...
CLOVA_APIGW_KEY=...
PASS2_PROVIDER=hcx
```

## 4. 컨테이너 기동

```bash
docker compose up -d --build
docker compose logs -f app   # 인덱스 로드 로그 확인 후 Ctrl+C
```

정상 기동 시 로그에 `NiceGUI ready to go`와 인덱스 로드 완료 메시지가 보여야 한다.
브라우저에서 `http://<공인IP>/` 접속.

## 5. 배포 전 사전 워밍 (발표 리스크 대비)

라이브 시연 중 API 장애/지연에 대비해, 배포 직후 데모 문서 2종을 미리 한 번씩
돌려 이력(SQLite)에 결과를 캐싱해둔다. 발표 중 문제가 생기면 "이력" 탭에서
캐시된 결과를 불러오는 것으로 시연을 이어갈 수 있다.

- UI에서 "샘플: 경미한 위반" / "샘플: 심각한 위반" 각각 1회 실행

## 6. 데이터 영속성

`docker-compose.yml`은 `./data`를 컨테이너의 `/app/data`에 마운트한다.
`data/index`(FAISS 인덱스), `data/history.db`(스크리닝 이력)는 컨테이너를
재생성해도 유지된다. 컨테이너 재빌드만으로 코드를 갱신할 수 있다:

```bash
git pull
docker compose up -d --build
```

## 7. 알려진 제약 (Phase 0 범위)

- TLS 미적용 (HTTP만). 발표용 단기 운영 기준으로는 허용 범위로 판단.
  Phase 1에서 Caddy/nginx + Let's Encrypt 추가 예정.
- 인증 없음 — 공인 IP를 아는 누구나 접근 가능. 발표 종료 후 서버 정지 권장.
- 단일 컨테이너, 오토스케일 없음.
