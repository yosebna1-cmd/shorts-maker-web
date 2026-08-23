SHORTS MAKER CLOUD V4.0 배포본

이 파일 세트는 기존 Streamlit Cloud 쇼츠메이커 프로젝트를 이어서 사용하는 업그레이드본입니다.

교체 파일
- streamlit_app.py : 기존 앱 메인 파일과 교체
- requirements.txt : 저장소 루트에 업로드/교체
- packages.txt : 저장소 루트에 업로드/교체

기존 Streamlit Cloud 앱 URL은 그대로 사용할 수 있습니다.
GitHub 저장소에서 위 3개 파일을 같은 위치에 교체한 뒤 Streamlit Cloud가 재배포되면 V4.0이 열립니다.

V4.0 주요 변경
- 기존 기사 URL → 대본 → AI 성우 → 렌더링 흐름 유지
- 연예뉴스 전용 스토리보드 엔진
- 지루함/컷 리듬 검사
- 실제 관련 자료 업로드 및 저작권 사전검사
- GREEN 자료만 자동 렌더링에 사용
- YELLOW/RED 자료 자동 제외
- YouTube Shorts 메타데이터 생성
- Naver Clip용 제목/설명/해시태그 별도 생성
- copyright_preflight_v40.txt 포함
- 결과 ZIP 파일명 shorts_result_v40.zip

주의
저작권 사전검사는 위험을 줄이기 위한 보수적 필터이며 법적 무침해를 보증하지 않습니다.
