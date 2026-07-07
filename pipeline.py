"""스크리닝 파이프라인 오케스트레이터.

업로드 → Gemini Pass 1 (OCR + 리스크 포인트) → Clova 쿼리 임베딩 → 
FAISS 검색 → Gemini Pass 2 (RAG 판정) → ScreeningReport
"""
import io
import logging
from typing import Callable, Optional

import fitz  # pymupdf
import numpy as np

from modules.schemas import (
    Pass1Output, ScreeningReport, RiskPoint, RetrievedChunk
)
from modules.gemini_client import GeminiClient
from modules.embedder_clova import ClovaEmbedder
from modules.vectorstore import VectorStore

logger = logging.getLogger(__name__)

# 총 주입 예산: Pass 2에 전달할 최대 청크 수 (Parent 확장 후)
MAX_CONTEXT_CHUNKS = 12


def pdf_to_images(file_bytes: bytes, dpi: int = 200) -> list[bytes]:
    """PDF를 페이지별 PNG 이미지로 변환.
    
    Args:
        file_bytes: PDF 파일 바이트
        dpi: 렌더링 DPI (기본 200)
    
    Returns:
        페이지별 PNG 바이트 목록
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        # DPI 기반 확대 행렬
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        images.append(pix.tobytes("png"))
    
    doc.close()
    return images


def run_screening(
    file_bytes: bytes,
    mime_type: str,
    gemini: GeminiClient,
    embedder: ClovaEmbedder,
    vectorstore: VectorStore,
    progress_callback: Optional[Callable[[str, str], None]] = None,
) -> ScreeningReport:
    """전체 스크리닝 파이프라인 실행.
    
    Args:
        file_bytes: 업로드된 파일 바이트
        mime_type: 파일 MIME 타입
        gemini: Gemini API 클라이언트
        embedder: Clova 임베딩 클라이언트
        vectorstore: FAISS 벡터 저장소
        progress_callback: 진행 상황 콜백 (stage_name, detail)
    
    Returns:
        ScreeningReport
    """
    def _progress(stage: str, detail: str = ""):
        if progress_callback:
            progress_callback(stage, detail)
        logger.info(f"[{stage}] {detail}")
    
    # ── 1단계: 이미지 준비 ──
    _progress("준비", "문서를 이미지로 변환 중...")
    
    if mime_type == "application/pdf":
        images = pdf_to_images(file_bytes)
        _progress("준비", f"{len(images)}페이지 변환 완료")
    elif mime_type in ("image/png", "image/jpeg", "image/jpg"):
        images = [file_bytes]
        _progress("준비", "이미지 1장 로드 완료")
    else:
        raise ValueError(f"지원하지 않는 파일 형식: {mime_type}")
    
    # ── 2단계: Gemini Pass 1 (OCR + 리스크 포인트 추출) ──
    _progress("OCR", "Gemini Pass 1: 텍스트 추출 및 리스크 포인트 식별 중...")
    pass1_output: Pass1Output = gemini.run_pass1(images)
    
    risk_count = len(pass1_output.risk_points)
    _progress("OCR", f"완료 — {risk_count}개 리스크 포인트 식별")
    
    if risk_count == 0:
        _progress("완료", "리스크 포인트가 없습니다.")
        return ScreeningReport(
            findings=[],
            inconclusive_points=[],
            document_summary=pass1_output.full_text[:500] + "...",
            disclaimer="본 결과는 AI 사전 스크리닝이며 법률 자문을 대체하지 않습니다.",
        )
    
    # ── 3단계: 쿼리 임베딩 + FAISS 검색 ──
    _progress("검색", "관련 법령 조문 검색 중...")
    
    all_chunks: list[RetrievedChunk] = []
    no_result_points: list[str] = []  # 근거 미검색 포인트
    
    for rp in pass1_output.risk_points:
        try:
            qvec = embedder.embed_query(rp.query_text)
            results = vectorstore.search(qvec, k=8, threshold=0.35)
            
            if not results:
                no_result_points.append(rp.point_id)
                _progress("검색", f"{rp.point_id}: 근거 미검색")
            else:
                # Parent 확장
                expanded = [vectorstore.expand_to_parent(c) for c in results]
                all_chunks.extend(expanded)
                _progress("검색", f"{rp.point_id}: {len(results)}건 검색")
        except Exception as e:
            logger.warning(f"{rp.point_id} 임베딩/검색 실패: {e}")
            no_result_points.append(rp.point_id)
    
    # 중복 제거 (동일 parent_id → 1건) + 점수순 정렬
    seen_parents: set[str] = set()
    seen_chunk_ids: set[str] = set()
    unique_chunks: list[RetrievedChunk] = []
    
    # 점수 내림차순 정렬
    all_chunks.sort(key=lambda c: c.score, reverse=True)
    
    for chunk in all_chunks:
        parent_key = chunk.meta.parent_id or chunk.chunk_id
        if parent_key in seen_parents:
            continue
        if chunk.chunk_id in seen_chunk_ids:
            continue
        seen_parents.add(parent_key)
        seen_chunk_ids.add(chunk.chunk_id)
        unique_chunks.append(chunk)
    
    # 토큰 예산 컷
    context_chunks = unique_chunks[:MAX_CONTEXT_CHUNKS]
    _progress("검색", f"총 {len(context_chunks)}개 근거 청크 선별 완료")
    
    # ── 4단계: Gemini Pass 2 (RAG 판정) ──
    _progress("판정", "Gemini Pass 2: 위반 판정 및 개선안 생성 중...")
    
    # 근거 미검색 포인트는 Pass 2에서 제외
    active_risk_points = [
        rp for rp in pass1_output.risk_points
        if rp.point_id not in no_result_points
    ]
    
    if not active_risk_points and not context_chunks:
        _progress("완료", "검색된 근거가 없어 판정을 수행할 수 없습니다.")
        return ScreeningReport(
            findings=[],
            inconclusive_points=[rp.point_id for rp in pass1_output.risk_points],
            document_summary=pass1_output.full_text[:500] + "...",
            disclaimer="본 결과는 AI 사전 스크리닝이며 법률 자문을 대체하지 않습니다.",
        )
    
    # 디버깅용: 실제로 LLM에 전달되는 컨텍스트를 출력
    context_text = "\n\n".join(
        [f"[chunk_id: {c.chunk_id}]\n{c.meta.text}" for c in context_chunks]
    )
    print("========== LLM에 전달되는 실제 Context ==========")
    print(context_text)
    print("==================================================")

    report = gemini.run_pass2(
        full_text=pass1_output.full_text,
        risk_points=active_risk_points,
        chunks=context_chunks,
    )
    
    # 근거 미검색 포인트를 inconclusive에 병합
    existing_inconclusive = set(report.inconclusive_points)
    for pid in no_result_points:
        if pid not in existing_inconclusive:
            report.inconclusive_points.append(pid)
    
    _progress("완료", f"분석 완료 — {len(report.findings)}건 위반, {len(report.inconclusive_points)}건 보류")
    
    return report
