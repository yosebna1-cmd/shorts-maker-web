from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import streamlit as st

from shorts_engine import (
    RATE_OPTIONS,
    VOICE_OPTIONS,
    ShortsMakerError,
    fetch_article,
    generate_local_plan,
    generate_plan_with_gemini,
    normalize_plan,
    render_video,
)

st.set_page_config(
    page_title="쇼츠메이커 WEB V2.0",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
.block-container {max-width: 1120px; padding-top: 1.4rem; padding-bottom: 4rem;}
.hero {padding: 24px 28px; border-radius: 22px; background: linear-gradient(135deg,#171b2d,#5632a8); color:#fff; margin-bottom:18px;}
.hero h1 {font-size:2.0rem; margin:0 0 8px 0;}
.hero p {margin:0; opacity:.9;}
.step {padding:12px 14px; border:1px solid #e4e6ef; border-radius:13px; background:#fff; min-height:65px;}
.small-note {font-size:.86rem; color:#666;}
.stButton > button {border-radius:13px; font-weight:800; min-height:50px;}
</style>
<div class="hero">
  <h1>🎬 쇼츠메이커 WEB V2.0</h1>
  <p>기사 링크 하나를 넣으면 대본·AI 성우·자막·세로 MP4를 순서대로 자동 제작합니다.</p>
</div>
""",
    unsafe_allow_html=True,
)

with st.expander("최초 1회 설정", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        gemini_key = st.text_input(
            "Gemini 무료 API 키",
            type="password",
            help="키가 있으면 기사 원문 분석과 고품질 대본 생성에 사용합니다. 저장하지 않습니다.",
        )
    with col2:
        pexels_key = st.text_input(
            "Pexels 무료 API 키 (선택)",
            type="password",
            help="없어도 기사 이미지 또는 자동 그래픽 배경으로 영상이 완성됩니다.",
        )

url = st.text_input(
    "기사 링크",
    placeholder="https://m.entertain.naver.com/home/article/...",
)

c1, c2, c3, c4 = st.columns(4)
with c1:
    category = st.selectbox("콘텐츠 유형", ["연예", "국내 이슈", "해외 이슈", "경제·주식", "생활정보", "제품·리뷰"])
with c2:
    target_duration = st.selectbox("목표 길이", [30, 45, 60], index=1, format_func=lambda x: f"{x}초")
with c3:
    voice_label = st.selectbox("AI 성우", list(VOICE_OPTIONS), index=0)
with c4:
    rate_label = st.selectbox("말하기 속도", list(RATE_OPTIONS), index=2)

st.caption("API 키는 현재 브라우저 세션에서 호출할 때만 사용하며 결과 ZIP에는 포함하지 않습니다.")

start = st.button("🚀 기사 링크로 쇼츠 자동 제작", type="primary", use_container_width=True)

if start:
    if not url.strip():
        st.error("기사 링크를 입력해주세요.")
        st.stop()

    progress = st.progress(0, text="기사 원문을 읽고 있습니다.")
    status = st.empty()
    try:
        article = fetch_article(url.strip())
        progress.progress(18, text="기사 제목과 본문을 확인했습니다.")

        if gemini_key.strip():
            status.info("Gemini가 기사 사실을 바탕으로 쇼츠 대본을 작성하고 있습니다.")
            try:
                raw_plan = generate_plan_with_gemini(article, gemini_key.strip(), target_duration, category)
                engine_name = "Gemini AI 대본"
            except Exception as gemini_error:
                status.warning(f"Gemini 대본 생성이 실패해 로컬 요약 방식으로 계속 진행합니다.\n\n{gemini_error}")
                raw_plan = generate_local_plan(article, target_duration, category)
                engine_name = "로컬 자동 대본"
        else:
            status.info("API 키 없이 로컬 자동 대본으로 계속 진행합니다.")
            raw_plan = generate_local_plan(article, target_duration, category)
            engine_name = "로컬 자동 대본"

        plan = normalize_plan(raw_plan, article, target_duration)
        progress.progress(38, text="대본과 장면 구성을 완료했습니다.")

        workdir = Path(tempfile.mkdtemp(prefix="shortsmaker_web_"))

        def render_status(message: str) -> None:
            status.info(message)

        files = render_video(
            article=article,
            plan=plan,
            voice=VOICE_OPTIONS[voice_label]["voice"],
            rate=RATE_OPTIONS[rate_label],
            pexels_key=pexels_key.strip(),
            workdir=workdir,
            progress=render_status,
        )
        progress.progress(100, text="쇼츠 영상이 완성됐습니다.")
        status.success(f"완료: {engine_name} · {len(plan['scenes'])}개 장면")

        result_key = hashlib.sha256(files["video"].read_bytes()[:4096]).hexdigest()
        st.session_state["shorts_result"] = {
            "key": result_key,
            "article": article,
            "plan": plan,
            "video": files["video"].read_bytes(),
            "audio": files["audio"].read_bytes(),
            "srt": files["srt"].read_bytes(),
            "script": files["script"].read_bytes(),
            "zip": files["zip"].read_bytes(),
        }
    except ShortsMakerError as exc:
        progress.empty()
        status.empty()
        st.error(str(exc))
    except Exception as exc:
        progress.empty()
        status.empty()
        st.exception(exc)

result = st.session_state.get("shorts_result")
if result:
    article = result["article"]
    plan = result["plan"]
    st.divider()
    left, right = st.columns([1, 1])
    with left:
        st.subheader("완성 영상")
        st.video(result["video"], format="video/mp4")
        st.download_button(
            "⬇️ 결과 전체 ZIP 다운로드",
            data=result["zip"],
            file_name="shorts_result.zip",
            mime="application/zip",
            type="primary",
            use_container_width=True,
            key=f"zip-{result['key']}",
        )
        d1, d2 = st.columns(2)
        with d1:
            st.download_button(
                "MP4 다운로드",
                result["video"],
                file_name="final_short.mp4",
                mime="video/mp4",
                use_container_width=True,
                key=f"video-{result['key']}",
            )
        with d2:
            st.download_button(
                "SRT 자막 다운로드",
                result["srt"],
                file_name="subtitles.srt",
                mime="text/plain",
                use_container_width=True,
                key=f"srt-{result['key']}",
            )
    with right:
        st.subheader(plan["video_title"])
        st.write(plan["description"])
        st.code(" ".join(plan["hashtags"]), language=None)
        st.markdown("**전체 나레이션**")
        st.write(plan["narration"])
        with st.expander("장면별 구성 확인"):
            for idx, scene in enumerate(plan["scenes"], 1):
                st.markdown(f"**{idx}. {scene['caption']}**")
                st.caption(scene["narration"])
        st.download_button(
            "Vrew용 대본 다운로드",
            result["script"],
            file_name="vrew_script.txt",
            mime="text/plain",
            use_container_width=True,
            key=f"script-{result['key']}",
        )
        st.caption(f"원문: {article.url}")

st.divider()
st.caption("기사와 이미지의 사용 권한, 인물·사건 관련 사실관계는 업로드 전에 최종 확인해주세요.")
