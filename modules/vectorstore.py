"""FAISS 기반 벡터 저장소. 로컬 디스크 영속화 지원."""
import json
import logging
from pathlib import Path
from typing import Optional

import faiss
import numpy as np

from modules.schemas import ChunkMeta, RetrievedChunk

logger = logging.getLogger(__name__)


class VectorStore:
    """FAISS IndexFlatIP 기반 벡터 저장소.
    
    L2 정규화된 벡터에 대해 내적 검색 = 코사인 유사도 검색.
    메타데이터는 metadata.jsonl에 벡터 인덱스와 1:1 정렬로 저장.
    """
    
    INDEX_FILENAME = "faiss.index"
    META_FILENAME = "metadata.jsonl"
    
    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.index: Optional[faiss.IndexFlatIP] = None
        self.metadatas: list[ChunkMeta] = []
        # parent_id → 전체 텍스트 매핑 (expand_to_parent용)
        self._parent_texts: dict[str, str] = {}
    
    def build(self, vectors: np.ndarray, metadatas: list[ChunkMeta]) -> None:
        """인덱스 구축.
        
        Args:
            vectors: L2 정규화된 벡터 배열 (N, dim)
            metadatas: 벡터와 1:1 대응하는 메타데이터 목록
        """
        assert vectors.shape[0] == len(metadatas), "벡터 수와 메타데이터 수 불일치"
        assert vectors.shape[1] == self.dim, f"벡터 차원 불일치: {vectors.shape[1]} != {self.dim}"
        
        self.index = faiss.IndexFlatIP(self.dim)
        self.index.add(vectors.astype(np.float32))
        self.metadatas = metadatas
        
        # Parent 텍스트 매핑 구축
        self._build_parent_map()
        
        logger.info(f"FAISS 인덱스 구축 완료: {self.index.ntotal}개 벡터")
    
    def _build_parent_map(self) -> None:
        """parent_id가 없는 (= 조 전체) 청크의 텍스트를 부모 맵에 등록."""
        self._parent_texts = {}
        for meta in self.metadatas:
            if meta.clause_no is None:
                # 이 청크 자체가 parent (조 전체)
                self._parent_texts[meta.chunk_id] = meta.text
            elif meta.parent_id:
                # parent_id로 참조되는 텍스트 집계
                if meta.parent_id not in self._parent_texts:
                    # parent 자체가 별도 청크로 존재하지 않을 수 있으므로
                    # child들의 텍스트를 모아서 parent 텍스트 구성
                    parent_children = [
                        m.text for m in self.metadatas
                        if m.parent_id == meta.parent_id or m.chunk_id == meta.parent_id
                    ]
                    if parent_children:
                        self._parent_texts[meta.parent_id] = "\n".join(parent_children)
    
    def search(
        self,
        qvec: np.ndarray,
        k: int = 4,
        threshold: float = 0.35,
    ) -> list[RetrievedChunk]:
        """Top-k 벡터 검색 + 유사도 임계값 필터.
        
        Args:
            qvec: 쿼리 벡터 (dim,) — L2 정규화 가정
            k: 반환할 최대 청크 수
            threshold: 최소 유사도 임계값 (코사인 유사도)
        
        Returns:
            임계값 이상의 검색 결과 (점수 내림차순)
        """
        if self.index is None or self.index.ntotal == 0:
            logger.warning("빈 인덱스에서 검색 시도")
            return []
        
        qvec = qvec.reshape(1, -1).astype(np.float32)
        scores, indices = self.index.search(qvec, min(k, self.index.ntotal))
        
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or score < threshold:
                continue
            meta = self.metadatas[idx]
            results.append(RetrievedChunk(
                chunk_id=meta.chunk_id,
                score=float(score),
                meta=meta,
            ))
        
        return results
    
    def get_all_vectors(self) -> np.ndarray:
        """인덱스에 저장된 원본 벡터 전체를 복원한다 (증분 인제스트용)."""
        if self.index is None or self.index.ntotal == 0:
            return np.zeros((0, self.dim), dtype=np.float32)
        return self.index.reconstruct_n(0, self.index.ntotal)

    def expand_to_parent(self, chunk: RetrievedChunk) -> RetrievedChunk:
        """항(子) 청크 히트 시 조문(親) 전체 텍스트로 컨텍스트 확장.
        
        단서 조항이 항 밖에 있는 경우의 오판을 방지한다.
        """
        parent_id = chunk.meta.parent_id
        if parent_id and parent_id in self._parent_texts:
            expanded_meta = chunk.meta.model_copy(update={
                "text": self._parent_texts[parent_id]
            })
            return RetrievedChunk(
                chunk_id=chunk.chunk_id,
                score=chunk.score,
                meta=expanded_meta,
            )
        return chunk
    
    def save(self, path: Path) -> None:
        """인덱스 + 메타데이터를 디스크에 영속화."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        
        if self.index is not None:
            faiss.write_index(self.index, str(path / self.INDEX_FILENAME))
        
        with open(path / self.META_FILENAME, "w", encoding="utf-8") as f:
            for meta in self.metadatas:
                f.write(meta.model_dump_json() + "\n")
        
        logger.info(f"인덱스 저장 완료: {path}")
    
    def load(self, path: Path) -> None:
        """디스크에서 인덱스 + 메타데이터 복원."""
        path = Path(path)
        index_path = path / self.INDEX_FILENAME
        meta_path = path / self.META_FILENAME
        
        if not index_path.exists() or not meta_path.exists():
            raise FileNotFoundError(f"인덱스 파일을 찾을 수 없습니다: {path}")
        
        self.index = faiss.read_index(str(index_path))
        self.dim = self.index.d
        
        self.metadatas = []
        with open(meta_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self.metadatas.append(ChunkMeta.model_validate_json(line))
        
        self._build_parent_map()
        logger.info(f"인덱스 로드 완료: {self.index.ntotal}개 벡터, {len(self.metadatas)}개 메타데이터")
