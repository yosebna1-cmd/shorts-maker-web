from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import streamlit as st

from shorts_engine import (
    ACCENT_COLOR_OPTIONS,
    BACKGROUND_MODE_OPTIONS,
    RATE_OPTIONS,
    RESOLUTION_OPTIONS,
    SUBTITLE_STYLE_OPTIONS,
    TEMPLATE_OPTIONS,
    VOICE_OPTIONS,
    ShortsMakerError,
    fetch_article,
    generate_local_plan,
    generate_plan_with_gemini,
    normalize_plan,
    render_video,
)

st.set_page_config(
    page_title="쇼츠메이커 WEB V2.1",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
.block-container {max-width: 1180px; padding-top: 1.15rem; padding-bottom: 4rem;}
.hero {padding: 26px 30px; border-radius: 24px; background: linear-gradient(135deg,#151A31,#5631A7 62%,#8B52E8); color:#fff; margin-bottom:18px; box-shadow:0 14px 34px rgba(50,33,100,.18);}
.hero h1 {font-size:2.08rem; margin:0 0 8px 0; letter-spacing:-.03em;}
.hero p {margin:0; opacity:.92; font-size:1rem;}
.feature-row {display:flex; gap:8px; flex-wrap:wrap; margin-top:14px;}
.feature-pill {font-size:.82rem; padding:7px 11px; border-radius:999px; background:rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.18);}
.design-note {padding:12px 14px; border-radius:14px; background:#f4f1ff; border:1px solid #e2dbff; color:#40306e; font-size:.9rem; margin:4px 0 12px;}
.small-note {font-size:.85rem; color:#687080;}
.stButton > button {border-radius:14px; font-weight:800; min-height:54px; font-size:1.02rem;}
[data-testid="stDownloadButton"] button {border-radius:12px; font-weight:750;}
</style>
<div class="hero">
  <h1>🎬 쇼츠메이커 WEB V2.1</h1>
  <p>기사 링크 하나로 대본·AI 성우·강조 자막·풀스크린 세로 MP4를 자동 제작합니다.</p>
  <div class="feature-row">
    <span class="feature-pill">반반 잘림 제거</span>
    <span class="feature-pill">풀스크린·블러 배경</span>
    <span class="feature-pill">상단 후킹 제목</span>
    <span class="feature-pill">핵심 단어 강조 자막</span>
    <span class="feature-pill">템플릿 3종</span>
  </div>
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
            help="키가 있으면 기사 원문 분석과 후킹·강조 단어 생성 품질이 좋아집니다. 저장하지 않습니다.",
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

with st.expander("🎨 디자인 설정 · V2.1", expanded=True):
    d1, d2, d3 = st.columns(3)
    with d1:
        template_label = st.selectbox("영상 템플릿", list(TEMPLATE_OPTIONS), index=0)
    with d2:
        subtitle_label = st.selectbox("자막 스타일", list(SUBTITLE_STYLE_OPTIONS), index=0)
    with d3:
        accent_label = st.selectbox("강조 색상", list(ACCENT_COLOR_OPTIONS), index=0)

    d4, d5 = st.columns(2)
    with d4:
        background_label = st.selectbox("이미지 배경 처리", list(BACKGROUND_MODE_OPTIONS), index=0)
    with d5:
        resolution_label = st.selectbox("출력 해상도", list(RESOLUTION_OPTIONS), index=0)

    t1, t2 = st.columns(2)
    with t1:
        show_hook = st.toggle("상단 후킹 제목 표시", value=True)
    with t2:
        show_badge = st.toggle("콘텐츠 유형 라벨 표시", value=True)

    template_help = {
        "highlight": "자막을 크게 보여주고 핵심 단어에 강조색을 적용하는 요즘 쇼츠형입니다.",
        "news": "상단 제목과 하단 자막 패널을 분리한 깔끔한 뉴스형입니다.",
        "card": "배경과 본문 이미지를 카드처럼 정리해 정보 전달에 적합한 형태입니다.",
    }
    st.markdown(
        f'<div class="design-note"><b>선택한 템플릿:</b> {template_label}<br>{template_help[TEMPLATE_OPTIONS[template_label]]}</div>',
        unsafe_allow_html=True,
    )

st.caption("API 키는 현재 브라우저 세션에서 호출할 때만 사용하며 결과 ZIP에는 포함하지 않습니다.")

start = st.button("🚀 기사 링크로 V2.1 쇼츠 자동 제작", type="primary", use_container_width=True)

if start:
    if not url.strip():
        st.error("기사 링크를 입력해주세요.")
        st.stop()

    progress = st.progress(0, text="기사 원문을 읽고 있습니다.")
    status = st.empty()
    try:
        article = fetch_article(url.strip())
        progress.progress(16, text="기사 제목과 본문을 확인했습니다.")

        if gemini_key.strip():
            status.info("Gemini가 기사 사실을 바탕으로 후킹·강조 자막·장면 대본을 작성하고 있습니다.")
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
        progress.progress(35, text="후킹 제목과 장면별 강조 자막을 완료했습니다.")

        workdir = Path(tempfile.mkdtemp(prefix="shortsmaker_web_v21_"))

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
            template=TEMPLATE_OPTIONS[template_label],
            subtitle_style=SUBTITLE_STYLE_OPTIONS[subtitle_label],
            accent=ACCENT_COLOR_OPTIONS[accent_label],
            background_mode=BACKGROUND_MODE_OPTIONS[background_label],
            show_hook=show_hook,
            show_badge=show_badge,
            category=category,
            resolution=RESOLUTION_OPTIONS[resolution_label],
        )
        progress.progress(100, text="V2.1 쇼츠 영상이 완성됐습니다.")
        status.success(
            f"완료: {engine_name} · {len(plan['scenes'])}개 장면 · {template_label} · {resolution_label}"
        )

        result_key = hashlib.sha256(files["video"].read_bytes()[:4096]).hexdigest()
        st.session_state["shorts_result_v21"] = {
            "key": result_key,
            "article": article,
            "plan": plan,
            "video": files["video"].read_bytes(),
            "audio": files["audio"].read_bytes(),
            "srt": files["srt"].read_bytes(),
            "script": files["script"].read_bytes(),
            "zip": files["zip"].read_bytes(),
            "design": {
                "template": template_label,
                "subtitle": subtitle_label,
                "accent": accent_label,
                "background": background_label,
                "resolution": resolution_label,
            },
        }
    except ShortsMakerError as exc:
        progress.empty()
        status.empty()
        st.error(str(exc))
    except Exception as exc:
        progress.empty()
        status.empty()
        st.exception(exc)

result = st.session_state.get("shorts_result_v21")
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
            file_name="shorts_v21_result.zip",
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
                file_name="final_short_v21.mp4",
                mime="video/mp4",
                use_container_width=True,
                key=f"video-{result['key']}",
            )
        with d2:
            st.download_button(
                "SRT 자막 다운로드",
                result["srt"],
                file_name="subtitles_v21.srt",
                mime="text/plain",
                use_container_width=True,
                key=f"srt-{result['key']}",
            )
    with right:
        st.subheader(plan["video_title"])
        st.caption(" · ".join(result["design"].values()))
        st.markdown(f"**후킹 제목**  \n{plan['hook']}")
        st.write(plan["description"])
        st.code(" ".join(plan["hashtags"]), language=None)
        st.markdown("**전체 나레이션**")
        st.write(plan["narration"])
        with st.expander("장면별 자막과 강조 단어 확인"):
            for idx, scene in enumerate(plan["scenes"], 1):
                emphasis = ", ".join(scene.get("emphasis") or []) or "자동 강조"
                st.markdown(f"**{idx}. {scene['caption']}**")
                st.caption(f"강조: {emphasis} · {scene['narration']}")
        st.download_button(
            "Vrew용 대본 다운로드",
            result["script"],
            file_name="vrew_script_v21.txt",
            mime="text/plain",
            use_container_width=True,
            key=f"script-{result['key']}",
        )
        st.caption(f"원문: {article.url}")

st.divider()
st.caption("기사와 이미지의 사용 권한, 인물·사건 관련 사실관계는 업로드 전에 최종 확인해주세요.")
