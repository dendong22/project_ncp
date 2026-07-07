"""전 모듈이 공유하는 데이터 계약. LLM 출력 검증의 단일 진실 공급원(SSOT)."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# 1. 중대성 열거형
# ──────────────────────────────────────────────

class Severity(str, Enum):
    """위반 중대성 3단계 분류."""
    CRITICAL = "치명적"   # 명시적 법률 조항 위반, 제재/형사 리스크
    MEDIUM = "중간"       # 위반 소지 높음, 해석 여지 존재
    LOW = "낮음"          # 가이드라인 권고 미준수, 모범 사례 이탈


# ──────────────────────────────────────────────
# 2. Pass 1 관련 스키마 (OCR + 리스크 포인트 추출)
# ──────────────────────────────────────────────

class RiskPoint(BaseModel):
    """기획서에서 식별된 개별 법적 리스크 포인트."""
    point_id: str                     # "RP-01"
    quoted_text: str                  # 기획서 내 원문 인용 (OCR 결과 기준)
    location_hint: str                # 페이지/섹션 위치
    risk_category: str                # 예: "수집·이용", "제3자 제공", "자동화된 결정"
    query_text: str                   # 벡터 검색용 재구성 쿼리 (법률 용어로 정규화)


class Pass1Output(BaseModel):
    """Pass 1 (OCR + 리스크 포인트 추출) 출력 스키마."""
    full_text: str                    # 페이지 구분자를 포함한 OCR 전문
    ocr_confidence_notes: list[str]   # 판독 불가/저신뢰 영역 목록
    risk_points: list[RiskPoint] = Field(default_factory=list, max_length=10)


# ──────────────────────────────────────────────
# 3. Pass 2 관련 스키마 (위반 판정 + 개선안)
# ──────────────────────────────────────────────

class LegalBasis(BaseModel):
    """위반 판정의 법적 근거 (인용 무결성 검사 대상)."""
    chunk_id: str                     # 검색 결과의 chunk_id
    law_name: str                     # "개인정보 보호법"
    article_ref: str                  # "제15조 제1항 제1호"
    quoted_provision: str             # 근거 조문 원문 (청크 텍스트에서 발췌)


class Remediation(BaseModel):
    """위반 해소를 위한 개선안."""
    revised_text: str                 # 즉시 대체 가능한 수정 문안
    ui_plan_changes: list[str]        # UI/기획 수정 방향
    rationale: str                    # 수정안이 위반을 해소하는 논리


class Finding(BaseModel):
    """개별 위반 발견 사항."""
    point_id: str
    violation_summary: str            # 위반 서술 (단정형 금지, 판정 근거 포함)
    severity: Severity
    severity_rationale: str           # 루브릭 기준 적용 근거
    legal_bases: list[LegalBasis] = Field(min_length=1)
    original_text: str                # 기획서 원문 (수정 대상)
    remediation: Remediation
    confidence: float = Field(ge=0.0, le=1.0)


class ScreeningReport(BaseModel):
    """전체 스크리닝 보고서 — Pass 2 최종 출력."""
    findings: list[Finding]           # 위반 없으면 빈 배열 (강제 생성 금지)
    inconclusive_points: list[str]    # 근거 미검색으로 판정 보류된 point_id
    document_summary: str
    disclaimer: str                   # 법률 자문 아님 고지


# ──────────────────────────────────────────────
# 4. 벡터 DB / 인덱싱 관련 스키마
# ──────────────────────────────────────────────

class ChunkMeta(BaseModel):
    """FAISS 인덱스와 1:1 정렬되는 청크 메타데이터."""
    chunk_id: str                     # "pipa-a15-c1"
    source_type: str                  # "statute" / "decree" / "guideline"
    law_id: str                       # "pipa"
    law_name: str                     # "개인정보 보호법"
    article_no: str                   # "15"
    article_title: str                # "개인정보의 수집·이용"
    clause_no: Optional[str] = None   # 항 번호 (子 청크만)
    parent_id: Optional[str] = None   # Parent-Child 확장 참조
    hierarchy_path: str               # "개인정보 보호법 > 제3장 > 제15조 제1항"
    effective_date: str = ""          # 시행일
    keywords: list[str] = Field(default_factory=list)
    text: str                         # 조문 원문


class RetrievedChunk(BaseModel):
    """벡터 검색 결과로 반환되는 청크."""
    chunk_id: str
    score: float
    meta: ChunkMeta


# ──────────────────────────────────────────────
# 5. 인제스트 관련 스키마
# ──────────────────────────────────────────────

class ClauseData(BaseModel):
    """파싱된 개별 항(項) 데이터."""
    clause_no: str                    # "1", "2", ...
    text: str                         # 항 본문 (소속 호 포함)


class Article(BaseModel):
    """파싱된 개별 조(條) 데이터."""
    article_no: str                   # "15"
    title: str                        # "개인정보의 수집·이용"
    full_text: str                    # 조 전체 원문
    clauses: list[ClauseData] = Field(default_factory=list)


class ChunkConfig(BaseModel):
    """청킹 설정."""
    chunk_size: int = 1000             # 청크 본문 상한 길이(문자수)
    chunk_overlap: int = 200          # 인접 청크 간 중첩 길이(문자수)
    max_child_tokens: int = 1000      # 항 단위 토큰 상한
    guideline_target_tokens: int = 550  # 가이드라인 목표 청크 크기
    guideline_min_tokens: int = 400
    guideline_max_tokens: int = 700
