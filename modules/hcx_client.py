"""HyperCLOVA X (Clova Studio Chat Completions v3) 연동 모듈.

Pass 2(RAG 위반 판정) 및 AI 법무 상담 챗을 담당한다.
Pass 1(멀티모달 OCR)은 Gemini 전담이므로 여기서는 다루지 않는다.
"""
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional

import requests

from modules.schemas import RetrievedChunk, ScreeningReport, Finding, CrossExamBatch, CrossExamResult
from modules.citation_check import check_citation_integrity

logger = logging.getLogger(__name__)
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    return cleaned


class HCXClient:
    """Clova Studio Chat Completions v3 클라이언트."""

    ENDPOINT_TEMPLATE = "https://clovastudio.stream.ntruss.com/v3/chat-completions/{model}"

    def __init__(self, api_key: str, model: str = "HCX-007", timeout: int = 90):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._pass2_prompt = (PROMPTS_DIR / "pass2_screening.txt").read_text(encoding="utf-8")
        self._cross_exam_prompt = (PROMPTS_DIR / "cross_exam.txt").read_text(encoding="utf-8")

    def _headers(self, stream: bool = False) -> dict:
        return {
            "Authorization": self.api_key,
            "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "text/event-stream" if stream else "application/json",
        }

    def _token_params(self, max_tokens: int) -> dict:
        """모델별 토큰 파라미터 이름 차이를 흡수한다.

        HCX-007은 추론(thinking) 모델이라 maxCompletionTokens를 쓰고,
        구조화 출력(responseFormat)과 병행하려면 thinking을 꺼야 한다
        (켜져 있으면 completion 토큰이 thinking에 소모되어 content가 비게 됨).
        """
        if self.model.startswith("HCX-007"):
            return {"maxCompletionTokens": max_tokens, "thinking": {"effort": "none"}}
        return {"maxTokens": max_tokens}

    def _chat(
        self,
        messages: list[dict],
        response_schema: Optional[type] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        endpoint = self.ENDPOINT_TEMPLATE.format(model=self.model)
        payload: dict[str, Any] = {
            "messages": messages,
            "topP": 0.8,
            "topK": 0,
            "temperature": temperature,
            "repetitionPenalty": 1.1,
            **self._token_params(max_tokens),
        }
        if response_schema is not None:
            payload["responseFormat"] = {
                "type": "json",
                "schema": response_schema.model_json_schema(),
            }

        resp = requests.post(endpoint, headers=self._headers(), json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        status_code = data.get("status", {}).get("code")
        if status_code != "20000":
            raise RuntimeError(f"HCX API 오류: {data.get('status')}")

        return data["result"]["message"]["content"]

    def run_pass2(
        self,
        full_text: str,
        risk_points: list,
        chunks: list[RetrievedChunk],
    ) -> ScreeningReport:
        """Pass 2: RAG 기반 위반 판정 + 개선안 생성 (HCX-007)."""
        risk_points_json = json.dumps(
            [rp.model_dump() for rp in risk_points], ensure_ascii=False, indent=2
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

        messages = [
            {"role": "system", "content": self._pass2_prompt},
            {"role": "user", "content": user_message},
        ]

        raw = self._chat(messages, response_schema=ScreeningReport, temperature=0.1)
        report = self._validate_or_repair(raw, ScreeningReport)

        return check_citation_integrity(report, chunks, full_text)

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
        messages = [
            {"role": "system", "content": self._cross_exam_prompt},
            {"role": "user", "content": user_message},
        ]

        raw = self._chat(messages, response_schema=CrossExamBatch, temperature=0.2)
        batch = self._validate_or_repair(raw, CrossExamBatch)
        return batch.results

    def _validate_or_repair(self, raw: str, schema: type) -> Any:
        """JSON 파싱 → Pydantic 검증 → 실패 시 repair 재시도 1회."""
        cleaned = _strip_code_fence(raw)
        try:
            return schema.model_validate(json.loads(cleaned))
        except Exception as e:
            logger.warning(f"HCX 1차 파싱 실패: {e}. repair 재시도 중...")

            repair_messages = [
                {
                    "role": "system",
                    "content": "이전 응답의 JSON 파싱/검증에 실패했다. 올바른 JSON만 출력하라. "
                               "설명, 마크다운, 코드펜스를 절대 포함하지 마라.",
                },
                {
                    "role": "user",
                    "content": f"오류: {e}\n원본 응답:\n{raw[:2000]}",
                },
            ]
            raw2 = self._chat(repair_messages, response_schema=schema, temperature=0.0)
            cleaned2 = _strip_code_fence(raw2)
            return schema.model_validate(json.loads(cleaned2))

    def chat_stream(
        self,
        system_prompt: str,
        user_message: str,
        history: Optional[list[dict]] = None,
    ) -> Iterator[str]:
        """AI 법무 상담 탭용 스트리밍 챗. 델타 텍스트를 순차 yield한다."""
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": user_message})

        endpoint = self.ENDPOINT_TEMPLATE.format(model=self.model)
        payload = {
            "messages": messages,
            "topP": 0.8,
            "topK": 0,
            "temperature": 0.3,
            "repetitionPenalty": 1.1,
            **self._token_params(1024),
        }

        resp = requests.post(
            endpoint, headers=self._headers(stream=True), json=payload,
            timeout=self.timeout, stream=True,
        )
        resp.raise_for_status()

        current_event = None
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if line.startswith("event:"):
                current_event = line[len("event:"):].strip()
                continue
            if not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if not data_str:
                continue
            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            if current_event == "token":
                content = event.get("message", {}).get("content", "")
                if content:
                    yield content
            elif current_event == "error":
                raise RuntimeError(f"HCX 스트리밍 오류: {event}")

    def generate_text(self, system_prompt: str, user_message: str, temperature: float = 0.3) -> str:
        """자유 형식 텍스트 생성 (산출물 생성 등 구조화 스키마가 필요 없는 호출용)."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        return self._chat(messages, response_schema=None, temperature=temperature, max_tokens=2048)
