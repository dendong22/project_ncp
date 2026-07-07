"""기획서 법무 스크리닝 AI 에이전트 — Streamlit UI."""
import json
import os
import logging
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from modules.schemas import ScreeningReport, Finding, Severity
from modules.gemini_client import GeminiClient
from modules.embedder_clova import ClovaEmbedder
from modules.vectorstore import VectorStore
from pipeline import run_screening

load_dotenv()
logger = logging.getLogger(__name__)

# ── 페이지 설정 ──
st.set_page_config(
    page_title="기획서 법무 스크리닝 AI",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── 커스텀 CSS ──
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700;900&display=swap');

/* === Global === */
.stApp {
    font-family: 'Noto Sans KR', sans-serif;
}

/* === Header === */
.main-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    padding: 2.5rem 2rem;
    border-radius: 16px;
    margin-bottom: 2rem;
    border: 1px solid rgba(255,255,255,0.08);
    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
}
.main-header h1 {
    color: #e0e0ff;
    font-size: 2rem;
    font-weight: 700;
    margin: 0;
    letter-spacing: -0.5px;
}
.main-header p {
    color: #8899cc;
    font-size: 0.95rem;
    margin-top: 0.5rem;
}

/* === Upload Area === */
.upload-zone {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
    border: 2px dashed rgba(88, 166, 255, 0.3);
    border-radius: 16px;
    padding: 2rem;
    text-align: center;
    transition: all 0.3s ease;
}
.upload-zone:hover {
    border-color: rgba(88, 166, 255, 0.6);
    box-shadow: 0 0 20px rgba(88, 166, 255, 0.1);
}

/* === Severity Badges === */
.severity-badge {
    display: inline-block;
    padding: 4px 14px;
    border-radius: 20px;
    font-size: 0.8rem;
    font-weight: 600;
    letter-spacing: 0.5px;
}
.severity-critical {
    background: rgba(211, 47, 47, 0.15);
    color: #ff6b6b;
    border: 1px solid rgba(211, 47, 47, 0.3);
}
.severity-medium {
    background: rgba(249, 168, 37, 0.15);
    color: #ffd93d;
    border: 1px solid rgba(249, 168, 37, 0.3);
}
.severity-low {
    background: rgba(56, 142, 60, 0.15);
    color: #6bcb77;
    border: 1px solid rgba(56, 142, 60, 0.3);
}

/* === Finding Cards === */
.finding-card {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1rem;
    border-left: 4px solid;
    box-shadow: 0 4px 16px rgba(0,0,0,0.2);
}
.finding-critical { border-left-color: #D32F2F; }
.finding-medium { border-left-color: #F9A825; }
.finding-low { border-left-color: #388E3C; }

/* === Legal Quote === */
.legal-quote {
    background: rgba(88, 166, 255, 0.05);
    border-left: 3px solid #58a6ff;
    padding: 0.8rem 1.2rem;
    border-radius: 0 8px 8px 0;
    margin: 0.8rem 0;
    font-size: 0.9rem;
    color: #b0b8c8;
}

/* === Diff Display === */
.diff-before {
    background: rgba(211, 47, 47, 0.08);
    border-left: 3px solid #D32F2F;
    padding: 0.8rem 1.2rem;
    border-radius: 0 8px 8px 0;
    margin: 0.4rem 0;
    font-size: 0.85rem;
}
.diff-after {
    background: rgba(56, 142, 60, 0.08);
    border-left: 3px solid #388E3C;
    padding: 0.8rem 1.2rem;
    border-radius: 0 8px 8px 0;
    margin: 0.4rem 0;
    font-size: 0.85rem;
}

/* === Stats Cards === */
.stats-card {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
    border-radius: 12px;
    padding: 1.2rem;
    text-align: center;
    border: 1px solid rgba(255,255,255,0.06);
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}
.stats-number {
    font-size: 2rem;
    font-weight: 700;
    margin: 0;
}
.stats-label {
    color: #8899aa;
    font-size: 0.8rem;
    margin-top: 0.3rem;
}

/* === Inconclusive Section === */
.inconclusive-card {
    background: rgba(249, 168, 37, 0.05);
    border: 1px solid rgba(249, 168, 37, 0.2);
    border-radius: 12px;
    padding: 1.2rem;
    margin-bottom: 0.8rem;
}

/* === Disclaimer === */
.disclaimer {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 8px;
    padding: 1rem;
    font-size: 0.8rem;
    color: #667788;
    text-align: center;
    margin-top: 2rem;
}

/* Hide Streamlit branding */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)


# ── 리소스 로드 ──
@st.cache_resource
def load_resources():
    """인덱스·클라이언트 1회 로드."""
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    clova_key = os.getenv("CLOVA_API_KEY", "")
    clova_apigw = os.getenv("CLOVA_APIGW_KEY", "")
    clova_app_id = os.getenv("CLOVA_EMBEDDING_APP_ID", "")
    
    # Gemini 클라이언트
    gemini = GeminiClient(api_key=gemini_key)
    
    # Clova 임베딩
    embedder = ClovaEmbedder(
        api_key=clova_key,
        apigw_key=clova_apigw,
        app_id=clova_app_id,
    )
    
    # FAISS 인덱스 로드
    vectorstore = VectorStore(dim=1024)
    index_path = Path("data/index")
    if index_path.exists() and (index_path / "faiss.index").exists():
        try:
            vectorstore.load(index_path)
        except Exception as e:
            logger.warning(f"인덱스 로드 실패: {e}")
    else:
        logger.warning("인덱스 파일이 존재하지 않습니다. 'python -m modules.ingest' 실행이 필요합니다.")
    
    return gemini, embedder, vectorstore


def get_severity_class(severity: Severity) -> str:
    if severity == Severity.CRITICAL:
        return "critical"
    elif severity == Severity.MEDIUM:
        return "medium"
    else:
        return "low"


def get_severity_emoji(severity: Severity) -> str:
    if severity == Severity.CRITICAL:
        return "🔴"
    elif severity == Severity.MEDIUM:
        return "🟡"
    else:
        return "🟢"


def render_finding_card(f: Finding, idx: int) -> None:
    """위반 발견 사항 카드 렌더링."""
    sev_class = get_severity_class(f.severity)
    sev_emoji = get_severity_emoji(f.severity)
    
    with st.expander(
        f"{sev_emoji} [{f.severity.value}] {f.point_id} — {f.violation_summary[:80]}..."
        if len(f.violation_summary) > 80
        else f"{sev_emoji} [{f.severity.value}] {f.point_id} — {f.violation_summary}",
        expanded=(idx == 0),
    ):
        # 위반 서술
        st.markdown(f"""
        <div class="finding-card finding-{sev_class}">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
                <span class="severity-badge severity-{sev_class}">{f.severity.value}</span>
                <span style="color:#667788; font-size:0.85rem;">확신도: {f.confidence:.0%}</span>
            </div>
            <p style="color:#c8d0dc; line-height:1.7;">{f.violation_summary}</p>
            <p style="color:#8899aa; font-size:0.85rem; margin-top:0.5rem;">📋 중대성 근거: {f.severity_rationale}</p>
        </div>
        """, unsafe_allow_html=True)
        
        # 법적 근거
        st.markdown("##### 📜 법적 근거")
        for lb in f.legal_bases:
            st.markdown(f"""
            <div class="legal-quote">
                <strong>{lb.law_name} {lb.article_ref}</strong>
                <br/><br/>
                "{lb.quoted_provision}"
                <br/><br/>
                <span style="color:#586878; font-size:0.8rem;">chunk_id: {lb.chunk_id}</span>
            </div>
            """, unsafe_allow_html=True)
        
        # 수정 전/후 대비
        st.markdown("##### ✏️ 수정 전/후 대비")
        st.markdown(f"""
        <div class="diff-before">
            <strong style="color:#ff6b6b;">⊖ 수정 전 (원문)</strong><br/>
            {f.original_text}
        </div>
        <div class="diff-after">
            <strong style="color:#6bcb77;">⊕ 수정 후 (제안)</strong><br/>
            {f.remediation.revised_text}
        </div>
        """, unsafe_allow_html=True)
        
        # UI/기획 수정 방향
        if f.remediation.ui_plan_changes:
            st.markdown("##### 🎨 UI/기획 수정 방향")
            for change in f.remediation.ui_plan_changes:
                st.markdown(f"- {change}")
        
        # 수정 근거
        st.markdown(f"##### 💡 수정 근거")
        st.markdown(f"{f.remediation.rationale}")


def render_report(report: ScreeningReport) -> None:
    """전체 스크리닝 보고서 렌더링."""
    # 통계 카드
    total = len(report.findings)
    critical = sum(1 for f in report.findings if f.severity == Severity.CRITICAL)
    medium = sum(1 for f in report.findings if f.severity == Severity.MEDIUM)
    low = sum(1 for f in report.findings if f.severity == Severity.LOW)
    inconclusive = len(report.inconclusive_points)
    
    cols = st.columns(5)
    stats = [
        ("총 발견", total, "#58a6ff"),
        ("🔴 치명적", critical, "#D32F2F"),
        ("🟡 중간", medium, "#F9A825"),
        ("🟢 낮음", low, "#388E3C"),
        ("⚪ 보류", inconclusive, "#667788"),
    ]
    for col, (label, num, color) in zip(cols, stats):
        col.markdown(f"""
        <div class="stats-card">
            <p class="stats-number" style="color:{color};">{num}</p>
            <p class="stats-label">{label}</p>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # 문서 요약
    with st.expander("📄 문서 요약", expanded=False):
        st.markdown(report.document_summary)
    
    # 위반 발견 사항
    if report.findings:
        st.markdown("### 🚨 위반 발견 사항")
        
        # 중대성 순 정렬
        severity_order = {Severity.CRITICAL: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
        sorted_findings = sorted(report.findings, key=lambda f: severity_order.get(f.severity, 3))
        
        for idx, finding in enumerate(sorted_findings):
            render_finding_card(finding, idx)
    else:
        st.success("✅ 위반 사항이 발견되지 않았습니다.")
    
    # 판정 보류
    if report.inconclusive_points:
        st.markdown("### ⚠️ 판정 보류 — 근거 부족")
        st.markdown("아래 포인트는 관련 법령 조문을 검색하지 못하여 판정을 보류하였습니다.")
        for pid in report.inconclusive_points:
            st.markdown(f"""
            <div class="inconclusive-card">
                <strong>{pid}</strong> — 관련 법령 근거를 검색하지 못했습니다. 수동 검토가 필요합니다.
            </div>
            """, unsafe_allow_html=True)
    
    # JSON 다운로드
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        report_json = report.model_dump_json(indent=2, ensure_ascii=False)
        st.download_button(
            label="📥 JSON 보고서 다운로드",
            data=report_json,
            file_name="screening_report.json",
            mime="application/json",
            use_container_width=True,
        )
    
    # 면책 조항
    st.markdown(f"""
    <div class="disclaimer">
        ⚖️ {report.disclaimer}
    </div>
    """, unsafe_allow_html=True)


def main():
    """메인 애플리케이션."""
    # 헤더
    st.markdown("""
    <div class="main-header">
        <h1>⚖️ 기획서 법무 스크리닝 AI</h1>
        <p>기획서를 업로드하면 개인정보 보호법 및 AI 가이드라인 위반 여부를 자동으로 스크리닝합니다.</p>
    </div>
    """, unsafe_allow_html=True)
    
    # 세션 상태 초기화
    if "report" not in st.session_state:
        st.session_state["report"] = None
    if "pass1_output" not in st.session_state:
        st.session_state["pass1_output"] = None
    
    # 리소스 로드
    try:
        gemini, embedder, vectorstore = load_resources()
    except Exception as e:
        st.error(f"⚠️ 리소스 로드 실패: {e}")
        st.info("`.env` 파일의 API 키 설정과 인덱스 구축 여부를 확인하세요.")
        return
    
    # 파일 업로드
    st.markdown('<div class="upload-zone">', unsafe_allow_html=True)
    uploaded_file = st.file_uploader(
        "기획서 파일을 업로드하세요",
        type=["png", "jpg", "jpeg", "pdf"],
        help="PNG, JPG, PDF 형식 지원 (최대 20MB)",
        key="file_uploader",
    )
    st.markdown('</div>', unsafe_allow_html=True)
    
    if uploaded_file:
        # 용량 검증
        if uploaded_file.size > 20 * 1024 * 1024:
            st.error("❌ 파일 크기가 20MB를 초과합니다.")
            return
        
        # 분석 버튼
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            analyze_btn = st.button(
                "🔍 법무 스크리닝 시작",
                use_container_width=True,
                type="primary",
            )
        
        if analyze_btn:
            st.session_state["report"] = None  # 이전 결과 초기화
            
            file_bytes = uploaded_file.read()
            mime_type = uploaded_file.type
            
            with st.status("🔄 스크리닝 진행 중...", expanded=True) as status:
                def progress_callback(stage: str, detail: str):
                    st.write(f"**[{stage}]** {detail}")
                
                try:
                    report = run_screening(
                        file_bytes=file_bytes,
                        mime_type=mime_type,
                        gemini=gemini,
                        embedder=embedder,
                        vectorstore=vectorstore,
                        progress_callback=progress_callback,
                    )
                    st.session_state["report"] = report
                    status.update(label="✅ 스크리닝 완료!", state="complete")
                except Exception as e:
                    status.update(label="❌ 오류 발생", state="error")
                    st.error(f"스크리닝 중 오류가 발생했습니다: {e}")
                    logger.exception("스크리닝 오류")
    
    # 결과 렌더링
    if st.session_state.get("report"):
        st.markdown("---")
        st.markdown("## 📊 스크리닝 결과")
        render_report(st.session_state["report"])


main()
