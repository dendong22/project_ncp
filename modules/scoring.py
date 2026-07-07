"""컴플라이언스 점수 산출."""
from modules.schemas import ScreeningReport, Severity

WEIGHTS = {Severity.CRITICAL: 15, Severity.MEDIUM: 7, Severity.LOW: 3}
INCONCLUSIVE_WEIGHT = 2


def compute_score(report: ScreeningReport) -> int:
    """100점 만점에서 위반 중대성별 감점을 적용한 컴플라이언스 점수."""
    penalty = sum(WEIGHTS[f.severity] for f in report.findings)
    penalty += INCONCLUSIVE_WEIGHT * len(report.inconclusive_points)
    return max(0, 100 - penalty)
