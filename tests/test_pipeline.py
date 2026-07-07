import unittest
from unittest.mock import MagicMock

from modules.schemas import Pass1Output, RiskPoint, ScreeningReport
from pipeline import run_screening


class PipelineTests(unittest.TestCase):
    def test_run_screening_uses_higher_topk_for_retrieval(self):
        gemini = MagicMock()
        gemini.run_pass1.return_value = Pass1Output(
            full_text="문서 본문",
            ocr_confidence_notes=[],
            risk_points=[
                RiskPoint(
                    point_id="RP-01",
                    quoted_text="동의 없이 수집",
                    location_hint="페이지 1",
                    risk_category="수집·이용",
                    query_text="개인정보 수집 동의",
                )
            ],
        )
        gemini.run_pass2.return_value = ScreeningReport(
            findings=[],
            inconclusive_points=[],
            document_summary="요약",
            disclaimer="본 결과는 AI 사전 스크리닝이며 법률 자문을 대체하지 않습니다.",
        )

        embedder = MagicMock()
        embedder.embed_query.return_value = "vec"

        vectorstore = MagicMock()
        vectorstore.search.return_value = []

        run_screening(b"img", "image/png", gemini, embedder, vectorstore)

        vectorstore.search.assert_called_once_with("vec", k=8, threshold=0.35)


if __name__ == "__main__":
    unittest.main()
