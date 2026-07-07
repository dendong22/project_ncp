"""인용 무결성 검사. LLM 판정 결과의 할루시네이션을 코드 레벨에서 방어한다.

Gemini/HCX 등 어떤 Pass 2 클라이언트를 쓰든 동일한 규칙을 적용해야 하므로
공용 모듈로 분리한다.
"""
import logging
import re

from modules.schemas import RetrievedChunk, ScreeningReport

logger = logging.getLogger(__name__)


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def check_citation_integrity(
    report: ScreeningReport,
    chunks: list[RetrievedChunk],
    full_text: str,
) -> ScreeningReport:
    """인용 무결성 검사.

    1. chunk_id가 검색 결과에 존재하는지
    2. quoted_provision이 실제 청크 텍스트에 포함되는지 (공백 정규화)
    3. original_text가 OCR 전문에 포함되는지
    """
    valid_chunk_ids = {c.chunk_id for c in chunks}
    chunk_texts = {c.chunk_id: c.meta.text for c in chunks}

    valid_findings = []
    for finding in report.findings:
        is_valid = True

        for lb in finding.legal_bases:
            if lb.chunk_id not in valid_chunk_ids:
                logger.warning(
                    f"인용 무결성 위반: finding {finding.point_id}의 "
                    f"chunk_id '{lb.chunk_id}'가 검색 결과에 없음. 폐기."
                )
                is_valid = False
                break

            chunk_text_norm = _normalize_ws(chunk_texts.get(lb.chunk_id, ""))
            provision_norm = _normalize_ws(lb.quoted_provision)
            if provision_norm and provision_norm not in chunk_text_norm:
                logger.warning(
                    f"발췌 검증 실패: finding {finding.point_id}의 "
                    f"quoted_provision이 청크 텍스트에 없음. 폐기."
                )
                is_valid = False
                break

        if is_valid:
            orig_norm = _normalize_ws(finding.original_text)
            full_norm = _normalize_ws(full_text)
            if orig_norm and orig_norm not in full_norm:
                logger.warning(
                    f"원문 검증 실패: finding {finding.point_id}의 "
                    f"original_text가 OCR 전문에 없음. 폐기."
                )
                is_valid = False

        if is_valid:
            valid_findings.append(finding)
        elif finding.point_id not in report.inconclusive_points:
            report.inconclusive_points.append(finding.point_id)

    return ScreeningReport(
        findings=valid_findings,
        inconclusive_points=report.inconclusive_points,
        document_summary=report.document_summary,
        disclaimer=report.disclaimer,
    )
