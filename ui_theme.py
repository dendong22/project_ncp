"""NiceGUI 앱 공통 테마(CSS) 및 정적 HTML 조각 렌더링 헬퍼.

v1(Streamlit) app.py의 다크 테마 CSS 자산을 그대로 계승한다.
"""
from modules.schemas import Finding, LegalBasis, Severity

THEME_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700;900&display=swap');

body, .nicegui-content {
    font-family: 'Noto Sans KR', sans-serif;
}

.main-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    padding: 2rem 2rem;
    border-radius: 16px;
    margin-bottom: 1rem;
    border: 1px solid rgba(255,255,255,0.08);
    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
}
.main-header h1 { color: #e0e0ff; font-size: 1.8rem; font-weight: 700; margin: 0; letter-spacing: -0.5px; }
.main-header p { color: #8899cc; font-size: 0.9rem; margin-top: 0.4rem; }

.severity-badge {
    display: inline-block; padding: 4px 14px; border-radius: 20px;
    font-size: 0.8rem; font-weight: 600; letter-spacing: 0.5px;
}
.severity-critical { background: rgba(211,47,47,0.15); color: #ff6b6b; border: 1px solid rgba(211,47,47,0.3); }
.severity-medium { background: rgba(249,168,37,0.15); color: #ffd93d; border: 1px solid rgba(249,168,37,0.3); }
.severity-low { background: rgba(56,142,60,0.15); color: #6bcb77; border: 1px solid rgba(56,142,60,0.3); }

.finding-card {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
    border-radius: 12px; padding: 1.25rem; margin-bottom: 0.5rem;
    border-left: 4px solid; box-shadow: 0 4px 16px rgba(0,0,0,0.2); width: 100%;
}
.finding-critical { border-left-color: #D32F2F; }
.finding-medium { border-left-color: #F9A825; }
.finding-low { border-left-color: #388E3C; }

.legal-quote {
    background: rgba(88,166,255,0.05); border-left: 3px solid #58a6ff;
    padding: 0.7rem 1.1rem; border-radius: 0 8px 8px 0; margin: 0.6rem 0;
    font-size: 0.88rem; color: #b0b8c8;
}
.sanction-quote {
    background: rgba(211,47,47,0.05); border-left: 3px solid #D32F2F;
    padding: 0.7rem 1.1rem; border-radius: 0 8px 8px 0; margin: 0.6rem 0;
    font-size: 0.85rem; color: #d0a0a0;
}

.diff-before { background: rgba(211,47,47,0.08); border-left: 3px solid #D32F2F; padding: 0.7rem 1.1rem; border-radius: 0 8px 8px 0; font-size: 0.85rem; }
.diff-after { background: rgba(56,142,60,0.08); border-left: 3px solid #388E3C; padding: 0.7rem 1.1rem; border-radius: 0 8px 8px 0; font-size: 0.85rem; }

.stats-card {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
    border-radius: 12px; padding: 1rem; text-align: center;
    border: 1px solid rgba(255,255,255,0.06); box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}
.stats-number { font-size: 1.8rem; font-weight: 700; margin: 0; }
.stats-label { color: #8899aa; font-size: 0.78rem; margin-top: 0.2rem; }

.inconclusive-card { background: rgba(249,168,37,0.05); border: 1px solid rgba(249,168,37,0.2); border-radius: 12px; padding: 1rem; margin-bottom: 0.6rem; }

.disclaimer { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 0.9rem; font-size: 0.78rem; color: #667788; text-align: center; margin-top: 1.5rem; }

.log-panel {
    background: #0d1117; border: 1px solid rgba(255,255,255,0.08); border-radius: 8px;
    padding: 0.8rem 1rem; font-family: 'Consolas', monospace; font-size: 0.8rem;
    line-height: 1.9; color: #8899aa; max-height: 320px; overflow-y: auto; width: 100%;
}
.log-line-done { color: #6bcb77; }
.log-line-active { color: #e0e0ff; }
.log-line-overturn { color: #ff6b6b; }
.log-line-uphold { color: #ffd93d; }

.step-row { display: flex; gap: 8px; width: 100%; margin-bottom: 1rem; }
.step-box { flex: 1; text-align: center; padding: 10px 4px; border-radius: 8px; font-size: 0.8rem; }
.step-pending { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); color: #667788; }
.step-active { background: rgba(249,168,37,0.12); border: 1px solid rgba(249,168,37,0.3); color: #ffd93d; }
.step-done { background: rgba(56,142,60,0.12); border: 1px solid rgba(56,142,60,0.3); color: #6bcb77; }

.score-ring { text-align: center; }
.score-number { font-size: 2.4rem; font-weight: 700; }

.chip { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 0.75rem; margin: 2px 4px 2px 0; background: rgba(88,166,255,0.1); color: #79b8ff; border: 1px solid rgba(88,166,255,0.25); }
"""

SEVERITY_CLASS = {
    Severity.CRITICAL: "critical",
    Severity.MEDIUM: "medium",
    Severity.LOW: "low",
}
SEVERITY_EMOJI = {
    Severity.CRITICAL: "\U0001F534",
    Severity.MEDIUM: "\U0001F7E1",
    Severity.LOW: "\U0001F7E2",
}


def severity_badge_html(severity: Severity) -> str:
    cls = SEVERITY_CLASS[severity]
    return f'<span class="severity-badge severity-{cls}">{severity.value}</span>'


def legal_basis_html(lb: LegalBasis) -> str:
    return f"""
    <div class="legal-quote">
        <strong>{lb.law_name} {lb.article_ref}</strong><br/><br/>
        "{lb.quoted_provision}"<br/><br/>
        <span style="color:#586878; font-size:0.78rem;">chunk_id: {lb.chunk_id}</span>
    </div>
    """


def diff_html(original_text: str, revised_text: str) -> str:
    return f"""
    <div class="diff-before"><strong style="color:#ff6b6b;">&#8854; 수정 전 (원문)</strong><br/>{original_text}</div>
    <div class="diff-after"><strong style="color:#6bcb77;">&#8853; 수정 후 (제안)</strong><br/>{revised_text}</div>
    """


def score_color(score: int) -> str:
    if score >= 80:
        return "#6bcb77"
    if score >= 50:
        return "#ffd93d"
    return "#ff6b6b"
