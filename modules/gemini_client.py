"""Gemini API 연동 모듈. 2-Pass 스크리닝 파이프라인."""
import json
import re
import logging
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from modules.schemas import (
    Pass1Output, ScreeningReport, RiskPoint, RetrievedChunk, Finding, CrossExamBatch, CrossExamResult
)
from modules.citation_check import check_citation_integrity

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
        self._pass1_text_prompt = (PROMPTS_DIR / "pass1_text_extract.txt").read_text(encoding="utf-8")
        self._pass2_prompt = (PROMPTS_DIR / "pass2_screening.txt").read_text(encoding="utf-8")
        self._cross_exam_prompt = (PROMPTS_DIR / "cross_exam.txt").read_text(encoding="utf-8")

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

    def run_pass1_text(self, full_text: str) -> Pass1Output:
        """Pass 1 (텍스트 입력 경로): DOCX·텍스트 레이어 PDF·재검사 루프용.

        이미 텍스트가 확보된 문서에서 OCR 없이 리스크 포인트·인벤토리만 추출한다.

        Args:
            full_text: 이미 추출된 문서 전문

        Returns:
            Pass1Output (전달받은 전문 + 리스크 포인트 + 인벤토리)
        """
        user_message = f"<document>\n{full_text}\n</document>"

        response = self.client.models.generate_content(
            model=self.model_pass1,
            contents=[
                types.Content(role="user", parts=[types.Part.from_text(text=user_message)])
            ],
            config=types.GenerateContentConfig(
                system_instruction=self._pass1_text_prompt,
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=Pass1Output,
            ),
        )

        result = self._validate_or_repair(response.text, Pass1Output, self.model_pass1)
        # 전문은 원본 텍스트를 그대로 사용 (모델이 재생성/축약하지 않도록 강제)
        result.full_text = full_text
        return result
    
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
        report = check_citation_integrity(report, chunks, full_text)

        return report

    def run_cross_exam(
        self,
        full_text: str,
        findings: list[Finding],
        chunks: list[RetrievedChunk],
    ) -> list[CrossExamResult]:
        """반론 에이전트: 각 finding에 대해 반박을 시도해 오탐을 거른다."""
        if not findings:
            return []

        findings_json = json.dumps(
            [f.model_dump(mode="json") for f in findings], ensure_ascii=False, indent=2
        )
        legal_context = "\n\n".join([
            f"[chunk_id: {c.chunk_id}]\n{c.meta.text}" for c in chunks
        ])
        user_message = (
            f"<document>\n{full_text}\n</document>\n\n"
            f"<findings>\n{findings_json}\n</findings>\n\n"
            f"<legal_context>\n{legal_context}\n</legal_context>"
        )

        response = self.client.models.generate_content(
            model=self.model_pass2,
            contents=[
                types.Content(role="user", parts=[types.Part.from_text(text=user_message)])
            ],
            config=types.GenerateContentConfig(
                system_instruction=self._cross_exam_prompt,
                temperature=0.2,
                response_mime_type="application/json",
                response_schema=CrossExamBatch,
            ),
        )

        batch = self._validate_or_repair(response.text, CrossExamBatch, self.model_pass2)
        return batch.results

    def chat_stream(self, system_prompt: str, user_message: str, history: list[dict] | None = None):
        """AI 법무 상담 탭용 스트리밍 챗 (HCXClient.chat_stream과 동일 인터페이스)."""
        contents = []
        for h in (history or []):
            role = "model" if h["role"] == "assistant" else "user"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=h["content"])]))
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_message)]))

        stream = self.client.models.generate_content_stream(
            model=self.model_pass2,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_prompt, temperature=0.3),
        )
        for chunk in stream:
            if chunk.text:
                yield chunk.text

    def generate_text(self, system_prompt: str, user_message: str, temperature: float = 0.3) -> str:
        """자유 형식 텍스트 생성 (산출물 생성 등 구조화 스키마가 필요 없는 호출용)."""
        response = self.client.models.generate_content(
            model=self.model_pass2,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=user_message)])],
            config=types.GenerateContentConfig(system_instruction=system_prompt, temperature=temperature),
        )
        return response.text or ""

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
