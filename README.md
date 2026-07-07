💻 실행 및 설치 방법
1) 환경 변수 설정
프로젝트 루트 디렉터리에 .env 파일을 생성하고 필요한 API Key 인프라를 작성합니다.

Code snippet
GEMINI_API_KEY="your_gemini_api_key"
CLOVA_API_KEY="your_clova_api_key"
CLOVA_APIGW_KEY="your_clova_apigw_api_key"
CLOVA_EMBEDDING_APP_ID="your_clova_embedding_app_id"
PASS2_PROVIDER="hcx" # hcx 또는 gemini 토글 가능
2) 필수 패키지 설치
Bash
pip install -r requirements.txt
3) 지식 코퍼스 임베딩 및 인덱스 구축
Bash
python -m modules.ingest
4) 애플리케이션 가동
NiceGUI 애플리케이션 가동 (v2 표준):

Bash
python app/main.py
Streamlit 프로토타입 가동 (v1 호환용):

Bash
streamlit run app.py
🐳 Docker를 통한 NCP 배포 가이드
NCP Server (VPC, Ubuntu 22.04 환경)에서 컨테이너 기반으로 무중단 시연 환경을 손쉽게 구축할 수 있습니다.

Bash
# 1. 저장소 클론 및 deploy 디렉터리 이동
cd legal-screening-agent/deploy

# 2. 호스트 볼륨 마운트용 data 경로 확인 후 컨테이너 빌드 및 백그라운드 실행
docker compose up -d --build

# 3. 배포 로그 및 가동 상태 확인
docker compose logs -f2
