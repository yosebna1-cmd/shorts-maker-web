from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import streamlit as st

from shorts_engine import (
    ACCENT_COLOR_OPTIONS,
    BACKGROUND_MODE_OPTIONS,
    MUSIC_TRACK_OPTIONS,
    RATE_OPTIONS,
    RESOLUTION_OPTIONS,
    SUBTITLE_STYLE_OPTIONS,
    TEMPLATE_OPTIONS,
    VOICE_OPTIONS,
    VOICE_PRESET_OPTIONS,
    ShortsMakerError,
    fetch_article,
    generate_local_plan,
    generate_plan_with_gemini,
    normalize_plan,
    render_video,
)

st.set_page_config(
    page_title="쇼츠메이커 WEB V2.2",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
.block-container {max-width: 1180px; padding-top: 1.15rem; padding-bottom: 4rem;}
.hero {padding: 26px 30px; border-radius: 24px; background: linear-gradient(135deg,#12182e,#3f2c86 62%,#7048d7); color:#fff; margin-bottom:18px; box-shadow:0 14px 34px rgba(50,33,100,.18);}
.hero h1 {font-size:2.05rem; margin:0 0 8px 0; letter-spacing:-.03em;}
.hero p {margin:0; opacity:.92; font-size:1rem;}
.feature-row {display:flex; gap:8px; flex-wrap:wrap; margin-top:14px;}
.feature-pill {font-size:.82rem; padding:7px 11px; border-radius:999px; background:rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.18);}
.safe-box {padding:16px 18px; border-radius:16px; background:#effcf5; border:1px solid #bdebd0; color:#155d36; margin:8px 0 16px;}
.notice {padding:13px 15px; border-radius:14px; background:#f4f1ff; border:1px solid #e1dbff; color:#40306e; font-size:.91rem;}
.step-title {font-size:1.12rem; font-weight:850; margin:10px 0 6px;}
.stButton > button {border-radius:14px; font-weight:800; min-height:54px; font-size:1.02rem;}
[data-testid="stDownloadButton"] button {border-radius:12px; font-weight:750;}
</style>
<div class="hero">
  <h1>🎬 쇼츠메이커 WEB V2.2</h1>
  <p>기사의 사실만 새롭게 해설하고, 저작권 안전 영상·내 목소리 보정·자동 BGM으로 세로 쇼츠를 만듭니다.</p>
  <div class="feature-row">
    <span class="feature-pill">기사 사진 자동 사용 금지</span>
    <span class="feature-pill">원문 복제 방지 대본</span>
    <span class="feature-pill">카테고리 표시 제거</span>
    <span class="feature-pill">내 목소리 4가지 보정</span>
    <span class="feature-pill">오리지널 BGM 자동 선택</span>
  </div>
</div>
<div class="safe-box"><b>저작권 안전 모드가 항상 적용됩니다.</b><br>
기사 사진·방송 캡처·타 유튜브 영상은 사용하지 않고, Pexels 스톡 또는 프로그램 자체 그래픽만 사용합니다.</div>
""",
    unsafe_allow_html=True,
)

with st.expander("최초 1회 설정", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        gemini_key = st.text_input(
            "Gemini 무료 API 키",
            type="password",
            help="사실 추출, 독창적 해설 대본, 후킹 문구 생성에 사용합니다. 결과 파일에는 저장하지 않습니다.",
        )
    with c2:
        pexels_key = st.text_input(
            "Pexels 무료 API 키 (선택)",
            type="password",
            help="입력하면 기사 사진 대신 상업 이용 가능한 스톡 이미지를 검색합니다. 없으면 자체 그래픽으로 완성합니다.",
        )

st.markdown('<div class="step-title">1. 기사 분석과 새 대본 만들기</div>', unsafe_allow_html=True)
url = st.text_input("대표 기사 링크", placeholder="https://m.entertain.naver.com/home/article/...")

c1, c2 = st.columns(2)
with c1:
    category = st.selectbox("콘텐츠 유형", ["연예", "국내 이슈", "해외 이슈", "경제·주식", "생활정보", "제품·리뷰"])
with c2:
    target_duration = st.selectbox("목표 길이", [30, 45, 60], index=1, format_func=lambda x: f"{x}초")

make_plan = st.button("🧠 사실 추출·독창 대본 만들기", type="primary", use_container_width=True)

if make_plan:
    if not url.strip():
        st.error("기사 링크를 입력해주세요.")
        st.stop()
    if not gemini_key.strip():
        st.error("V2.2 저작권 안전 대본에는 무료 Gemini API 키가 필요합니다. 원문 문장을 그대로 가져오는 로컬 대본은 사용하지 않습니다.")
        st.stop()
    status = st.empty()
    progress = st.progress(0, text="기사 내용을 확인하고 있습니다.")
    try:
        article = fetch_article(url.strip())
        progress.progress(25, text="기사에서 확인 가능한 사실을 분리했습니다.")
        status.info("기사 문장 구조를 따라가지 않고, 사실·쟁점·자체 해설로 새 대본을 작성합니다.")
        try:
            raw_plan = generate_plan_with_gemini(article, gemini_key.strip(), target_duration, category)
            engine_name = "Gemini 독창 해설 대본"
        except Exception as exc:
            raise ShortsMakerError(f"저작권 안전 대본 생성에 실패했습니다. 무료 Gemini 키와 사용 한도를 확인해주세요.\n\n{exc}")
        plan = normalize_plan(raw_plan, article, target_duration)
        progress.progress(100, text="대본이 준비됐습니다. 아래에서 음성과 음악을 선택하세요.")
        status.success(f"완료: {engine_name} · {len(plan['scenes'])}개 장면")
        st.session_state["v22_plan"] = {
            "article": article,
            "plan": plan,
            "category": category,
            "duration": target_duration,
            "engine": engine_name,
        }
        st.session_state.pop("v22_result", None)
    except ShortsMakerError as exc:
        progress.empty()
        status.empty()
        st.error(str(exc))
    except Exception as exc:
        progress.empty()
        status.empty()
        st.exception(exc)

plan_state = st.session_state.get("v22_plan")
if plan_state:
    article = plan_state["article"]
    plan = plan_state["plan"]
    category = plan_state["category"]

    st.divider()
    st.markdown('<div class="step-title">2. 대본 확인</div>', unsafe_allow_html=True)
    left, right = st.columns([1.1, 1])
    with left:
        st.markdown(f"### {plan['video_title']}")
        st.markdown(f"**후킹 문구**  \n{plan['hook']}")
        st.text_area("읽을 전체 대본", value=plan["narration"], height=220, disabled=True)
    with right:
        st.markdown("**확인된 핵심 사실**")
        for fact in plan.get("core_facts") or [article.title]:
            st.write(f"• {fact}")
        st.markdown(
            '<div class="notice"><b>내 목소리 사용 시</b><br>아래 대본을 직접 읽어 녹음하세요. '
            '한 번 올린 짧은 샘플만으로 새 문장을 자동 발화하려면 별도의 음성복제 서비스가 필요하므로, '
            'V2.2는 더 안전하고 자연스러운 <b>직접 녹음 + 자동 보정</b> 방식입니다.</div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div class="step-title">3. 나레이션 선택</div>', unsafe_allow_html=True)
    narration_mode = st.radio(
        "나레이션 방식",
        ["AI 성우로 자동 제작", "내 목소리 녹음·업로드 후 보정"],
        horizontal=True,
    )

    voice_label = list(VOICE_OPTIONS)[0]
    rate_label = "쇼츠 추천"
    voice_preset_label = "밝고 듣기 좋게"
    recorded_audio = None
    uploaded_voice = None

    if narration_mode == "AI 성우로 자동 제작":
        v1, v2 = st.columns(2)
        with v1:
            voice_label = st.selectbox("AI 성우", list(VOICE_OPTIONS), index=0)
        with v2:
            rate_label = st.selectbox("말하기 속도", list(RATE_OPTIONS), index=2)
    else:
        v1, v2 = st.columns(2)
        with v1:
            voice_preset_label = st.selectbox("내 목소리 보정 스타일", list(VOICE_PRESET_OPTIONS), index=1)
        with v2:
            uploaded_voice = st.file_uploader("녹음 파일 업로드", type=["wav", "mp3", "m4a", "aac", "ogg"])
        if hasattr(st, "audio_input"):
            recorded_audio = st.audio_input("또는 브라우저에서 대본을 직접 녹음")
        st.caption("녹음은 대본 전체를 읽어야 장면과 음성이 정확히 맞습니다. 업로드 파일이 있으면 브라우저 녹음보다 우선 사용합니다.")

    st.markdown('<div class="step-title">4. 배경음악 자동 선택</div>', unsafe_allow_html=True)
    m1, m2, m3 = st.columns([1, 1, 1])
    with m1:
        music_ui = st.selectbox(
            "음악 방식",
            ["콘텐츠에 맞춰 자동 추천", "내장 오리지널 중 직접 선택", "YouTube 오디오 라이브러리 음원 업로드", "음악 사용 안 함"],
        )
    with m2:
        selected_music = st.selectbox("내장 음악", list(MUSIC_TRACK_OPTIONS), disabled=music_ui != "내장 오리지널 중 직접 선택")
    with m3:
        music_volume_pct = st.slider("배경음악 크기", 3, 25, 10, 1, format="%d%%", disabled=music_ui == "음악 사용 안 함")

    custom_music_upload = None
    attribution_text = ""
    if music_ui == "YouTube 오디오 라이브러리 음원 업로드":
        c1, c2 = st.columns(2)
        with c1:
            custom_music_upload = st.file_uploader("YouTube 오디오 라이브러리에서 내려받은 음원", type=["mp3", "wav", "m4a", "aac", "ogg"])
        with c2:
            attribution_text = st.text_input("저작자 표시 문구 (필요한 트랙만)", placeholder="Music: 곡명 - 아티스트")
        st.info("YouTube Studio의 오디오 보관함에서 트랙의 '저작자 표시 필요 여부'를 확인한 뒤 업로드하세요.")

    st.markdown('<div class="step-title">5. 영상 디자인</div>', unsafe_allow_html=True)
    d1, d2, d3 = st.columns(3)
    with d1:
        template_label = st.selectbox("영상 템플릿", list(TEMPLATE_OPTIONS), index=0)
    with d2:
        subtitle_label = st.selectbox("자막 스타일", list(SUBTITLE_STYLE_OPTIONS), index=0)
    with d3:
        accent_label = st.selectbox("강조 색상", list(ACCENT_COLOR_OPTIONS), index=0)
    d4, d5, d6 = st.columns(3)
    with d4:
        background_label = st.selectbox("이미지 배경 처리", list(BACKGROUND_MODE_OPTIONS), index=0)
    with d5:
        resolution_label = st.selectbox("출력 해상도", list(RESOLUTION_OPTIONS), index=0)
    with d6:
        show_hook = st.toggle("상단 후킹 제목", value=True)
    st.caption("요청하신 노란색 '연예' 카테고리 표시는 V2.2에서 완전히 제거했습니다.")

    render = st.button("🎬 저작권 안전 쇼츠 완성하기", type="primary", use_container_width=True)
    if render:
        source_audio = uploaded_voice or recorded_audio
        if narration_mode != "AI 성우로 자동 제작" and source_audio is None:
            st.error("내 목소리 방식은 대본을 읽은 녹음 파일 또는 브라우저 녹음이 필요합니다.")
            st.stop()
        if music_ui == "YouTube 오디오 라이브러리 음원 업로드" and custom_music_upload is None:
            st.error("사용할 오디오 라이브러리 음원을 업로드해주세요.")
            st.stop()

        workdir = Path(tempfile.mkdtemp(prefix="shortsmaker_web_v22_"))
        narration_path = None
        if source_audio is not None:
            suffix = Path(getattr(source_audio, "name", "voice.wav")).suffix or ".wav"
            narration_path = workdir / f"my_voice_input{suffix}"
            narration_path.write_bytes(source_audio.getvalue())

        custom_music_path = None
        if custom_music_upload is not None:
            suffix = Path(custom_music_upload.name).suffix or ".mp3"
            custom_music_path = workdir / f"custom_music{suffix}"
            custom_music_path.write_bytes(custom_music_upload.getvalue())

        music_mode_map = {
            "콘텐츠에 맞춰 자동 추천": "auto",
            "내장 오리지널 중 직접 선택": "select",
            "YouTube 오디오 라이브러리 음원 업로드": "upload",
            "음악 사용 안 함": "none",
        }

        progress = st.progress(0, text="영상 제작을 시작합니다.")
        status = st.empty()

        def render_status(message: str) -> None:
            status.info(message)

        try:
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
                show_badge=False,
                category=category,
                resolution=RESOLUTION_OPTIONS[resolution_label],
                narration_audio=narration_path,
                voice_preset=VOICE_PRESET_OPTIONS[voice_preset_label],
                music_mode=music_mode_map[music_ui],
                music_track=selected_music if music_ui == "내장 오리지널 중 직접 선택" else "자동 추천",
                music_volume=music_volume_pct / 100.0,
                custom_music=custom_music_path,
            )
            if attribution_text.strip():
                with files["music_license"].open("a", encoding="utf-8") as fh:
                    fh.write(f"\n사용자 입력 저작자 표시: {attribution_text.strip()}\n")
            progress.progress(100, text="완성됐습니다.")
            status.success("영상·나레이션·자막·출처·권리 점검 보고서를 만들었습니다.")
            result_key = hashlib.sha256(files["video"].read_bytes()[:4096]).hexdigest()
            st.session_state["v22_result"] = {
                "key": result_key,
                "article": article,
                "plan": plan,
                **{name: path.read_bytes() for name, path in files.items()},
            }
        except ShortsMakerError as exc:
            progress.empty()
            status.empty()
            st.error(str(exc))
        except Exception as exc:
            progress.empty()
            status.empty()
            st.exception(exc)

result = st.session_state.get("v22_result")
if result:
    st.divider()
    left, right = st.columns([1, 1])
    with left:
        st.subheader("완성 영상")
        st.video(result["video"], format="video/mp4")
        st.download_button(
            "⬇️ 결과 전체 ZIP 다운로드",
            result["zip"],
            file_name="shorts_v22_result.zip",
            mime="application/zip",
            type="primary",
            use_container_width=True,
            key=f"zip-{result['key']}",
        )
        d1, d2 = st.columns(2)
        with d1:
            st.download_button("MP4 다운로드", result["video"], "final_short_v22.mp4", "video/mp4", use_container_width=True)
        with d2:
            st.download_button("보정된 음성 다운로드", result["audio"], "narration_v22.mp3", "audio/mpeg", use_container_width=True)
    with right:
        st.subheader("저작권 안전 점검")
        st.text(result["copyright_report"].decode("utf-8-sig", errors="replace"))
        st.download_button(
            "저작권 점검 보고서",
            result["copyright_report"],
            "copyright_check_report.txt",
            "text/plain",
            use_container_width=True,
        )
        st.download_button(
            "AI 공개 안내",
            result["ai_disclosure"],
            "ai_disclosure.txt",
            "text/plain",
            use_container_width=True,
        )

st.caption("V2.2의 안전 검사는 저작권 위험을 줄이는 제작 장치이며 법적 무침해를 보증하지는 않습니다. 최종 게시 전 사실관계와 음원 조건을 확인하세요.")
