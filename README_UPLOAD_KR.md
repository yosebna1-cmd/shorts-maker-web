# 쇼츠메이커 CLOUD V3.1 업로드 방법

1. 이 ZIP의 압축을 풉니다.
2. GitHub 저장소 `yosebna1-cmd/shorts-maker-web`에서 `파일 추가 → 파일 업로드`를 누릅니다.
3. 압축을 푼 폴더 안의 내용 전체를 업로드합니다.
4. 같은 이름의 기존 파일은 교체합니다.
5. `변경사항 커밋`을 누릅니다.
6. Streamlit 앱에서 `Manage app → Reboot app`을 누릅니다.

중요: 이번 버전은 `streamlit_app.py` 하나에 실행 엔진이 포함되어 있어 `shorts_engine.py`를 불러오지 않습니다. GitHub에 예전 `shorts_engine.py`가 남아 있어도 실행에는 사용되지 않습니다.
