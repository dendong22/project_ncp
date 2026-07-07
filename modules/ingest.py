"""[오프라인] 법령/가이드라인 청킹 + FAISS 인덱스 구축 CLI."""
import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from modules.schemas import Article, ClauseData, ChunkMeta, ChunkConfig

# 문맥 보존을 위한 기본 청크 설정
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200


def _build_contextual_clause_text(article: Article, clause_idx: int, header: str, cfg: ChunkConfig) -> str:
    """인접 항을 포함해 문맥이 보존되도록 청크 텍스트를 구성한다."""
    context_window = max(1, cfg.chunk_overlap // 200)
    start_idx = max(0, clause_idx - context_window)
    end_idx = min(len(article.clauses), clause_idx + context_window + 1)

    selected_parts = []
    for idx in range(start_idx, end_idx):
        part_text = article.clauses[idx].text.strip()
        if not part_text:
            continue
        candidate = f"{header} {' '.join(selected_parts + [part_text])}".strip()
        if len(candidate) <= cfg.chunk_size:
            selected_parts.append(part_text)
        elif not selected_parts:
            selected_parts.append(part_text)
            break
        else:
            break

    return f"{header} {' '.join(selected_parts)}".strip()


from modules.embedder_clova import ClovaEmbedder
from modules.vectorstore import VectorStore

logger = logging.getLogger(__name__)
base_path = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=base_path / ".env")

def parse_statute(raw_text: str, law_id: str, law_name: str, source_type: str = "statute") -> list[Article]:
    """정규식 기반 법령 조·항·호 계층 파싱.
    
    지원 패턴:
    - 조: "제N조(제목)" 또는 "제N조의N(제목)"
    - 항: ① ② ③ ... 또는 ⑴ ⑵ ⑶ ...
    - 호: 1. 2. 3. ...
    """
    articles = []

    # 조문 분리 패턴. 줄 시작 위치에서만 매칭해야 한다 — 그렇지 않으면
    # "「에너지법」 제2조제1호에 따른..." 같은 타법 인용이나 부칙의
    # "제17조의2, 제18조, 제22조의3 및 제35조..." 같은 문장 중간의
    # 조문 번호 언급까지 조문 경계로 오인해 파싱이 깨진다.
    article_pattern = re.compile(
        r'^제(\d+(?:의\d+)?)조\s*(?:\(([^)]+)\))?',
        re.MULTILINE,
    )

    # 텍스트를 조문 단위로 분할
    splits = article_pattern.split(raw_text)
    
    # splits는 [앞부분, 조번호1, 제목1, 본문1, 조번호2, 제목2, 본문2, ...] 형태
    i = 1  # 첫 번째 매칭부터
    while i < len(splits) - 1:
        article_no = splits[i].strip()
        title = (splits[i + 1] or "").strip() if i + 1 < len(splits) else ""
        body = splits[i + 2].strip() if i + 2 < len(splits) else ""
        
        # 전체 조문 텍스트 복원
        full_text = f"제{article_no}조"
        if title:
            full_text += f"({title})"
        full_text += f" {body}"
        
        # 항 파싱 (①②③... 패턴)
        clauses = _parse_clauses(body)
        
        articles.append(Article(
            article_no=article_no,
            title=title,
            full_text=full_text.strip(),
            clauses=clauses,
        ))
        
        i += 3
    
    logger.info(f"{law_name}: {len(articles)}개 조문 파싱 완료")
    return articles


def _parse_clauses(body: str) -> list[ClauseData]:
    """항(①②③) 단위 파싱."""
    # 원문자 숫자 패턴: ① ~ ⑳
    circled_nums = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'
    pattern = re.compile(f'([{circled_nums}])')
    
    parts = pattern.split(body)
    clauses = []
    
    i = 1  # 첫 번째 원문자부터
    clause_num = 1
    while i < len(parts):
        marker = parts[i]
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        clauses.append(ClauseData(
            clause_no=str(clause_num),
            text=f"{marker} {text}",
        ))
        clause_num += 1
        i += 2
    
    return clauses


def chunk_corpus(
    articles: list[Article],
    law_id: str,
    law_name: str,
    source_type: str = "statute",
    cfg: ChunkConfig = ChunkConfig(),
) -> list[ChunkMeta]:
    """조문 구조 보존형 Parent-Child 청킹.
    
    - Parent: 조(條) 전체 — 검색 대상 아님, 확장 주입용
    - Child: 항(項) — 임베딩 대상. 항이 없으면 조 본문
    - 임베딩 텍스트: 컨텍스트 헤더 + 본문
    """
    chunks = []
    
    for article in articles:
        parent_id = f"{law_id}-a{article.article_no}"
        hierarchy_base = f"{law_name} > 제{article.article_no}조"
        if article.title:
            hierarchy_base += f"({article.title})"
        
        if article.clauses:
            # 항 단위 Child 청크 생성
            for clause_idx, clause in enumerate(article.clauses):
                chunk_id = f"{law_id}-a{article.article_no}-c{clause.clause_no}"
                hierarchy = f"{hierarchy_base} > 제{clause.clause_no}항"
                
                # 임베딩 텍스트: 컨텍스트 헤더 + 본문
                header = f"[{law_name} 제{article.article_no}조"
                if article.title:
                    header += f"({article.title})"
                header += f" 제{clause.clause_no}항]"
                embed_text = _build_contextual_clause_text(article, clause_idx, header, cfg)
                
                chunks.append(ChunkMeta(
                    chunk_id=chunk_id,
                    source_type=source_type,
                    law_id=law_id,
                    law_name=law_name,
                    article_no=article.article_no,
                    article_title=article.title,
                    clause_no=clause.clause_no,
                    parent_id=parent_id,
                    hierarchy_path=hierarchy,
                    text=embed_text,
                ))
        else:
            # 항이 없는 조 → 조 자체가 단일 청크
            chunk_id = parent_id
            chunks.append(ChunkMeta(
                chunk_id=chunk_id,
                source_type=source_type,
                law_id=law_id,
                law_name=law_name,
                article_no=article.article_no,
                article_title=article.title,
                clause_no=None,
                parent_id=None,
                hierarchy_path=hierarchy_base,
                text=f"[{law_name} 제{article.article_no}조({article.title})] {article.full_text}",
            ))
    
    logger.info(f"{law_name}: {len(chunks)}개 청크 생성 완료")
    return chunks


def chunk_guideline(
    text: str,
    law_id: str,
    law_name: str,
    cfg: ChunkConfig = ChunkConfig(),
) -> list[ChunkMeta]:
    """가이드라인 문서 청킹 (섹션 헤더 기반 분할).
    
    조문 구조가 없으므로 마크다운/목차 구조를 활용한다.
    """
    chunks = []
    
    # 섹션 분리 (## 또는 숫자. 패턴)
    section_pattern = re.compile(r'\n(?=(?:#{1,3}\s|\d+\.\s|[IVX]+\.\s))')
    sections = section_pattern.split(text)
    
    chunk_idx = 0
    for section in sections:
        section = section.strip()
        if not section or len(section) < 20:
            continue
        
        # 섹션 제목 추출
        first_line = section.split('\n')[0].strip('#').strip()
        
        chunk_id = f"{law_id}-g{chunk_idx:03d}"
        chunks.append(ChunkMeta(
            chunk_id=chunk_id,
            source_type="guideline",
            law_id=law_id,
            law_name=law_name,
            article_no=str(chunk_idx),
            article_title=first_line[:50],
            clause_no=None,
            parent_id=None,
            hierarchy_path=f"{law_name} > {first_line[:50]}",
            text=section,
        ))
        chunk_idx += 1
    
    logger.info(f"{law_name}: {len(chunks)}개 가이드라인 청크 생성 완료")
    return chunks


def load_corpus_file(file_path: Path) -> dict:
    """코퍼스 파일 로드. .txt 또는 .json 지원.
    
    JSON 파일 형식:
    {
        "law_id": "pipa",
        "law_name": "개인정보 보호법",
        "source_type": "statute",  // or "decree", "guideline"
        "text": "법령 전문 텍스트..."
    }
    """
    if file_path.suffix == ".json":
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    elif file_path.suffix == ".txt":
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
        # 파일명에서 메타데이터 추론
        stem = file_path.stem
        return {
            "law_id": stem,
            "law_name": stem,
            "source_type": "statute",
            "text": text,
        }
    else:
        raise ValueError(f"지원하지 않는 파일 형식: {file_path.suffix}")


def main():
    """CLI 엔트리포인트: 법령 파싱 → 청킹 → 임베딩 → 인덱스 저장."""
    parser = argparse.ArgumentParser(description="법령/가이드라인 인덱스 구축")
    parser.add_argument("--corpus", type=str, required=True, help="코퍼스 디렉터리 경로")
    parser.add_argument("--out", type=str, required=True, help="인덱스 출력 디렉터리")
    parser.add_argument("--dim", type=int, default=1024, help="임베딩 차원")
    parser.add_argument(
        "--append", action="store_true",
        help="기존 인덱스에 law_id가 없는 코퍼스 파일만 추가 임베딩하여 병합 "
             "(기존 벡터는 재임베딩하지 않고 인덱스에서 복원)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    load_dotenv()

    corpus_dir = Path(args.corpus)
    out_dir = Path(args.out)

    if not corpus_dir.exists():
        logger.error(f"코퍼스 디렉터리를 찾을 수 없습니다: {corpus_dir}")
        sys.exit(1)

    # --append: 기존 인덱스에 이미 있는 law_id는 건너뛴다
    existing_law_ids: set[str] = set()
    existing_vectors = None
    existing_metadatas: list[ChunkMeta] = []
    if args.append and (out_dir / VectorStore.INDEX_FILENAME).exists():
        old_store = VectorStore(dim=args.dim)
        old_store.load(out_dir)
        existing_metadatas = old_store.metadatas
        existing_vectors = old_store.get_all_vectors()
        existing_law_ids = {m.law_id for m in existing_metadatas}
        logger.info(f"기존 인덱스 로드: {len(existing_metadatas)}개 청크, law_id={existing_law_ids}")

    # 1. 코퍼스 로드 및 청킹
    all_chunks: list[ChunkMeta] = []

    for file_path in sorted(corpus_dir.glob("*")):
        if file_path.suffix not in (".json", ".txt"):
            continue

        corpus_data = load_corpus_file(file_path)
        law_id = corpus_data["law_id"]

        if args.append and law_id in existing_law_ids:
            logger.info(f"건너뜀 (이미 인덱싱됨): {file_path.name} ({law_id})")
            continue

        logger.info(f"처리 중: {file_path.name}")
        source_type = corpus_data.get("source_type", "statute")
        law_name = corpus_data["law_name"]
        text = corpus_data["text"]

        if source_type == "guideline":
            chunks = chunk_guideline(text, law_id, law_name)
        else:
            articles = parse_statute(text, law_id, law_name, source_type)
            chunks = chunk_corpus(articles, law_id, law_name, source_type)

        all_chunks.extend(chunks)

    if not all_chunks:
        if existing_metadatas:
            logger.info("새로 추가할 코퍼스가 없습니다. 기존 인덱스를 그대로 유지합니다.")
            sys.exit(0)
        logger.error("청킹된 데이터가 없습니다.")
        sys.exit(1)

    logger.info(f"총 {len(all_chunks)}개 신규 청크 생성")

    # 2. 임베딩 (신규 청크만)
    clova_api_key = os.getenv("CLOVA_API_KEY", "").strip()
    clova_apigw_key = os.getenv("CLOVA_APIGW_KEY", "").strip()
    clova_app_id = (
        os.getenv("CLOVA_EMBEDDING_APP_ID", "").strip()
        or os.getenv("CLOVA_APP_ID", "").strip()
    )

    if not clova_api_key:
        logger.error("Clova API 키가 설정되지 않았습니다. .env 파일을 확인하세요.")
        sys.exit(1)

    embedder = ClovaEmbedder(
        api_key=clova_api_key,
        apigw_key=clova_apigw_key,
        app_id=clova_app_id,
        dim=args.dim,
    )

    texts = [chunk.text for chunk in all_chunks]
    logger.info(f"{len(texts)}개 텍스트 임베딩 시작...")
    new_vectors = embedder.embed_documents(texts)

    if embedder.fallback_count:
        logger.error(
            f"{embedder.fallback_count}개 청크가 실제 API 대신 로컬 대체(랜덤) 임베딩으로 생성되었습니다. "
            "인덱스를 저장하지 않고 중단합니다. 잠시 후 다시 실행하세요."
        )
        sys.exit(1)

    # 3. 기존 벡터와 병합 후 인덱스 구축 및 저장
    if existing_vectors is not None and existing_vectors.shape[0] > 0:
        vectors = np.vstack([existing_vectors, new_vectors]).astype(np.float32)
        all_metadatas = existing_metadatas + all_chunks
    else:
        vectors = new_vectors
        all_metadatas = all_chunks

    store = VectorStore(dim=args.dim)
    store.build(vectors, all_metadatas)
    store.save(out_dir)

    logger.info(f"인덱스 구축 완료: {out_dir} (총 {len(all_metadatas)}개 청크)")


if __name__ == "__main__":
    main()
