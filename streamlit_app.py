from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

import streamlit as st

from shorts_engine import (
    ACCENT_COLOR_OPTIONS,
    MUSIC_TRACK_OPTIONS,
    RATE_OPTIONS,
    RESOLUTION_OPTIONS,
    SUBTITLE_STYLE_OPTIONS,
    TEMPLATE_OPTIONS,
    VOICE_OPTIONS,
    VOICE_PRESET_OPTIONS,
    ArticleData,
    ShortsMakerError,
    fetch_article,
    generate_zero_key_plan,
    normalize_plan,
    plan_from_edited_narration,
    render_video,
)

VERSION = "3.0"
STATE_PLAN = "v30_plan"
STATE_RESULT = "v30_result"

st.set_page_config(
    page_title="쇼츠메이커 CLOUD V3.0",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
.block-container {max-width: 1180px; padding-top: 1.0rem; padding-bottom: 4rem;}
.hero {padding: 28px 30px; border-radius: 25px; background:linear-gradient(135deg,#11182f,#33276e 56%,#7355dc); color:white; margin-bottom:16px; box-shadow:0 15px 38px rgba(37,28,91,.2)}
.hero h1 {font-size:2.1rem; margin:0 0 8px; letter-spacing:-.035em}
.hero p {font-size:1rem; margin:0; opacity:.94}
.pills {display:flex; flex-wrap:wrap; gap:8px; margin-top:15px}
.pill {padding:7px 11px; border:1px solid rgba(255,255,255,.2); background:rgba(255,255,255,.13); border-radius:999px; font-size:.82rem}
.cloudbox {padding:16px 18px; border-radius:16px; background:#edf8ff; border:1px solid #b9dfff; color:#164d73; margin:8px 0 18px}
.safebox {padding:15px 18px; border-radius:16px; background:#effcf5; border:1px solid #bdebd0; color:#155d36; margin:8px 0 18px}
.step {font-size:1.12rem; font-weight:850; margin:12px 0 7px}
.note {padding:13px 15px; border-radius:14px; background:#f5f2ff; border:1px solid #ddd5ff; color:#49377a; font-size:.91rem}
.stButton > button {border-radius:14px; min-height:52px; font-weight:800}
[data-testid="stDownloadButton"] button {border-radius:12px; font-weight:750}
@media (max-width: 700px) {.hero {padding:22px 20px}.hero h1 {font-size:1.65rem}.block-container {padding-left:.8rem; padding-right:.8rem}}
</style>
<div class="hero">
  <h1>🎬 쇼츠메이커 CLOUD V3.0</h1>
  <p>집 노트북·회사 PC·휴대폰에서 같은 웹주소로 접속해, 기사 기반 쇼츠를 제작하는 클라우드 버전입니다.</p>
  <div class="pills">
    <span class="pill">API 키 입력 없음</span>
    <span class="pill">개인 PC 서버 불필요</span>
    <span class="pill">기사 사진 자동 사용 금지</span>
    <span class="pill">내 목소리 보정</span>
    <span class="pill">콘텐츠별 자동 BGM</span>
  </div>
</div>
<div class="cloudbox"><b>접속 방식</b><br>기존 Streamlit 웹주소 하나만 사용합니다. 집에서는 노트북, 회사에서는 회사 PC, 밖에서는 휴대폰으로 같은 주소를 열면 됩니다. 어느 기기도 서버 역할을 하지 않습니다.</div>
<div class="safebox"><b>저작권 안전 기본값</b><br>기사 문장과 기사 사진을 그대로 가져오지 않습니다. 제목·핵심어·수치만 구조화해 새 해설 대본을 만들고, 화면은 프로그램이 직접 생성한 에디토리얼 그래픽으로 구성합니다.</div>
""",
    unsafe_allow_html=True,
)

with st.expander("📱 휴대폰·PC에 바로가기 설치", expanded=False):
    st.markdown(
        """
- **안드로이드 Chrome:** 오른쪽 위 `⋮` → `홈 화면에 추가` 또는 `앱 설치`
- **아이폰 Safari:** 하단 `공유` → `홈 화면에 추가`
- **Windows Chrome/Edge:** 주소창 오른쪽의 설치 또는 바로가기 아이콘

설치 후에도 실제 프로그램은 클라우드에서 실행되므로 집 노트북이나 회사 PC를 켜둘 필요가 없습니다.
"""
    )

with st.expander("다른 기기에서 이어서 수정할 프로젝트 불러오기", expanded=False):
    project_upload = st.file_uploader("프로젝트 JSON", type=["json"], key="project-import")
    if project_upload is not None and st.button("프로젝트 불러오기", use_container_width=True):
        try:
            payload = json.loads(project_upload.getvalue().decode("utf-8"))
            article = ArticleData(**payload["article"])
            plan = normalize_plan(payload["plan"], article, int(payload.get("duration", 45)))
            st.session_state[STATE_PLAN] = {
                "article": article,
                "plan": plan,
                "category": payload.get("category", "연예"),
                "duration": int(payload.get("duration", 45)),
                "engine": "무키 자체 해설 엔진",
            }
            st.session_state.pop(STATE_RESULT, None)
            st.success("프로젝트를 불러왔습니다.")
            st.rerun()
        except Exception as exc:
            st.error(f"프로젝트 파일을 읽지 못했습니다: {exc}")

st.markdown('<div class="step">1. 기사와 제작 조건 입력</div>', unsafe_allow_html=True)
url = st.text_input("대표 기사 링크", placeholder="https://m.entertain.naver.com/article/...")
manual_title = st.text_input("기사 제목 직접 입력 (링크 분석이 막힐 때만)", placeholder="기사 제목")
manual_text = st.text_area(
    "기사 본문 직접 붙여넣기 (선택)",
    placeholder="네이버 등 일부 사이트에서 본문 추출이 막히면 기사 본문을 붙여넣으세요. 기사 사진은 가져오지 않습니다.",
    height=130,
)

c1, c2 = st.columns(2)
with c1:
    category = st.selectbox("콘텐츠 유형", ["연예", "국내 이슈", "해외 이슈", "경제·주식", "생활정보", "제품·리뷰"])
with c2:
    target_duration = st.selectbox("목표 길이", [30, 45, 60], index=1, format_func=lambda x: f"{x}초")

make_plan = st.button("🧠 키 없이 새 해설 대본 만들기", type="primary", use_container_width=True)
if make_plan:
    if not url.strip() and not manual_text.strip():
        st.error("기사 링크 또는 기사 본문을 입력해주세요.")
        st.stop()
    progress = st.progress(0, text="기사를 확인하고 있습니다.")
    status = st.empty()
    try:
        article: ArticleData
        if url.strip():
            try:
                article = fetch_article(url.strip())
            except Exception as exc:
                if not manual_text.strip():
                    raise ShortsMakerError(f"기사 본문을 불러오지 못했습니다. 아래 본문 입력칸에 내용을 붙여넣어 주세요.\n\n{exc}")
                article = ArticleData(
                    url=url.strip(),
                    title=manual_title.strip() or "기사 기반 쇼츠",
                    publisher="직접 입력",
                    published_date="",
                    text=manual_text.strip(),
                    images=[],
                )
        else:
            article = ArticleData(
                url="직접 입력",
                title=manual_title.strip() or "기사 기반 쇼츠",
                publisher="직접 입력",
                published_date="",
                text=manual_text.strip(),
                images=[],
            )
        if manual_text.strip():
            article.text = manual_text.strip()
        if manual_title.strip():
            article.title = manual_title.strip()

        progress.progress(42, text="기사 문장이 아닌 핵심어와 수치를 분리했습니다.")
        raw = generate_zero_key_plan(article, target_duration, category)
        plan = normalize_plan(raw, article, target_duration)
        progress.progress(100, text="대본이 준비됐습니다.")
        status.success(f"완료: 무키 자체 해설 엔진 · {len(plan['scenes'])}개 장면")
        st.session_state[STATE_PLAN] = {
            "article": article,
            "plan": plan,
            "category": category,
            "duration": target_duration,
            "engine": "무키 자체 해설 엔진",
        }
        st.session_state.pop(STATE_RESULT, None)
    except ShortsMakerError as exc:
        progress.empty(); status.empty(); st.error(str(exc))
    except Exception as exc:
        progress.empty(); status.empty(); st.exception(exc)

plan_state = st.session_state.get(STATE_PLAN)
if plan_state:
    article: ArticleData = plan_state["article"]
    plan = plan_state["plan"]
    category = plan_state["category"]
    duration = int(plan_state["duration"])

    st.divider()
    st.markdown('<div class="step">2. 대본 확인·수정</div>', unsafe_allow_html=True)
    left, right = st.columns([1.15, 1])
    with left:
        edited_title = st.text_input("영상 제목", value=plan["video_title"], key="edit-title")
        edited_script = st.text_area("읽을 전체 대본", value=plan["narration"], height=270, key="edit-script")
        if st.button("✍️ 수정 대본을 장면에 적용", use_container_width=True):
            try:
                plan["video_title"] = edited_title.strip() or plan["video_title"]
                plan = plan_from_edited_narration(plan, edited_script, article, duration)
                st.session_state[STATE_PLAN]["plan"] = plan
                st.success("수정한 문장 기준으로 자막과 장면을 다시 나눴습니다.")
                st.rerun()
            except ShortsMakerError as exc:
                st.error(str(exc))
    with right:
        st.markdown("**프로그램이 추출한 정보**")
        for fact in plan.get("core_facts") or [article.title]:
            st.write(f"• {fact}")
        overlap = int(plan.get("originality_overlap_words") or 0)
        if overlap >= 8:
            st.warning(f"기사 원문과 최대 {overlap}어절이 연속으로 겹칠 수 있습니다. 대본을 한 번 더 수정하는 편이 안전합니다.")
        else:
            st.success(f"원문 연속 유사 표현 검사: 최대 {overlap}어절")
        project_payload = {
            "version": VERSION,
            "article": asdict(article),
            "plan": plan,
            "category": category,
            "duration": duration,
        }
        st.download_button(
            "💾 다른 기기용 프로젝트 저장",
            json.dumps(project_payload, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name="shorts_project_v30.json",
            mime="application/json",
            use_container_width=True,
        )
        st.markdown('<div class="note"><b>기기 간 사용</b><br>새 영상은 어느 기기에서든 바로 만들 수 있습니다. 작업 중인 대본을 다른 기기로 옮길 때는 위 프로젝트 파일을 저장한 뒤 불러오세요.</div>', unsafe_allow_html=True)

    st.markdown('<div class="step">3. 나레이션 선택</div>', unsafe_allow_html=True)
    narration_mode = st.radio("나레이션 방식", ["AI 성우로 자동 제작", "내 목소리 녹음·업로드 후 보정"], horizontal=True)
    voice_label = list(VOICE_OPTIONS)[0]
    rate_label = "쇼츠 추천"
    voice_preset_label = "밝고 듣기 좋게"
    uploaded_voice = None
    recorded_audio = None
    if narration_mode == "AI 성우로 자동 제작":
        v1, v2 = st.columns(2)
        with v1:
            voice_label = st.selectbox("AI 성우", list(VOICE_OPTIONS), index=0)
        with v2:
            rate_label = st.selectbox("말하기 속도", list(RATE_OPTIONS), index=2)
        st.caption("AI 성우는 별도 키 입력 없이 동작하지만, 외부 음성 서비스 상태에 따라 일시적으로 지연될 수 있습니다.")
    else:
        v1, v2 = st.columns(2)
        with v1:
            voice_preset_label = st.selectbox("내 목소리 보정 스타일", list(VOICE_PRESET_OPTIONS), index=1)
        with v2:
            uploaded_voice = st.file_uploader("대본 전체를 읽은 녹음 파일", type=["wav", "mp3", "m4a", "aac", "ogg"])
        if hasattr(st, "audio_input"):
            recorded_audio = st.audio_input("또는 지금 대본을 직접 녹음")
        st.caption("올려주신 짧은 목소리 샘플로 새 문장을 복제하는 방식이 아니라, 대본 전체 녹음을 밝게·재밌게·차분하게 보정하는 방식입니다.")

    st.markdown('<div class="step">4. 콘텐츠별 자동 배경음악</div>', unsafe_allow_html=True)
    m1, m2, m3 = st.columns(3)
    with m1:
        music_ui = st.selectbox("음악 방식", ["콘텐츠에 맞춰 자동 추천", "내장 오리지널 중 직접 선택", "YouTube 오디오 라이브러리 음원 업로드", "음악 사용 안 함"])
    with m2:
        selected_music = st.selectbox("내장 음악", list(MUSIC_TRACK_OPTIONS), disabled=music_ui != "내장 오리지널 중 직접 선택")
    with m3:
        music_volume_pct = st.slider("배경음악 크기", 3, 25, 10, 1, format="%d%%", disabled=music_ui == "음악 사용 안 함")
    custom_music_upload = None
    attribution_text = ""
    if music_ui == "YouTube 오디오 라이브러리 음원 업로드":
        c1, c2 = st.columns(2)
        with c1:
            custom_music_upload = st.file_uploader("다운로드한 음원", type=["mp3", "wav", "m4a", "aac", "ogg"], key="music-upload")
        with c2:
            attribution_text = st.text_input("저작자 표시 문구 (필요한 곡만)", placeholder="Music: 곡명 - 아티스트")

    st.markdown('<div class="step">5. 영상 디자인</div>', unsafe_allow_html=True)
    d1, d2, d3 = st.columns(3)
    with d1:
        template_label = st.selectbox("영상 템플릿", list(TEMPLATE_OPTIONS), index=0)
    with d2:
        subtitle_label = st.selectbox("자막 스타일", list(SUBTITLE_STYLE_OPTIONS), index=0)
    with d3:
        accent_label = st.selectbox("강조 색상", list(ACCENT_COLOR_OPTIONS), index=0)
    d4, d5 = st.columns(2)
    with d4:
        resolution_label = st.selectbox("출력 해상도", list(RESOLUTION_OPTIONS), index=0)
    with d5:
        show_hook = st.toggle("상단 후킹 제목", value=True)
    st.caption("화면의 '연예' 카테고리 배지는 표시하지 않습니다. 외부 기사 사진 대신 장면별 상징 그래픽을 자동 생성합니다.")

    render = st.button("🎬 클라우드에서 쇼츠 완성하기", type="primary", use_container_width=True)
    if render:
        source_audio = uploaded_voice or recorded_audio
        if narration_mode != "AI 성우로 자동 제작" and source_audio is None:
            st.error("내 목소리 방식은 대본 전체를 읽은 녹음 파일 또는 브라우저 녹음이 필요합니다.")
            st.stop()
        if music_ui == "YouTube 오디오 라이브러리 음원 업로드" and custom_music_upload is None:
            st.error("사용할 음원을 업로드해주세요.")
            st.stop()

        # 화면에서 마지막으로 수정한 대본을 자동 반영
        plan["video_title"] = edited_title.strip() or plan["video_title"]
        plan = plan_from_edited_narration(plan, edited_script, article, duration)
        st.session_state[STATE_PLAN]["plan"] = plan

        workdir = Path(tempfile.mkdtemp(prefix="shortsmaker_cloud_v30_"))
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
            "내장 오리지널 중 직접 선택": "built_in",
            "YouTube 오디오 라이브러리 음원 업로드": "upload",
            "음악 사용 안 함": "none",
        }
        progress = st.progress(0, text="클라우드 영상 제작을 시작합니다.")
        status = st.empty()
        try:
            files = render_video(
                article=article,
                plan=plan,
                voice=VOICE_OPTIONS[voice_label]["voice"],
                rate=RATE_OPTIONS[rate_label],
                pexels_key="",
                workdir=workdir,
                progress=lambda message: status.info(message),
                template=TEMPLATE_OPTIONS[template_label],
                subtitle_style=SUBTITLE_STYLE_OPTIONS[subtitle_label],
                accent=ACCENT_COLOR_OPTIONS[accent_label],
                background_mode="auto",
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
            status.success("영상·자막·음성·출처·권리 점검 파일을 만들었습니다.")
            result_key = hashlib.sha256(files["video"].read_bytes()[:4096]).hexdigest()
            st.session_state[STATE_RESULT] = {
                "key": result_key,
                "article": article,
                "plan": plan,
                **{name: path.read_bytes() for name, path in files.items()},
            }
        except ShortsMakerError as exc:
            progress.empty(); status.empty(); st.error(str(exc))
        except Exception as exc:
            progress.empty(); status.empty(); st.exception(exc)

result = st.session_state.get(STATE_RESULT)
if result:
    st.divider()
    left, right = st.columns([1, 1])
    with left:
        st.subheader("완성 영상")
        st.video(result["video"], format="video/mp4")
        st.download_button("⬇️ 결과 전체 ZIP", result["zip"], "shorts_v30_result.zip", "application/zip", type="primary", use_container_width=True, key=f"zip-{result['key']}")
        d1, d2 = st.columns(2)
        with d1:
            st.download_button("MP4 다운로드", result["video"], "final_short_v30.mp4", "video/mp4", use_container_width=True)
        with d2:
            st.download_button("나레이션 다운로드", result["audio"], "narration_v30.mp3", "audio/mpeg", use_container_width=True)
    with right:
        st.subheader("저작권 안전 점검")
        st.text(result["copyright_report"].decode("utf-8-sig", errors="replace"))
        st.download_button("점검 보고서", result["copyright_report"], "copyright_check_report.txt", "text/plain", use_container_width=True)
        st.download_button("AI 공개 안내", result["ai_disclosure"], "ai_disclosure.txt", "text/plain", use_container_width=True)

st.caption("CLOUD V3.0은 API 키 없이 작동하는 자체 대본·그래픽 엔진을 사용합니다. 자동 검사는 위험을 줄이는 장치이며 최종 게시 전 사실관계와 음원 조건을 확인해야 합니다.")
