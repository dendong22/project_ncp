# 기획서 법무 스크리닝 AI — v2 블루프린트

> 작성일: 2026-07-07 · 발표(시연): **2026-07-08 (D-1)** · 시연 환경: NCP 서버 라이브
>
> 이 문서는 두 층으로 구성된다.
> - **Phase 0**: 내일 발표까지 실제로 구현/배포하는 최소 컷라인 (시간 단위 계획)
> - **Phase 1~2**: 발표 이후 완성하는 v2 전체 설계 (수업 제출물·포트폴리오 기준)

---

## 1. 제품 정의

**포지셔닝**: "출시 전 컴플라이언스 코파일럿" — 위반을 *찾아주는* 도구가 아니라, 기획서를 *출시 가능한 상태로 만들어주는* 도구. AI 기본법 시행 시점에 맞춰 개인정보보호법 + AI 거버넌스(AI기본법·국가AI윤리기준)를 함께 심사하는 것이 차별화 축.

**핵심 루프**: 업로드 → 스크리닝(교차검증 포함) → 대화형 질의 → 수정안 채택 → 재검사(점수 상승) → 컴플라이언스 산출물 생성(처리방침·동의문구).

**v1 대비 정체성 변화**: 단방향 1회 판정기 → 반복 검토 루프 + 산출물 생성기.

## 2. 확정 결정 요약

| 항목 | 결정 |
|---|---|
| UI 프레임워크 | **NiceGUI** (FastAPI 위에 마운트, 순수 파이썬) |
| 아키텍처 | FastAPI + 비동기 작업 (Phase 0에서는 NiceGUI async 태스크로 갈음) |
| Pass 1 (OCR·추출) | Gemini (멀티모달 유지) |
| Pass 2 (판정) 기본값 | **HyperCLOVA X** (LLM 추상화 계층으로 Gemini 전환 가능) |
| 입력 형식 | PDF / PNG / JPG / **DOCX** (HWP 제외) |
| 벡터 저장소 | Phase 0: FAISS 유지 → Phase 1: PostgreSQL + pgvector |
| 이력 저장 | Phase 0: SQLite → Phase 1: PostgreSQL |
| 인증 | Phase 1로 보류 (발표 데모에는 불필요) |
| 법령 자동 수집 | Phase 2 (국가법령정보센터 API) |
| 배포 | NCP Server(VPC) 1대 + Docker, 라이브 시연 |
| 수명주기 맵 | **포함** (인벤토리 기반 정적 시각화) |

**기능 스코프(확정)**: 대화형 질의, 수정 후 재검사 루프, 법령 도메인 확장+선택, 처리방침/동의문구 생성, 오탐 피드백, 개인정보 인벤토리 추출, 제재 사례 코퍼스, 검사-반론 교차검증, 수명주기 맵.

## 3. 시스템 아키텍처 (v2 목표형)

```
┌─ NCP Server (VPC, Ubuntu 22.04, Docker) ─────────────────────┐
│                                                               │
│  nginx/Caddy (:80/:443)                                       │
│    └─ uvicorn ── FastAPI ──┬── NiceGUI UI (5개 탭)            │
│                            └── REST API (/api/v1/*)           │
│                                                               │
│  Screening Orchestrator (async)                               │
│    Pass1(Gemini) → 인벤토리 추출 → 검색(FAISS/pgvector)       │
│    → Pass2(HCX│Gemini) → 교차검증 → 인용 무결성 → 점수 산출   │
│                                                               │
│  Storage: SQLite(이력) · FAISS 인덱스 · 업로드/산출물 파일     │
│  (Phase 1: PostgreSQL + pgvector 로 통합)                     │
└───────────────────────────────────────────────────────────────┘
외부: Gemini API · Clova Studio(HCX, 임베딩 v2)
```

## 4. 디렉터리 구조 (v2)

```
legal-screening-agent/
├─ app/
│  ├─ main.py               # FastAPI 생성 + NiceGUI 마운트 + REST 라우트
│  ├─ ui/
│  │  ├─ theme.py           # 공통 CSS/테마 (v1 카드·배지 스타일 이식)
│  │  ├─ page_screening.py  # ① 업로드 + 실시간 파이프라인 뷰
│  │  ├─ page_report.py     # ② 점수·인벤토리·수명주기 맵·finding 카드·재검사
│  │  ├─ page_chat.py       # ③ AI 법무 상담 (RAG 챗)
│  │  ├─ page_artifacts.py  # ④ 처리방침·동의문구·체크리스트 생성
│  │  └─ page_history.py    # ⑤ 이력 (리허설 캐시 겸용)
│  └─ api.py                # /api/v1/screenings (확장 채널용 REST)
├─ core/
│  ├─ pipeline.py           # 오케스트레이터 (v1 이식 + 교차검증 + 인벤토리)
│  ├─ schemas.py            # v1 이식 + Inventory/CrossExam/Score 추가
│  ├─ llm/
│  │  ├─ base.py            # LLMClient 프로토콜 (추상화 계층)
│  │  ├─ gemini.py          # v1 gemini_client 이식
│  │  └─ hcx.py             # HyperCLOVA X (Clova Studio Chat Completions)
│  ├─ retrieval/
│  │  ├─ embedder_clova.py  # v1 이식 (폴백 버그 제거)
│  │  ├─ vectorstore.py     # v1 FAISS 이식 → Phase 1 pgvector 구현 추가
│  │  └─ hybrid.py          # Phase 1: BM25 + 벡터 + 리랭킹
│  ├─ ingest.py             # v1 이식 + 제재사례/AI기본법/윤리기준 코퍼스
│  ├─ scoring.py            # 컴플라이언스 점수
│  ├─ artifacts.py          # 처리방침/동의문구/체크리스트 생성 + DOCX 출력
│  └─ store.py              # SQLite 이력 (screenings, feedback)
├─ prompts/                 # pass1 / pass2 / cross_exam / chat / policy_draft
├─ data/corpus/             # 기존 3종 + ai_basic_act, ai_ethics, sanction_cases
├─ data/index/              # FAISS
├─ demo/                    # 데모 기획서 2종(위반 심은 샘플) + 캐시된 리포트 JSON
├─ deploy/                  # Dockerfile, docker-compose.yml, 배포 스크립트
└─ tests/
```

## 5. 파이프라인 v2

v1의 5단계에 3단계가 추가된다. **v1의 인용 무결성 검사·Parent-Child 확장은 그대로 계승.**

1. **문서 준비**: PDF 텍스트 레이어 감지 → 텍스트 직접 추출(있으면) / 이미지 렌더링(스캔본). DOCX는 python-docx로 텍스트 추출(텍스트 경로로 합류).
2. **Pass 1 (Gemini)**: OCR + 리스크 포인트 + **개인정보 인벤토리 추출**(신규 — 수집항목/목적/보유기간/제공·위탁/파기). 텍스트 입력 변형(pass1-text)을 별도 지원 → 재검사 루프의 기반.
3. **검색**: 리스크 포인트별 임베딩 → FAISS → **포인트당 top-3 근거 보장** 후 전역 예산(12청크) 배분. *(v1의 전역 풀 컷 문제 해결)* 도메인 선택 체크박스는 `law_id` 필터로 구현.
4. **Pass 2 (HCX 기본)**: RAG 판정 + 개선안. HCX는 JSON 지시 + 기존 `_validate_or_repair` 재사용(구조화 출력 스키마 강제는 Gemini 대비 약하므로 repair 경로가 필수 방어선).
5. **교차검증 (신규)**: 반론 에이전트가 전체 findings에 대해 1회 호출로 반박 시도 → `uphold`(유지) / `weaken`(확신도 하향) / `overturn`(폐기→보류 이동). 진행 로그에 반론 내용을 그대로 스트리밍(발표 연출 핵심).
6. **인용 무결성 검사**: v1 로직 그대로.
7. **점수 산출**: `score = max(0, 100 − 15·critical − 7·medium − 3·low − 2·inconclusive)`.
8. **저장**: SQLite에 리포트 전체 JSON + 점수 + 메타 기록 → 이력 탭·재검사 비교·리허설 캐시가 전부 이 테이블 하나에서 나옴.

**재검사 루프**: 결과 탭에서 채택된 수정안들을 full_text에 치환 → pass1-text 경로로 재실행 → 이전 실행과 점수·finding diff 비교 뷰.

**버그 제거(확정)**: 쿼리 임베딩 실패 시 랜덤 폴백 → 명시적 에러 + 해당 포인트 보류 처리. `print()` 디버그 제거, `apigw_key`/`app_id` 사(死) 파라미터 제거.

## 6. 스키마 추가분 (core/schemas.py)

```python
class InventoryItem(BaseModel):      # 개인정보 인벤토리 (Pass 1 확장)
    item: str                        # "이메일 주소"
    category: str                    # 일반/고유식별/민감정보
    purpose: str
    retention: str                   # 미기재 시 "명시 없음" — 그 자체가 finding 후보
    third_party: str                 # 제공/위탁 대상 (없으면 "")
    lifecycle_stages: list[str]      # ["수집","이용","제공","파기"] 중 언급된 단계

class CrossExamResult(BaseModel):    # 교차검증 (Pass 2.5)
    point_id: str
    verdict: Literal["uphold", "weaken", "overturn"]
    rebuttal: str                    # 반론 요지 (UI 로그·배지에 노출)

class ScreeningRecord(BaseModel):    # SQLite 이력 행
    id: str; created_at: str; filename: str
    score: int; report: ScreeningReport
    inventory: list[InventoryItem]
    parent_id: str | None            # 재검사 체인 추적
```

`Finding`에 `cross_exam: CrossExamResult | None`, `sanction_refs: list[str]`(제재사례 chunk 인용) 필드 추가.

## 7. LLM 추상화 계층

```python
class LLMClient(Protocol):
    def generate_json(self, system: str, user: str, schema: type[BaseModel],
                      images: list[bytes] | None = None) -> BaseModel: ...
    def generate_stream(self, system: str, user: str) -> Iterator[str]: ...  # 챗용
```

- `gemini.py`: v1 이식. Pass 1 전담(멀티모달) + Pass 2 대체 옵션.
- `hcx.py`: Clova Studio Chat Completions(HCX-005 계열). Pass 2·교차검증·챗·산출물 기본값.
- 설정: `.env`의 `PASS2_PROVIDER=hcx|gemini` — 발표 중 HCX 장애 시 환경변수 하나로 Gemini 전환(백업 플랜).

## 8. UI 설계 (NiceGUI, 5개 탭)

탭 순서 = 발표 시나리오. 공통: 다크 테마, v1 CSS 자산(finding 카드·배지·diff) 이식.

| 탭 | 구성 요소 (NiceGUI) |
|---|---|
| ① 스크리닝 | `ui.upload` + 도메인 체크박스(개인정보법/AI기본법/윤리기준) + **샘플 문서 버튼 2개** → 시작 시 같은 화면이 5단계 스테퍼(`ui.stepper` 커스텀) + 실시간 로그 패널로 전환. 파이프라인 progress_callback → `asyncio.Queue` → UI 갱신 |
| ② 결과 리포트 | 점수·통계 카드 행 → 인벤토리 `ui.table` → 수명주기 맵(`ui.html` SVG, 인벤토리에서 정적 생성) → finding 카드(근거 조문+제재사례, before/after diff, 교차검증 배지, [수정안 채택]/[오탐 신고]) → 하단 [채택 반영 후 재검사] |
| ③ AI 법무 상담 | `ui.chat_message` 스트리밍. 컨텍스트 = 현재 리포트 + RAG 재검색. 답변 하단 근거 조문 칩 |
| ④ 산출물 생성 | [처리방침 초안] [동의 문구] [출시 전 체크리스트] 버튼 → 미리보기 → DOCX 다운로드(python-docx) |
| ⑤ 이력 | SQLite 목록, 클릭 시 결과 탭으로 로드(= **리허설 캐시 모드**), 재검사 체인 점수 추이 |

**오탐 신고**: 버튼 → feedback 테이블 기록만(Phase 0). Phase 1에서 골든셋 파이프라인화.

## 9. RAG·코퍼스 계획

- **코퍼스 추가**: AI기본법·국가AI윤리기준(루트에 원문 보유 — 즉시 인제스트 가능), 제재 사례(Phase 0: 개인정보위 의결 요약 10~20건 수작업 JSON, `source_type: "sanction"`), Phase 2: 자동 수집.
- **하이브리드 검색**(BM25+벡터+리랭킹)은 Phase 1. Phase 0는 FAISS + 포인트별 top-3 보장까지만.

## 10. NCP 배포 (Phase 0 기준)

- **서버**: NCP Server(VPC) 1대, Standard 2vCPU/8GB, Ubuntu 22.04, 공인 IP.
- **ACG**: 22(관리 IP만), 80 오픈.
- **구성**: 단일 Docker 컨테이너(uvicorn :80 직결 — nginx는 시간 나면). `docker compose up -d`.
- **볼륨**: `data/`(인덱스·SQLite·업로드) 호스트 마운트 → 컨테이너 재배포에도 이력·캐시 보존.
- **비밀키**: `.env`는 서버에서 직접 작성(git 제외 유지).
- **사전 워밍**: 배포 후 데모 문서 2종을 서버에서 미리 1회씩 실행 → 이력에 캐시 확보(라이브 실패 대비).

## 11. 마일스톤

### Phase 0 — D-1 컷라인 (오늘 저녁 ~ 내일 발표 전, ~10시간 예산)

> 원칙: **발표 화면에 보이는 것 우선.** 아래 Must만 완성돼도 발표 가능하도록 순서 배치.

| # | 작업 | 예산 | 등급 |
|---|---|---|---|
| 1 | NiceGUI 스캐폴드(5탭 골격+테마) + v1 파이프라인 이식 + 진행 로그 스트리밍 | 2.5h | **Must** |
| 2 | 결과 리포트 탭: 점수·통계·finding 카드·diff | 2h | **Must** |
| 3 | 샘플 문서 버튼 + SQLite 이력 + 리허설 캐시 로드 | 1h | **Must** |
| 4 | NCP 서버 생성 + Docker 배포 + 데모 사전 워밍 | 1.5h | **Must** |
| 5 | 상담 탭(RAG 챗, 기존 인프라 재활용) | 1h | Should |
| 6 | 인벤토리 추출(Pass1 스키마 확장) + 인벤토리 표 | 1h | Should |
| 7 | HCX Pass2 클라이언트 + `PASS2_PROVIDER` 토글 | 1h | Should |
| 8 | 교차검증 pass(단일 호출) + 로그 연출 + 배지 | 1h | Stretch |
| 9 | 수명주기 맵 SVG(인벤토리 기반 정적 렌더) | 1h | Stretch |
| 10 | 재검사 루프(pass1-text 경로 + 점수 비교) | 1.5h | Stretch |
| 11 | 처리방침 초안 생성 + DOCX 다운로드 | 1h | Stretch |

- **Must 합계 7h** — 여기까지가 마지노선. Should/Stretch는 남는 시간 순서대로.
- 시간이 밀리면: 8~11번은 **발표 슬라이드의 "로드맵"으로 전환**하고, UI에 탭·버튼은 남겨 "구현 중" 상태로 보여주는 것도 방법(단, 시연 동선에서는 제외).
- AI기본법·윤리기준 코퍼스 인제스트는 4번 배포 전에 서버에서 1회 실행(30분, 임베딩 API 시간 포함).

### Phase 1 — 발표 후 1~2주 (v2 완성)

PostgreSQL+pgvector 이관 · 하이브리드 검색+리랭킹 · DOCX 입력 · 제재사례 코퍼스 확충 · 간단 로그인 · 오탐 피드백→골든셋 평가 루프 · REST API 문서화 · 테스트/CI · 미완 Stretch 항목 수습.

### Phase 2 — 이후

국가법령정보센터 API 자동 수집·개정 감지 · 판례/유권해석 코퍼스 · 조직 대시보드 · 협업툴 봇 · 버전 비교 뷰 · 원문 하이라이팅.

## 12. 발표 리스크 플랜

| 리스크 | 대비 |
|---|---|
| 라이브 중 LLM API 장애/지연 | 이력 탭에서 사전 워밍된 결과 로드(리허설 캐시) — 시연 동선에 자연스럽게 편입 가능 |
| HCX 구조화 출력 불안정 | `_validate_or_repair` 1회 재시도 + `PASS2_PROVIDER=gemini` 즉시 전환 |
| NCP 서버 접속 불가 | 로컬 동일 Docker 이미지 기동(백업), 발표 자료에 서버 시연 녹화 GIF 포함 |
| 처리 시간 초과 | 데모 문서 2~3페이지 제한, 목표 90초. 그 시간은 진행 로그 연출로 채움 |
| 발표장 네트워크 | 휴대폰 테더링 예비, 캐시 모드는 오프라인에서도 동작(로컬 기동 시) |

## 13. 발표 시연 동선 (약 4분 30초)

1. 샘플 기획서 로드 → 도메인 3종 체크 → 시작 *(10초)*
2. 진행 화면: 인벤토리 추출 → 법령 검색 → **교차검증에서 판정 1건이 철회되는 로그** 강조 *(90초, 시스템 설명 병행)*
3. 결과 탭: 점수 → 치명적 finding 카드(근거 조문+제재사례+diff) *(60초)*
4. 상담 탭: "법정대리인 동의만 받으면 해결되나요?" *(30초)*
5. 수정안 채택 → 재검사 → **점수 상승** *(45초)* — 10번 미구현 시 생략하고 이력 탭 비교로 대체
6. 산출물 탭: 처리방침 초안 → DOCX 다운로드 *(30초)*
