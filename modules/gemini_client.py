"""Gemini API 연동 모듈. 2-Pass 스크리닝 파이프라인."""
import json
import re
import logging
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from modules.schemas import (
    Pass1Output, ScreeningReport, RiskPoint, RetrievedChunk, Finding
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class GeminiClient:
    """Gemini API 클라이언트. Pass 1(OCR) / Pass 2(판정) 분리."""
    
    def __init__(
        self,
        api_key: str,
        model_pass1: str = "gemini-3.5-flash",
        model_pass2: str = "gemini-3.5-flash",
    ):
        self.client = genai.Client(api_key=api_key)
        self.model_pass1 = model_pass1
        self.model_pass2 = model_pass2
        
        # 프롬프트 로드
        self._pass1_prompt = (PROMPTS_DIR / "pass1_ocr_extract.txt").read_text(encoding="utf-8")
        self._pass2_prompt = (PROMPTS_DIR / "pass2_screening.txt").read_text(encoding="utf-8")
    
    def run_pass1(self, images: list[bytes]) -> Pass1Output:
        """Pass 1: 멀티모달 OCR + 리스크 포인트 추출.
        
        Args:
            images: 페이지별 이미지 바이트 목록
        
        Returns:
            Pass1Output (OCR 전문 + 리스크 포인트)
        """
        # 이미지 파트 구성
        contents = []
        for i, img_bytes in enumerate(images):
            contents.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
        contents.append(types.Part.from_text(text=self._pass1_prompt))
        
        response = self.client.models.generate_content(
            model=self.model_pass1,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=Pass1Output,
            ),
        )
        
        return self._validate_or_repair(response.text, Pass1Output, self.model_pass1)
    
    def run_pass2(
        self,
        full_text: str,
        risk_points: list[RiskPoint],
        chunks: list[RetrievedChunk],
    ) -> ScreeningReport:
        """Pass 2: RAG 기반 위반 판정 + 개선안 생성.
        
        Args:
            full_text: OCR 전문 텍스트
            risk_points: 리스크 포인트 목록
            chunks: 벡터 검색으로 회수된 근거 청크
        
        Returns:
            ScreeningReport (위반 판정 + 개선안)
        """
        # 컨텍스트 조립
        risk_points_json = json.dumps(
            [rp.model_dump() for rp in risk_points],
            ensure_ascii=False, indent=2
        )
        legal_context = "\n\n".join([
            f"[chunk_id: {c.chunk_id}]\n"
            f"[hierarchy_path: {c.meta.hierarchy_path}]\n"
            f"[source_type: {c.meta.source_type}]\n"
            f"{c.meta.text}"
            for c in chunks
        ])
        
        user_message = (
            f"<document>\n{full_text}\n</document>\n\n"
            f"<risk_points>\n{risk_points_json}\n</risk_points>\n\n"
            f"<legal_context>\n{legal_context}\n</legal_context>"
        )
        
        response = self.client.models.generate_content(
            model=self.model_pass2,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=user_message)],
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=self._pass2_prompt,
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=ScreeningReport,
            ),
        )
        
        report = self._validate_or_repair(response.text, ScreeningReport, self.model_pass2)
        
        # 인용 무결성 검사
        report = self._check_citation_integrity(report, chunks, full_text)
        
        return report
    
    def _validate_or_repair(
        self,
        raw: str,
        schema: type,
        model: str,
    ) -> Any:
        """JSON 파싱 → Pydantic 검증 → 실패 시 repair 재시도 1회."""
        # 코드펜스 제거
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
        
        # 1차 시도
        try:
            data = json.loads(cleaned)
            return schema.model_validate(data)
        except Exception as e:
            logger.warning(f"1차 파싱 실패: {e}. repair 재시도 중...")

            # repair 재시도
            repair_prompt = (
                f"이전 응답의 JSON 파싱/검증에 실패했습니다.\n"
                f"오류: {str(e)}\n"
                f"원본 응답:\n{raw[:2000]}\n\n"
                f"위 내용을 올바른 JSON으로 수정하여 다시 출력하세요. "
                f"JSON만 출력하고 다른 텍스트는 포함하지 마세요."
            )

            response = self.client.models.generate_content(
                model=model,
                contents=repair_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )

            cleaned = response.text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
                cleaned = re.sub(r"\n?```$", "", cleaned)

            data = json.loads(cleaned)
            return schema.model_validate(data)
    
    def _check_citation_integrity(
        self,
        report: ScreeningReport,
        chunks: list[RetrievedChunk],
        full_text: str,
    ) -> ScreeningReport:
        """인용 무결성 검사. 코드 레벨 할루시네이션 방어선.
        
        1. chunk_id가 검색 결과에 존재하는지
        2. quoted_provision이 실제 청크 텍스트에 포함되는지 (공백 정규화)
        3. original_text가 OCR 전문에 포함되는지
        """
        valid_chunk_ids = {c.chunk_id for c in chunks}
        chunk_texts = {c.chunk_id: c.meta.text for c in chunks}
        
        def normalize_ws(s: str) -> str:
            return re.sub(r'\s+', ' ', s).strip()
        
        valid_findings = []
        for finding in report.findings:
            is_valid = True
            
            # 1. chunk_id 존재 검사
            for lb in finding.legal_bases:
                if lb.chunk_id not in valid_chunk_ids:
                    logger.warning(
                        f"인용 무결성 위반: finding {finding.point_id}의 "
                        f"chunk_id '{lb.chunk_id}'가 검색 결과에 없음. 폐기."
                    )
                    is_valid = False
                    break
                
                # 2. quoted_provision 포함 검사 (공백 정규화)
                chunk_text_norm = normalize_ws(chunk_texts.get(lb.chunk_id, ""))
                provision_norm = normalize_ws(lb.quoted_provision)
                if provision_norm and provision_norm not in chunk_text_norm:
                    logger.warning(
                        f"발췌 검증 실패: finding {finding.point_id}의 "
                        f"quoted_provision이 청크 텍스트에 없음. 폐기."
                    )
                    is_valid = False
                    break
            
            # 3. original_text 포함 검사
            if is_valid:
                orig_norm = normalize_ws(finding.original_text)
                full_norm = normalize_ws(full_text)
                if orig_norm and orig_norm not in full_norm:
                    logger.warning(
                        f"원문 검증 실패: finding {finding.point_id}의 "
                        f"original_text가 OCR 전문에 없음. 폐기."
                    )
                    is_valid = False
            
            if is_valid:
                valid_findings.append(finding)
            else:
                # 폐기된 finding의 point_id를 inconclusive로 이동
                if finding.point_id not in report.inconclusive_points:
                    report.inconclusive_points.append(finding.point_id)
        
        return ScreeningReport(
            findings=valid_findings,
            inconclusive_points=report.inconclusive_points,
            document_summary=report.document_summary,
            disclaimer=report.disclaimer,
        )
