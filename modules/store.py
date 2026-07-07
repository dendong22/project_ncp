"""스크리닝 이력 SQLite 저장소.

이력 탭 · 리허설 캐시 로드 · 재검사 체인 추적 · 오탐 피드백을
전부 이 파일 하나로 처리한다 (Phase 0 규모에 맞춘 최소 구성).
"""
import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from modules.schemas import Pass1Output, ScreeningReport

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS screenings (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    filename TEXT NOT NULL,
    score INTEGER NOT NULL,
    report_json TEXT NOT NULL,
    pass1_json TEXT NOT NULL,
    parent_id TEXT,
    is_seed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    screening_id TEXT NOT NULL,
    point_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    note TEXT DEFAULT ''
);
"""


class HistoryStore:
    """스크리닝 이력을 저장·조회하는 SQLite 래퍼."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save(
        self,
        filename: str,
        score: int,
        report: ScreeningReport,
        pass1_output: Pass1Output,
        parent_id: Optional[str] = None,
        is_seed: bool = False,
        record_id: Optional[str] = None,
    ) -> str:
        """스크리닝 결과 1건 저장. record_id 미지정 시 새 UUID 발급."""
        rid = record_id or str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screenings "
                "(id, created_at, filename, score, report_json, pass1_json, parent_id, is_seed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rid,
                    datetime.now(timezone.utc).isoformat(),
                    filename,
                    score,
                    report.model_dump_json(),
                    pass1_output.model_dump_json(),
                    parent_id,
                    1 if is_seed else 0,
                ),
            )
        return rid

    def list_records(self, limit: int = 50) -> list[dict]:
        """최신순 이력 목록 (요약 필드만)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, filename, score, parent_id, is_seed "
                "FROM screenings ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, record_id: str) -> Optional[dict]:
        """단일 레코드 전체 조회. report/pass1_output을 파싱해 반환."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM screenings WHERE id = ?", (record_id,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["report"] = ScreeningReport.model_validate_json(d.pop("report_json"))
        d["pass1_output"] = Pass1Output.model_validate_json(d.pop("pass1_json"))
        return d

    def add_feedback(self, screening_id: str, point_id: str, note: str = "") -> str:
        """오탐 신고 기록."""
        fid = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO feedback (id, screening_id, point_id, created_at, note) "
                "VALUES (?, ?, ?, ?, ?)",
                (fid, screening_id, point_id, datetime.now(timezone.utc).isoformat(), note),
            )
        logger.info(f"오탐 신고 접수: screening={screening_id} point={point_id}")
        return fid

    def get_chain(self, record_id: str) -> list[dict]:
        """재검사 체인 전체(부모→자식 순)를 점수 추이 비교용으로 반환."""
        record = self.get(record_id)
        if record is None:
            return []

        # 루트까지 거슬러 올라가기
        chain_ids = [record_id]
        cur = record
        while cur.get("parent_id"):
            parent = self.get(cur["parent_id"])
            if parent is None:
                break
            chain_ids.insert(0, parent["id"])
            cur = parent

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, filename, score FROM screenings "
                f"WHERE id IN ({','.join('?' * len(chain_ids))}) "
                "ORDER BY created_at ASC",
                chain_ids,
            ).fetchall()
        return [dict(r) for r in rows]
