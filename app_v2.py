"""기획서 법무 스크리닝 AI v2 — NiceGUI 웹앱.

Streamlit v1(app.py)을 대체하는 발표용 버전. 실시간 파이프라인 로그,
컴플라이언스 점수, 개인정보 인벤토리, AI 법무 상담, 이력 관리를 제공한다.
"""
import asyncio
import logging
import os
import queue
import threading
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from nicegui import ui, run

from modules.gemini_client import GeminiClient
from modules.embedder_clova import ClovaEmbedder
from modules.vectorstore import VectorStore
from modules.llm_provider import get_pass2_client
from modules.store import HistoryStore
from modules.scoring import compute_score
from modules.schemas import ScreeningReport, Pass1Output, Finding, Severity, InventoryItem
from pipeline import run_screening
import modules.artifacts as artifacts
import ui_theme as theme

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).parent
DEMO_DIR = ROOT_DIR / "demo"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

DOMAIN_GROUPS = [
    ("privacy", "개인정보 보호법 (+시행령·AI가이드라인)", ["pipa", "pipa_decree", "ai_privacy_guide"]),
    ("ai_gov", "AI 거버넌스 (AI기본법·국가AI윤리기준)", ["ai_basic_act", "ai_ethics"]),
]

STAGE_ORDER = ["준비", "리스크 추출", "법령 검색", "판정", "교차검증", "완료"]
STAGE_LABELS = {
    "준비": "문서 분석",
    "리스크 추출": "리스크·인벤토리 추출",
    "법령 검색": "법령 검색",
    "판정": "위반 판정",
    "교차검증": "교차검증",
    "완료": "완료",
}


# ──────────────────────────────────────────────
# 리소스 (프로세스 전역 1회 로드)
# ──────────────────────────────────────────────
class Resources:
    gemini: GeminiClient
    pass2_client: object
    embedder: ClovaEmbedder
    vectorstore: VectorStore
    history: HistoryStore


_res: Optional[Resources] = None


def get_resources() -> Resources:
    global _res
    if _res is not None:
        return _res

    r = Resources()
    r.gemini = GeminiClient(api_key=os.getenv("GEMINI_API_KEY", ""))
    r.pass2_client = get_pass2_client(r.gemini)
    r.embedder = ClovaEmbedder(
        api_key=os.getenv("CLOVA_API_KEY", ""),
        apigw_key=os.getenv("CLOVA_APIGW_KEY", ""),
    )
    r.vectorstore = VectorStore(dim=1024)
    index_path = ROOT_DIR / "data" / "index"
    if (index_path / VectorStore.INDEX_FILENAME).exists():
        r.vectorstore.load(index_path)
    else:
        logger.warning("인덱스가 없습니다. python -m modules.ingest 를 먼저 실행하세요.")
    r.history = HistoryStore()

    _res = r
    return r


# ──────────────────────────────────────────────
# 세션 상태 (클라이언트 연결마다 1개)
# ──────────────────────────────────────────────
class SessionState:
    def __init__(self):
        self.file_bytes: Optional[bytes] = None
        self.mime_type: Optional[str] = None
        self.filename: str = ""
        self.report: Optional[ScreeningReport] = None
        self.pass1_output: Optional[Pass1Output] = None
        self.record_id: Optional[str] = None
        self.parent_record_id: Optional[str] = None
        self.score: int = 0
        self.chat_history: list[dict] = []
        self.reported_feedback: set[str] = set()
        self.domain_checks: dict[str, object] = {}  # key -> ui.checkbox
        # UI 핸들 (다른 탭에서 갱신하기 위해 보관)
        self.report_container = None
        self.tabs = None
        self.tab_report = None
        self.chat_container = None
        self.history_container = None
        self.upload_label = None


def resolve_selected_law_ids(state: SessionState) -> Optional[list[str]]:
    """도메인 체크박스 선택 상태 → law_id 필터 목록. 전부 선택 시 None(필터 없음)."""
    selected: list[str] = []
    all_selected = True
    for key, _label, law_ids in DOMAIN_GROUPS:
        cb = state.domain_checks.get(key)
        if cb is not None and cb.value:
            selected.extend(law_ids)
        else:
            all_selected = False
    if all_selected or not selected:
        return None
    return selected


# ──────────────────────────────────────────────
# 진행 로그 스트리밍 (파이프라인 스레드 → UI 폴링)
# ──────────────────────────────────────────────
class ProgressView:
    """5(6)단계 스텝 박스 + 실시간 로그 패널."""

    def __init__(self):
        self.step_html = ui.html("").classes("w-full")
        self.log_html = ui.html("").classes("w-full")
        self.log_lines: list[str] = []
        self.stage_state = {s: "pending" for s in STAGE_ORDER}
        self._render()

    def _render(self):
        boxes = []
        icon_map = {"pending": "&#9675;", "active": "&#9679;", "done": "&#10003;"}
        for s in STAGE_ORDER:
            st = self.stage_state[s]
            boxes.append(
                f'<div class="step-box step-{st}">{icon_map[st]}<br/>{STAGE_LABELS[s]}</div>'
            )
        self.step_html.set_content('<div class="step-row">' + "".join(boxes) + "</div>")
        log_body = "<br/>".join(self.log_lines[-300:]) or "대기 중..."
        self.log_html.set_content(f'<div class="log-panel">{log_body}</div>')

    def push(self, stage: str, detail: str):
        if stage in self.stage_state:
            idx = STAGE_ORDER.index(stage)
            for i, s in enumerate(STAGE_ORDER):
                if i < idx:
                    self.stage_state[s] = "done"
            self.stage_state[stage] = "done" if stage == "완료" else "active"
            if stage == "완료":
                for s in STAGE_ORDER:
                    self.stage_state[s] = "done"

        css = "log-line-active"
        if "철회" in detail:
            css = "log-line-overturn"
        elif "기각" in detail or "판정 유지" in detail:
            css = "log-line-uphold"
        elif stage == "완료":
            css = "log-line-done"
        self.log_lines.append(f'<span class="{css}">[{stage}]</span> {detail}')
        self._render()

    def reset(self):
        self.log_lines = []
        self.stage_state = {s: "pending" for s in STAGE_ORDER}
        self._render()


def build_screening_tab(state: SessionState):
    res = get_resources()

    ui.label("1. 문서 업로드").classes("text-lg")
    with ui.row().classes("w-full gap-3"):
        def handle_upload(e):
            async def _read():
                data = await e.file.read()
                state.file_bytes = data
                state.mime_type = e.file.content_type or ""
                state.filename = e.file.name
                upload_status.set_text(f"업로드됨: {e.file.name} ({len(data)//1024}KB)")
            ui.timer(0, _read, once=True)

        uploader = ui.upload(
            on_upload=handle_upload,
            max_file_size=20 * 1024 * 1024,
            auto_upload=True,
        ).props('accept=".pdf,.png,.jpg,.jpeg,.docx" label="기획서 업로드 (PDF/이미지/DOCX, 최대 20MB)"').classes("w-1/2")

        with ui.column().classes("gap-1"):
            ui.label("또는 샘플 기획서로 바로 시작").classes("text-sm text-grey")
            with ui.row().classes("gap-2"):
                ui.button("샘플: 경미한 위반", on_click=lambda: load_sample(state, upload_status, "sample_mild.docx"))
                ui.button("샘플: 심각한 위반", on_click=lambda: load_sample(state, upload_status, "sample_severe.docx"))

    upload_status = ui.label("업로드된 파일 없음").classes("text-sm text-grey")

    ui.label("2. 검토 법령 도메인").classes("text-lg mt-4")
    with ui.row().classes("gap-6"):
        for key, label, _law_ids in DOMAIN_GROUPS:
            cb = ui.checkbox(label, value=True)
            state.domain_checks[key] = cb

    ui.label("3. 스크리닝 시작").classes("text-lg mt-4")
    progress_view_holder: dict = {}

    async def on_start():
        if not state.file_bytes:
            ui.notify("먼저 문서를 업로드하거나 샘플을 선택하세요.", type="warning")
            return
        start_btn.disable()
        progress_area.clear()
        with progress_area:
            pv = ProgressView()
        progress_view_holder["pv"] = pv

        law_ids = resolve_selected_law_ids(state)
        log_q: "queue.Queue[tuple[str, str]]" = queue.Queue()

        def progress_cb(stg: str, detail: str):
            log_q.put((stg, detail))

        def drain():
            while True:
                try:
                    stg, detail = log_q.get_nowait()
                except queue.Empty:
                    break
                pv.push(stg, detail)

        timer = ui.timer(0.2, drain)

        def blocking_call():
            return run_screening(
                file_bytes=state.file_bytes,
                mime_type=state.mime_type,
                pass1_client=res.gemini,
                pass2_client=res.pass2_client,
                embedder=res.embedder,
                vectorstore=res.vectorstore,
                law_ids=law_ids,
                progress_callback=progress_cb,
            )

        try:
            report, pass1_output = await run.io_bound(blocking_call)
        except Exception as e:
            timer.cancel()
            drain()
            logger.exception("스크리닝 오류")
            ui.notify(f"스크리닝 실패: {e}", type="negative")
            start_btn.enable()
            return

        timer.cancel()
        drain()

        score = compute_score(report)
        record_id = res.history.save(
            filename=state.filename or "업로드 문서",
            score=score,
            report=report,
            pass1_output=pass1_output,
            parent_id=state.parent_record_id,
        )
        state.report = report
        state.pass1_output = pass1_output
        state.record_id = record_id
        state.score = score

        render_report_tab(state)
        render_history_tab(state)
        start_btn.enable()
        if state.tabs is not None and state.tab_report is not None:
            state.tabs.set_value(state.tab_report)
        ui.notify(f"스크리닝 완료 — 컴플라이언스 점수 {score}점", type="positive")

    start_btn = ui.button("법무 스크리닝 시작", on_click=on_start).props("color=primary").classes("mt-2")

    progress_area = ui.column().classes("w-full mt-4")


def load_sample(state: SessionState, upload_status, filename: str):
    path = DEMO_DIR / filename
    if not path.exists():
        ui.notify(f"샘플 파일이 아직 준비되지 않았습니다: {filename}", type="warning")
        return
    state.file_bytes = path.read_bytes()
    state.mime_type = DOCX_MIME
    state.filename = filename
    state.parent_record_id = None
    upload_status.set_text(f"샘플 로드됨: {filename}")
    ui.notify(f"{filename} 로드 완료. '법무 스크리닝 시작'을 눌러주세요.", type="info")


# ──────────────────────────────────────────────
# 결과 리포트 탭
# ──────────────────────────────────────────────
def render_stats_row(report: ScreeningReport, score: int):
    total = len(report.findings)
    critical = sum(1 for f in report.findings if f.severity == Severity.CRITICAL)
    medium = sum(1 for f in report.findings if f.severity == Severity.MEDIUM)
    low = sum(1 for f in report.findings if f.severity == Severity.LOW)
    inconclusive = len(report.inconclusive_points)

    cards = [
        ("컴플라이언스 점수", str(score), theme.score_color(score)),
        ("총 발견", str(total), "#58a6ff"),
        ("치명적", str(critical), "#D32F2F"),
        ("중간", str(medium), "#F9A825"),
        ("낮음", str(low), "#388E3C"),
        ("보류", str(inconclusive), "#667788"),
    ]
    with ui.row().classes("w-full gap-3"):
        for label, num, color in cards:
            ui.html(f"""
            <div class="stats-card" style="min-width:100px;">
                <p class="stats-number" style="color:{color};">{num}</p>
                <p class="stats-label">{label}</p>
            </div>
            """)


def render_inventory_table(inventory: list[InventoryItem]):
    if not inventory:
        return
    with ui.expansion("개인정보 처리 인벤토리", icon="table_view").classes("w-full mt-3"):
        rows = [
            {
                "item": it.item,
                "category": it.category,
                "purpose": it.purpose,
                "retention": it.retention,
                "third_party": it.third_party or "-",
                "stages": ", ".join(it.lifecycle_stages) or "-",
            }
            for it in inventory
        ]
        columns = [
            {"name": "item", "label": "항목", "field": "item", "align": "left"},
            {"name": "category", "label": "분류", "field": "category", "align": "left"},
            {"name": "purpose", "label": "수집 목적", "field": "purpose", "align": "left"},
            {"name": "retention", "label": "보유기간", "field": "retention", "align": "left"},
            {"name": "third_party", "label": "제3자 제공", "field": "third_party", "align": "left"},
            {"name": "stages", "label": "수명주기", "field": "stages", "align": "left"},
        ]
        ui.table(rows=rows, columns=columns, row_key="item").classes("w-full")

        render_lifecycle_map(inventory)


LIFECYCLE_STAGES = ["수집", "이용", "제공", "파기"]


def render_lifecycle_map(inventory: list[InventoryItem]):
    """개인정보 수명주기 맵: 수집→이용→제공→파기 단계별 항목 흐름."""
    cols_html = []
    for stage in LIFECYCLE_STAGES:
        items = [it.item for it in inventory if stage in it.lifecycle_stages]
        chips = "".join(f'<span class="chip">{name}</span>' for name in items) or '<span style="color:#556;">-</span>'
        cols_html.append(f"""
        <div style="flex:1; background:#0d1117; border:1px solid rgba(255,255,255,0.08); border-radius:8px; padding:0.8rem;">
            <div style="font-weight:600; color:#e0e0ff; margin-bottom:0.5rem;">{stage}</div>
            {chips}
        </div>
        """)
    arrow = '<div style="display:flex; align-items:center; color:#556; font-size:1.2rem;">&#8594;</div>'
    row = arrow.join(cols_html)
    ui.html(f'<div style="display:flex; gap:0.5rem; margin-top:1rem; width:100%;">{row}</div>')


def render_finding_card(state: SessionState, f: Finding, res: Resources):
    sev_cls = theme.SEVERITY_CLASS[f.severity]
    sev_emoji = theme.SEVERITY_EMOJI[f.severity]
    summary = f.violation_summary if len(f.violation_summary) <= 80 else f.violation_summary[:80] + "..."

    with ui.expansion(f"{sev_emoji} [{f.severity.value}] {f.point_id} — {summary}").classes("w-full"):
        header_extra = ""
        if f.cross_exam is not None:
            verdict_color = {"유지": "#6bcb77", "약화": "#ffd93d", "철회": "#ff6b6b"}.get(f.cross_exam.verdict.value, "#8899aa")
            header_extra = f"""
            <div style="margin-top:0.6rem; padding:0.6rem 0.9rem; background:rgba(255,255,255,0.03); border-radius:6px;">
                <span style="color:{verdict_color}; font-weight:600;">교차검증: {f.cross_exam.verdict.value}</span>
                <span style="color:#8899aa; font-size:0.82rem;"> — {f.cross_exam.rebuttal}</span>
            </div>
            """

        ui.html(f"""
        <div class="finding-card finding-{sev_cls}">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.8rem;">
                {theme.severity_badge_html(f.severity)}
                <span style="color:#667788; font-size:0.85rem;">확신도: {f.confidence:.0%}</span>
            </div>
            <p style="color:#c8d0dc; line-height:1.7;">{f.violation_summary}</p>
            <p style="color:#8899aa; font-size:0.85rem; margin-top:0.4rem;">중대성 근거: {f.severity_rationale}</p>
            {header_extra}
        </div>
        """)

        ui.label("법적 근거").classes("text-sm font-bold mt-2")
        for lb in f.legal_bases:
            ui.html(theme.legal_basis_html(lb))

        ui.label("수정 전/후 대비").classes("text-sm font-bold mt-2")
        ui.html(theme.diff_html(f.original_text, f.remediation.revised_text))

        if f.remediation.ui_plan_changes:
            ui.label("UI/기획 수정 방향").classes("text-sm font-bold mt-2")
            for change in f.remediation.ui_plan_changes:
                ui.label(f"- {change}").classes("text-sm")

        ui.label("수정 근거").classes("text-sm font-bold mt-2")
        ui.label(f.remediation.rationale).classes("text-sm")

        with ui.row().classes("gap-2 mt-3"):
            def report_false_positive(point_id=f.point_id):
                if state.record_id and point_id not in state.reported_feedback:
                    res.history.add_feedback(state.record_id, point_id)
                    state.reported_feedback.add(point_id)
                    ui.notify("오탐 신고가 접수되었습니다.", type="info")
                else:
                    ui.notify("이미 신고된 항목입니다.", type="warning")

            ui.button("오탐 신고", on_click=report_false_positive).props("flat")


def render_report_tab(state: SessionState):
    if state.report_container is None:
        return
    res = get_resources()
    container = state.report_container
    container.clear()

    if state.report is None:
        with container:
            ui.label("아직 스크리닝 결과가 없습니다. '스크리닝' 탭에서 문서를 분석해주세요.").classes("text-grey")
        return

    report = state.report
    with container:
        render_stats_row(report, state.score)
        render_inventory_table(state.pass1_output.inventory if state.pass1_output else [])

        with ui.expansion("문서 요약", icon="description").classes("w-full mt-3"):
            ui.label(report.document_summary)

        if report.findings:
            ui.label("위반 발견 사항").classes("text-lg mt-4")
            severity_order = {Severity.CRITICAL: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
            sorted_findings = sorted(report.findings, key=lambda f: severity_order.get(f.severity, 3))
            for f in sorted_findings:
                render_finding_card(state, f, res)
        else:
            ui.html('<div style="color:#6bcb77; padding:1rem;">위반 사항이 발견되지 않았습니다.</div>')

        if report.inconclusive_points:
            ui.label("판정 보류 — 근거 부족").classes("text-lg mt-4")
            for pid in report.inconclusive_points:
                ui.html(f'<div class="inconclusive-card"><strong>{pid}</strong> — 관련 법령 근거를 검색하지 못했거나 교차검증에서 판정이 철회되었습니다.</div>')

        with ui.row().classes("w-full justify-center mt-4"):
            def download_json():
                ui.download(report.model_dump_json(indent=2).encode("utf-8"), filename="screening_report.json")
            ui.button("JSON 보고서 다운로드", on_click=download_json).props("outline")

        ui.html(f'<div class="disclaimer">{report.disclaimer}</div>')


# ──────────────────────────────────────────────
# 이력 탭 — SQLite 목록 + 리허설 캐시 로드
# ──────────────────────────────────────────────
def render_history_tab(state: SessionState):
    if state.history_container is None:
        return
    res = get_resources()
    container = state.history_container
    container.clear()

    records = res.history.list_records(limit=50)
    with container:
        ui.button("새로고침", icon="refresh", on_click=lambda: render_history_tab(state)).props("outline")
        if not records:
            ui.label("아직 저장된 스크리닝 이력이 없습니다.").classes("text-grey mt-2")
            return

        for rec in records:
            with ui.row().classes("w-full items-center gap-3 mt-2").style(
                "border:1px solid rgba(255,255,255,0.08); border-radius:8px; padding:0.6rem 1rem;"
            ):
                ui.html(f'<span style="color:{theme.score_color(rec["score"])}; font-weight:700; font-size:1.1rem;">{rec["score"]}점</span>')
                ui.label(rec["filename"]).classes("flex-grow")
                ui.label(rec["created_at"][:19].replace("T", " ")).classes("text-sm text-grey")

                def make_loader(record_id: str):
                    def _load():
                        full = res.history.get(record_id)
                        if full is None:
                            ui.notify("레코드를 찾을 수 없습니다.", type="negative")
                            return
                        state.report = full["report"]
                        state.pass1_output = full["pass1_output"]
                        state.record_id = full["id"]
                        state.score = full["score"]
                        state.parent_record_id = full["id"]
                        render_report_tab(state)
                        state.tabs.set_value(state.tab_report)
                        ui.notify("이력에서 결과를 불러왔습니다.", type="positive")
                    return _load

                ui.button("불러오기", on_click=make_loader(rec["id"])).props("outline")


# ──────────────────────────────────────────────
# AI 법무 상담 탭 — RAG 기반 스트리밍 챗
# ──────────────────────────────────────────────
async def _consume_stream(gen_factory, on_update) -> str:
    """제너레이터를 백그라운드 스레드에서 소진하며 누적 텍스트를 주기적으로 전달."""
    q: "queue.Queue[str]" = queue.Queue()
    done = threading.Event()
    error_holder: dict = {}

    def worker():
        try:
            for delta in gen_factory():
                q.put(delta)
        except Exception as e:  # noqa: BLE001
            error_holder["error"] = e
        finally:
            done.set()

    threading.Thread(target=worker, daemon=True).start()

    full_text = ""
    while True:
        try:
            delta = q.get_nowait()
            full_text += delta
            on_update(full_text)
        except queue.Empty:
            if done.is_set() and q.empty():
                break
            await asyncio.sleep(0.05)

    if "error" in error_holder:
        raise error_holder["error"]
    return full_text


CHAT_SYSTEM_TEMPLATE = (
    "너는 개인정보 보호법 및 AI 규제 법무 상담 AI다. 반드시 아래 제공된 근거 조문 범위 내에서만 "
    "답변하고, 근거가 없으면 '제공된 자료에서 근거를 찾을 수 없어 판단이 어렵습니다'라고 답하라. "
    "사전 지식이나 추측으로 답변하지 마라. 답변 말미에 이 답변이 법률 자문이 아닌 참고용 설명임을 "
    "간단히 안내하라.\n\n"
    "<report_context>\n{report_context}\n</report_context>\n\n"
    "<legal_context>\n{legal_context}\n</legal_context>"
)


def build_chat_tab(state: SessionState):
    res = get_resources()

    ui.label("현재 스크리닝 결과와 법령 근거를 바탕으로 질의응답합니다.").classes("text-sm text-grey")
    chat_column = ui.column().classes("w-full mt-2").style("min-height:300px;")
    state.chat_container = chat_column

    with ui.row().classes("w-full gap-2 mt-2"):
        chat_input = ui.input(placeholder="예: 법정대리인 동의만 받으면 해결되나요?").classes("flex-grow")

        async def send_message():
            question = chat_input.value.strip() if chat_input.value else ""
            if not question:
                return
            chat_input.value = ""

            with chat_column:
                ui.chat_message(question, name="나", sent=True)

            try:
                qvec = await run.io_bound(res.embedder.embed_query, question)
                results = await run.io_bound(res.vectorstore.search, qvec, 5, 0.3)
            except Exception:
                results = []

            legal_context = "\n\n".join(
                f"[{r.meta.law_name} {r.meta.hierarchy_path}]\n{r.meta.text}" for r in results
            ) or "(관련 근거를 검색하지 못했습니다.)"

            report_context = "(현재 진행된 스크리닝 결과가 없습니다.)"
            if state.report:
                report_context = (
                    f"문서 요약: {state.report.document_summary}\n"
                    f"위반 {len(state.report.findings)}건 발견, 컴플라이언스 점수 {state.score}점."
                )

            system_prompt = CHAT_SYSTEM_TEMPLATE.format(
                report_context=report_context, legal_context=legal_context
            )

            with chat_column:
                assistant_msg = ui.chat_message(name="법무 상담 AI", sent=False)
                with assistant_msg:
                    content_label = ui.markdown("...")

            def on_update(text: str):
                content_label.set_content(text)

            history_payload = list(state.chat_history)

            def gen_factory():
                return res.pass2_client.chat_stream(system_prompt, question, history_payload)

            try:
                full_text = await _consume_stream(gen_factory, on_update)
            except Exception as e:  # noqa: BLE001
                content_label.set_content(f"오류가 발생했습니다: {e}")
                return

            if not full_text.strip():
                content_label.set_content("(응답을 받지 못했습니다. 다시 시도해주세요.)")
                return

            state.chat_history.append({"role": "user", "content": question})
            state.chat_history.append({"role": "assistant", "content": full_text})

        chat_input.on("keydown.enter", send_message)
        ui.button("전송", on_click=send_message).props("color=primary")


# ──────────────────────────────────────────────
# 산출물 생성 탭 — 개인정보 처리방침 초안·동의 문구
# ──────────────────────────────────────────────
def build_artifacts_tab(state: SessionState):
    res = get_resources()
    ui.label("스크리닝 결과의 개인정보 인벤토리를 바탕으로 컴플라이언스 산출물을 생성합니다.").classes("text-sm text-grey")

    output_area = ui.column().classes("w-full mt-3")

    async def generate(title: str, gen_fn, docx_filename: str):
        if state.pass1_output is None or not state.pass1_output.inventory:
            ui.notify("먼저 스크리닝을 실행해 개인정보 인벤토리를 확보하세요.", type="warning")
            return
        output_area.clear()
        with output_area:
            ui.label(f"{title} 생성 중...").classes("text-grey")

        try:
            text = await run.io_bound(gen_fn, res.pass2_client, state.pass1_output.inventory)
        except Exception as e:  # noqa: BLE001
            output_area.clear()
            with output_area:
                ui.label(f"생성 실패: {e}").classes("text-negative")
            return

        output_area.clear()
        with output_area:
            ui.label(title).classes("text-lg")
            ui.markdown(text).classes("w-full")

            def download():
                data = artifacts.markdown_to_docx_bytes(title, text)
                ui.download(data, filename=docx_filename)

            ui.button("DOCX 다운로드", on_click=download).props("outline")

    with ui.row().classes("gap-2"):
        ui.button(
            "개인정보 처리방침 초안 생성",
            on_click=lambda: generate("개인정보 처리방침 (초안)", artifacts.generate_privacy_policy, "privacy_policy_draft.docx"),
        )
        ui.button(
            "동의 문구 생성",
            on_click=lambda: generate("동의 UI 문구 (초안)", artifacts.generate_consent_copy, "consent_copy_draft.docx"),
        )


# ──────────────────────────────────────────────
# 페이지
# ──────────────────────────────────────────────
@ui.page("/")
def main_page():
    ui.add_head_html(f"<style>{theme.THEME_CSS}</style>")
    state = SessionState()

    ui.html("""
    <div class="main-header">
        <h1>기획서 법무 스크리닝 AI</h1>
        <p>기획서를 업로드하면 개인정보 보호법 및 AI 규제 위반 여부를 자동으로 스크리닝합니다.</p>
    </div>
    """)

    with ui.tabs().classes("w-full") as tabs:
        tab_screen = ui.tab("스크리닝")
        tab_report = ui.tab("결과 리포트")
        tab_chat = ui.tab("AI 법무 상담")
        tab_artifacts = ui.tab("산출물 생성")
        tab_history = ui.tab("이력")

    state.tabs = tabs
    state.tab_report = tab_report

    with ui.tab_panels(tabs, value=tab_screen).classes("w-full"):
        with ui.tab_panel(tab_screen):
            build_screening_tab(state)
        with ui.tab_panel(tab_report):
            state.report_container = ui.column().classes("w-full")
            render_report_tab(state)
        with ui.tab_panel(tab_chat):
            build_chat_tab(state)
        with ui.tab_panel(tab_artifacts):
            build_artifacts_tab(state)
        with ui.tab_panel(tab_history):
            state.history_container = ui.column().classes("w-full")
            render_history_tab(state)


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        title="기획서 법무 스크리닝 AI",
        dark=True,
        language="ko-KR",
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8080")),
        show=False,
        reload=False,
        storage_secret=os.getenv("NICEGUI_STORAGE_SECRET", "dev-only-secret-change-in-prod"),
    )
