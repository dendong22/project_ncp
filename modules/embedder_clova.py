"""Clova Studio 임베딩 API 클라이언트 (개편된 v2 반영)."""
import hashlib
import logging
import os
import time
import numpy as np
import requests

logger = logging.getLogger(__name__)


class ClovaEmbedder:
    """Clova Studio 임베딩 v2 API 래퍼."""
    
    # 새로운 공통 엔드포인트 주소로 변경
    DEFAULT_ENDPOINT = "https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2"
    
    def __init__(
        self,
        api_key: str,
        apigw_key: str,
        app_id: str = "", # 이제 사용되지 않으므로 기본값 처리
        model: str = "clir-emb-dolphin",
        dim: int = 1024,
    ):
        self.api_key = api_key
        self.apigw_key = apigw_key
        self.model = model
        self.dim = dim
        self.endpoint = self.DEFAULT_ENDPOINT
        self.use_local_fallback = os.getenv("USE_LOCAL_EMBEDDING", "0").lower() in {"1", "true", "yes", "on"}
        self.fallback_count = 0
        
        # 헤더 설정 (Authorization에 Bearer 키가 그대로 주입됨)
        # 신형 통합 엔드포인트(v2)는 X-NCP-APIGW-API-KEY가 불필요하며,
        # 포함 시 키 불일치로 500 오류가 발생할 수 있어 제외한다.
        self._headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json; charset=utf-8",
        }
    
    @staticmethod
    def _l2_normalize(v: np.ndarray) -> np.ndarray:
        """L2 정규화."""
        norms = np.linalg.norm(v, axis=-1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        return v / norms

    def _fallback_embedding(self, text: str) -> np.ndarray:
        """Clova API 실패 시 사용할 결정적 로컬 임베딩."""
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big")
        rng = np.random.default_rng(seed)
        vector = rng.standard_normal(self.dim).astype(np.float32)
        return self._l2_normalize(vector.reshape(1, -1)).flatten()
    
    def _call_api(self, text: str, max_retries: int = 6) -> np.ndarray:
        """단일 텍스트 임베딩 API 호출. 429/일시적 오류는 지수 백오프로 재시도한다."""
        if self.use_local_fallback:
            logger.info("로컬 대체 임베딩 모드로 실행합니다.")
            return self._fallback_embedding(text)

        payload = {"text": text}
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    self.endpoint,
                    headers=self._headers,
                    json=payload,
                    timeout=20,
                )
                if resp.status_code == 429:
                    wait = min(2 ** attempt, 20)
                    logger.warning(f"Clova API 속도 제한(429). {wait}초 대기 후 재시도 ({attempt + 1}/{max_retries})")
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()

                # 응답 성공 여부 확인
                if data.get("status", {}).get("code") == "20000":
                    embedding = data["result"]["embedding"]
                    return np.array(embedding, dtype=np.float32)
                raise RuntimeError(f"Clova API 오류 내부 코드: {data.get('status')}")

            except (requests.RequestException, KeyError, RuntimeError) as e:
                last_error = e
                wait = min(2 ** attempt, 20)
                logger.warning(f"Clova API 호출 실패 ({attempt + 1}/{max_retries}): {e}. {wait}초 후 재시도합니다.")
                time.sleep(wait)

        logger.error(f"Clova API 재시도 {max_retries}회 모두 실패. 로컬 대체(랜덤) 임베딩을 사용합니다: {last_error}")
        self.fallback_count += 1
        return self._fallback_embedding(text)

    def embed_documents(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        """문서 배치 임베딩."""
        vectors = []
        for i, text in enumerate(texts):
            if i > 0 and i % batch_size == 0:
                time.sleep(0.5)
                logger.info(f"임베딩 진행: {i}/{len(texts)}")
            vec = self._call_api(text)
            vectors.append(vec)
            time.sleep(0.25)  # 요청 간 간격을 둬 속도 제한(429) 자체를 예방

        if self.fallback_count:
            logger.error(f"{self.fallback_count}/{len(texts)}개 청크가 API 실패로 로컬 대체 임베딩을 사용했습니다. 인덱스 품질에 영향을 줄 수 있습니다.")

        result = np.stack(vectors).astype(np.float32)
        return self._l2_normalize(result)
    
    def embed_query(self, text: str) -> np.ndarray:
        """단일 쿼리 임베딩.

        검색 품질에 직결되므로 폴백(랜덤 벡터)을 허용하지 않는다.
        실패 시 명시적으로 예외를 발생시켜 호출측(pipeline)이
        해당 리스크 포인트를 판정 보류로 처리하게 한다.
        """
        before = self.fallback_count
        vec = self._call_api(text)
        if self.fallback_count > before:
            raise RuntimeError(f"쿼리 임베딩 실패로 로컬 대체 벡터가 생성되었습니다: {text[:50]!r}")
        vec = vec.astype(np.float32).reshape(1, -1)
        return self._l2_normalize(vec).flatten()