"""Pass 2(위반 판정) LLM 프로바이더 선택. 환경변수 하나로 즉시 전환 가능.

발표 중 HCX 장애 시 PASS2_PROVIDER=gemini 로 바꾸면 바로 대체된다.
"""
import logging
import os

from modules.gemini_client import GeminiClient
from modules.hcx_client import HCXClient

logger = logging.getLogger(__name__)


def get_pass2_client(gemini_client: GeminiClient):
    """PASS2_PROVIDER 환경변수(hcx|gemini, 기본 hcx)에 따라 Pass 2 클라이언트를 반환.

    Args:
        gemini_client: 이미 생성된 GeminiClient (provider가 gemini일 때 재사용)
    """
    provider = os.getenv("PASS2_PROVIDER", "hcx").strip().lower()

    if provider == "gemini":
        logger.info("Pass 2 프로바이더: Gemini")
        return gemini_client

    clova_api_key = os.getenv("CLOVA_API_KEY", "").strip()
    if not clova_api_key:
        logger.warning("CLOVA_API_KEY가 없어 Pass 2를 Gemini로 대체합니다.")
        return gemini_client

    logger.info("Pass 2 프로바이더: HyperCLOVA X")
    return HCXClient(api_key=clova_api_key)
