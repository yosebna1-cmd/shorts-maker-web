from __future__ import annotations

import asyncio
import base64
import html as html_lib
import io
import json
import math
import os
import re
import subprocess
import tempfile
import textwrap
import urllib.parse
import wave
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

try:
    import trafilatura
except Exception:  # pragma: no cover
    trafilatura = None

try:
    import edge_tts
except Exception:  # pragma: no cover
    edge_tts = None


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)

VOICE_OPTIONS: dict[str, dict[str, str]] = {
    "지민 · 밝고 경쾌한 쇼츠형": {"voice": "ko-KR-JiMinNeural", "gender": "여성"},
    "서현 · 또렷한 뉴스형": {"voice": "ko-KR-SeoHyeonNeural", "gender": "여성"},
    "유진 · 자연스러운 종합형": {"voice": "ko-KR-YuJinNeural", "gender": "여성"},
    "순복 · 편안한 사연형": {"voice": "ko-KR-SoonBokNeural", "gender": "여성"},
    "선희 · 안정적인 정보형": {"voice": "ko-KR-SunHiNeural", "gender": "여성"},
    "현수 · 젊고 빠른 쇼츠형": {"voice": "ko-KR-HyunsuNeural", "gender": "남성"},
    "봉진 · 힘 있는 이슈형": {"voice": "ko-KR-BongJinNeural", "gender": "남성"},
    "국민 · 묵직한 경제형": {"voice": "ko-KR-GookMinNeural", "gender": "남성"},
    "인준 · 차분한 해설형": {"voice": "ko-KR-InJoonNeural", "gender": "남성"},
    "현수 다국어 · 외래어 친화형": {"voice": "ko-KR-HyunsuMultilingualNeural", "gender": "남성"},
}


COPYRIGHT_SOURCE_OPTIONS = [
    "직접 촬영/직접 제작",
    "사용 허락을 받은 사진·영상",
    "상업 이용이 허용된 라이선스 자료",
    "기사·언론사 사진",
    "연예인/공개계정 SNS 사진·캡처",
    "방송·유튜브·타 크리에이터 영상/캡처",
    "출처·권리관계 불명",
]

COPYRIGHT_BASIS_OPTIONS = [
    "직접 제작하여 권리 보유",
    "권리자로부터 상업적 사용 허락 확보",
    "라이선스에서 상업적 이용 및 재편집 허용 확인",
    "출처만 알고 있으며 별도 허락 없음",
    "확인하지 못함",
]

def audit_visual_rights(source_type: str, basis: str) -> dict[str, str]:
    """보수적 사전 필터. 법률 판단이 아니라 위험 자료의 자동 사용을 막는 안전장치."""
    green_basis = {
        "직접 제작하여 권리 보유",
        "권리자로부터 상업적 사용 허락 확보",
        "라이선스에서 상업적 이용 및 재편집 허용 확인",
    }
    if source_type == "직접 촬영/직접 제작" and basis == "직접 제작하여 권리 보유":
        return {"grade": "GREEN", "label": "🟢 사용 가능", "reason": "직접 제작 자료로 표시됨"}
    if basis in green_basis and source_type not in {"출처·권리관계 불명"}:
        return {"grade": "GREEN", "label": "🟢 사용 가능", "reason": "상업적 사용 근거를 사용자가 확인함"}
    if source_type in {"방송·유튜브·타 크리에이터 영상/캡처", "출처·권리관계 불명"}:
        return {"grade": "RED", "label": "🔴 사용 제외", "reason": "무단 재사용 위험이 높거나 권리관계가 불명확함"}
    if source_type in {"기사·언론사 사진", "연예인/공개계정 SNS 사진·캡처"}:
        return {"grade": "YELLOW", "label": "🟡 확인 필요", "reason": "공개된 자료라도 재사용 권한이 자동으로 생기지 않음"}
    return {"grade": "YELLOW", "label": "🟡 확인 필요", "reason": "상업적 사용·재편집 권한을 추가 확인해야 함"}


def music_rights_audit(music_ui: str, cross_platform_confirmed: bool = False) -> dict[str, str]:
    if music_ui == "음악 사용 안 함":
        return {"grade": "GREEN", "label": "🟢 사용 가능", "reason": "마스터 영상에 BGM을 삽입하지 않음"}
    if music_ui in {"콘텐츠에 맞춰 자동 추천", "내장 오리지널 중 직접 선택"}:
        return {"grade": "GREEN", "label": "🟢 사용 가능", "reason": "프로그램 자체 오리지널 트랙 사용"}
    if music_ui == "YouTube 오디오 라이브러리 음원 업로드":
        if cross_platform_confirmed:
            return {"grade": "GREEN", "label": "🟢 사용 가능", "reason": "사용자가 YouTube·네이버 양쪽 사용 조건을 확인함"}
        return {"grade": "YELLOW", "label": "🟡 확인 필요", "reason": "YouTube용 음원의 네이버 클립 재사용 권한은 별도 확인 필요"}
    return {"grade": "YELLOW", "label": "🟡 확인 필요", "reason": "음원 이용 조건 확인 필요"}


def build_copyright_report(asset_rows: list[dict[str, str]], music_audit: dict[str, str], article_overlap: int = 0) -> str:
    lines = [
        "SHORTS MAKER V4.0 · COPYRIGHT SAFE 사전 점검",
        "",
        "[대본]",
        "- 기사 사실 기반 독자적 재작성",
        f"- 기사 원문과 최장 연속 유사 어절: {article_overlap}어절",
        "",
        "[시각 자료]",
    ]
    if not asset_rows:
        lines.append("- 사용자 실제 자료 없음 · 최종 렌더러는 승인 자료만 사용 권장")
    for row in asset_rows:
        lines.append(f"- {row['label']} {row['name']} | {row['source_type']} | {row['reason']}")
    lines += ["", "[음악]", f"- {music_audit['label']} {music_audit['reason']}", ""]
    blocked = [r for r in asset_rows if r['grade'] != 'GREEN']
    lines.append("[판정]")
    if blocked or music_audit['grade'] != 'GREEN':
        lines.append("- ⚠️ 확인 필요/사용 제외 자료는 최종 영상에 자동 삽입하지 않습니다.")
    else:
        lines.append("- ✅ 등록된 자료 기준 사전 필터 통과")
    lines.append("- 이 검사는 위험을 줄이는 보수적 사전 필터이며 법적 무침해를 보증하지 않습니다.")
    return "\n".join(lines) + "\n"

RATE_OPTIONS = {
    "보통": "+0%",
    "조금 빠르게": "+8%",
    "쇼츠 추천": "+15%",
    "매우 빠르게": "+22%",
}

TEMPLATE_OPTIONS = {
    "자막 강조형 · 요즘 쇼츠 추천": "highlight",
    "뉴스형 · 깔끔하고 신뢰감": "news",
    "카드형 · 정보 정리형": "card",
}

SUBTITLE_STYLE_OPTIONS = {
    "강조형 · 핵심 단어 컬러": "accent",
    "깔끔형 · 흰색 볼드": "clean",
    "반투명 박스형 · 가독성 우선": "box",
}

ACCENT_COLOR_OPTIONS = {
    "노랑 · 시선 집중": (255, 216, 77),
    "민트 · 산뜻한 느낌": (92, 225, 230),
    "보라 · 세련된 느낌": (171, 126, 255),
    "핑크 · 연예·화제형": (255, 112, 166),
}

BACKGROUND_MODE_OPTIONS = {
    "자동 추천": "auto",
    "화면 꽉 채우기": "cover",
    "블러 배경 + 원본": "blur",
}

RESOLUTION_OPTIONS = {
    "720×1280 · 빠른 제작": (720, 1280),
    "1080×1920 · 고화질": (1080, 1920),
}


VOICE_PRESET_OPTIONS = {
    "깔끔하고 자연스럽게": "clean",
    "밝고 듣기 좋게": "bright",
    "재밌고 경쾌하게": "fun",
    "차분하고 따뜻하게": "warm",
}

MUSIC_TRACK_OPTIONS = {
    "연예·화제 팝": "01_entertainment_pop.mp3",
    "뉴스·이슈 펄스": "02_news_pulse.mp3",
    "경제·정보 미니멀": "03_economy_minimal.mp3",
    "생활·사연 따뜻함": "04_lifestyle_warm.mp3",
    "제품·리뷰 업비트": "05_review_upbeat.mp3",
    "진지한 이슈·차분함": "06_serious_calm.mp3",
}

MUSIC_AUTO_BY_CATEGORY = {
    "연예": "연예·화제 팝",
    "국내 이슈": "뉴스·이슈 펄스",
    "해외 이슈": "뉴스·이슈 펄스",
    "경제·주식": "경제·정보 미니멀",
    "생활정보": "생활·사연 따뜻함",
    "제품·리뷰": "제품·리뷰 업비트",
}


@dataclass
class ArticleData:
    url: str
    title: str
    publisher: str
    published_date: str
    text: str
    images: list[str]


class ShortsMakerError(RuntimeError):
    pass


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = (value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _meta_content(soup: BeautifulSoup, *selectors: tuple[str, str]) -> str:
    for attr, value in selectors:
        tag = soup.find("meta", attrs={attr: value})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    return ""


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def fetch_article(url: str, timeout: int = 25) -> ArticleData:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ShortsMakerError("올바른 기사 주소를 입력해주세요.")

    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": f"{parsed.scheme}://{parsed.netloc}/",
        },
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    html_text = response.text
    soup = BeautifulSoup(html_text, "html.parser")

    title = (
        _meta_content(soup, ("property", "og:title"), ("name", "twitter:title"))
        or (soup.title.get_text(" ", strip=True) if soup.title else "")
        or "기사 기반 쇼츠"
    )
    publisher = _meta_content(
        soup,
        ("property", "og:site_name"),
        ("name", "author"),
        ("property", "article:author"),
    )
    published_date = _meta_content(
        soup,
        ("property", "article:published_time"),
        ("name", "date"),
        ("name", "pubdate"),
    )

    json_bodies: list[str] = []
    json_images: list[str] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            parsed_json = json.loads(tag.string or tag.get_text())
        except Exception:
            continue
        for obj in _walk_json(parsed_json):
            body = obj.get("articleBody")
            if isinstance(body, str) and len(body) > 100:
                json_bodies.append(body)
            image = obj.get("image")
            if isinstance(image, str):
                json_images.append(image)
            elif isinstance(image, list):
                json_images.extend([x for x in image if isinstance(x, str)])
            elif isinstance(image, dict) and isinstance(image.get("url"), str):
                json_images.append(image["url"])
            if not publisher and isinstance(obj.get("publisher"), dict):
                publisher = str(obj["publisher"].get("name") or "")
            if not published_date:
                published_date = str(obj.get("datePublished") or "")

    selector_candidates = [
        "#dic_area",
        "#newsct_article",
        "#articleBodyContents",
        "#articeBody",
        "#article_body",
        ".news_end",
        ".article_body",
        ".article-body",
        ".article_view",
        ".article-content",
        ".end_body_wrp",
        ".story-news",
        "article",
        "main",
    ]
    candidates: list[str] = []
    for selector in selector_candidates:
        node = soup.select_one(selector)
        if node:
            for noisy in node.select("script, style, iframe, noscript, button, nav, aside"):
                noisy.decompose()
            text = node.get_text("\n", strip=True)
            if len(text) > 200:
                candidates.append(text)

    if trafilatura is not None:
        try:
            extracted = trafilatura.extract(
                html_text,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
                output_format="txt",
            )
            if extracted and len(extracted) > 200:
                candidates.append(extracted)
        except Exception:
            pass

    paragraph_text = "\n".join(
        p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text(" ", strip=True)) > 30
    )
    if len(paragraph_text) > 200:
        candidates.append(paragraph_text)

    candidates.extend(json_bodies)
    body = max(candidates, key=len, default="")
    body = html_lib.unescape(body)
    body = re.sub(r"[\t\r ]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    image_urls: list[str] = [
        _meta_content(soup, ("property", "og:image"), ("name", "twitter:image")),
        *json_images,
    ]
    for selector in selector_candidates:
        node = soup.select_one(selector)
        if not node:
            continue
        for img in node.find_all("img"):
            candidate = (
                img.get("data-src")
                or img.get("data-original")
                or img.get("data-lazy-src")
                or img.get("src")
                or ""
            )
            if candidate:
                image_urls.append(candidate)

    normalized_images: list[str] = []
    for image_url in image_urls:
        if not image_url or image_url.startswith("data:"):
            continue
        absolute = urllib.parse.urljoin(url, image_url)
        if absolute.startswith("http"):
            normalized_images.append(absolute)

    return ArticleData(
        url=url,
        title=re.sub(r"\s+", " ", title).strip(),
        publisher=re.sub(r"\s+", " ", publisher).strip(),
        published_date=published_date.strip(),
        text=body,
        images=_unique(normalized_images)[:15],
    )


def _extract_json(text: str) -> dict[str, Any]:
    clean = text.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start >= 0 and end > start:
            return json.loads(clean[start : end + 1])
        raise


def _gemini_request(api_key: str, model: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    response = requests.post(
        endpoint,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if not response.ok:
        detail = response.text[:700]
        raise ShortsMakerError(f"Gemini 호출 실패 ({response.status_code}): {detail}")
    return response.json()


def _response_text(data: dict[str, Any]) -> str:
    parts = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )
    texts = [str(part.get("text", "")) for part in parts if part.get("text")]
    if not texts:
        reason = data.get("promptFeedback") or data
        raise ShortsMakerError(f"Gemini 응답에 대본이 없습니다: {str(reason)[:500]}")
    return "\n".join(texts)



def _normalized_tokens(text: str) -> list[str]:
    return [
        token for token in re.findall(r"[0-9A-Za-z가-힣]+", (text or "").lower())
        if token
    ]


def _longest_shared_phrase_words(source: str, target: str, max_n: int = 14) -> int:
    source_tokens = _normalized_tokens(source)
    target_tokens = _normalized_tokens(target)
    if not source_tokens or not target_tokens:
        return 0
    source_joined = " ".join(source_tokens)
    upper = min(max_n, len(target_tokens))
    for n in range(upper, 5, -1):
        for idx in range(0, len(target_tokens) - n + 1):
            phrase = " ".join(target_tokens[idx:idx+n])
            if phrase in source_joined:
                return n
    return 0

def generate_plan_with_gemini(
    article: ArticleData,
    api_key: str,
    duration: int,
    category: str,
) -> dict[str, Any]:
    body = article.text[:26000]
    source_note = (
        f"\n추출된 기사 본문:\n{body}" if len(body) >= 300 else "\n본문 추출이 짧으므로 URL Context로 원문을 직접 확인하세요."
    )
    prompt = f"""
당신은 저작권과 사실 검증을 중시하는 한국어 유튜브 쇼츠 제작자입니다.
다음 기사 한 건을 바탕으로 {duration}초 분량의 독창적인 쇼츠 대본과 장면 계획을 만드세요.
기사 URL: {article.url}
기사 제목: {article.title}
콘텐츠 유형: {category}
{source_note}

규칙:
- 기사 문장을 요약하거나 문장 순서를 따라가지 말고, 인물·날짜·금액·공식 발표 등 확인 가능한 사실만 먼저 구조화합니다.
- 원문과 8어절 이상 연속으로 같은 표현을 만들지 않습니다. 기사 제목도 그대로 후킹 문구로 사용하지 않습니다.
- 기사 사진·방송 화면을 사용하지 않는 전제로, 실존 인물과 닮지 않은 에디토리얼 그래픽·사물·장소·실루엣 중심의 장면을 설계합니다.
- 기사 내용을 그대로 전달하는 데서 끝내지 말고 쟁점·배경·오해하기 쉬운 점을 자체 해설로 덧붙입니다.
- 확인되지 않은 추측, 과장, 명예훼손 표현을 넣지 않습니다.
- 첫 장면은 2초 이내의 강한 궁금증형 후킹 문장입니다.
- 전체 나레이션은 자연스러운 한국어 구어체이며 {duration}초 안에 읽을 수 있어야 합니다.
- 장면은 6~9개, 장면별 화면 자막은 18자 안팎으로 짧게 씁니다.
- caption은 최대 2줄로 자연스럽게 나눌 수 있는 문장으로 작성합니다.
- emphasis는 화면에서 강조할 핵심 단어 1~2개를 배열로 작성합니다.
- 마지막은 시청자 의견을 묻는 한 문장으로 끝냅니다.
- stock_keywords는 특정 연예인 얼굴이나 방송 캡처가 아닌, 사물·장소·상황 중심의 상업 이용 가능한 스톡 이미지 검색용 영어 단어 2~4개로 작성합니다.
- visual_note는 실존 인물을 복제하지 않는 독창적인 에디토리얼 그래픽 지시문으로 작성합니다.

다음 JSON 구조로만 답하세요.
{{
  "video_title": "",
  "hook": "",
  "narration": "",
  "description": "",
  "hashtags": ["#쇼츠"],
  "core_facts": ["", "", ""],
  "scenes": [
    {{"caption": "", "emphasis": [""], "narration": "", "stock_keywords": "", "visual_note": ""}}
  ]
}}
""".strip()

    payload: dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.35,
            "responseMimeType": "application/json",
            "maxOutputTokens": 4096,
        },
        "tools": [{"url_context": {}}],
    }
    errors: list[str] = []
    for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
        try:
            candidate = _extract_json(_response_text(_gemini_request(api_key, model, payload)))
            target_text = " ".join([
                str(candidate.get("video_title") or ""),
                str(candidate.get("hook") or ""),
                str(candidate.get("narration") or ""),
                " ".join(str(scene.get("caption") or "") for scene in (candidate.get("scenes") or []) if isinstance(scene, dict)),
            ])
            overlap = _longest_shared_phrase_words(article.text, target_text)
            if overlap >= 8:
                revise_prompt = f"""
아래 JSON 대본은 기사 원문과 {overlap}어절 이상 연속으로 겹치는 표현이 발견되었습니다.
사실·고유명사·수치만 유지하고, 문장 구조·정보 순서·후킹·해설을 완전히 새롭게 바꾸세요.
직접 인용은 사용하지 마세요. JSON 구조는 그대로 유지하세요.

기존 JSON:
{json.dumps(candidate, ensure_ascii=False)}
""".strip()
                revise_payload = {
                    "contents": [{"parts": [{"text": revise_prompt}]}],
                    "generationConfig": {
                        "temperature": 0.55,
                        "responseMimeType": "application/json",
                        "maxOutputTokens": 4096,
                    },
                }
                candidate = _extract_json(_response_text(_gemini_request(api_key, model, revise_payload)))
                target_text = " ".join([
                    str(candidate.get("video_title") or ""),
                    str(candidate.get("hook") or ""),
                    str(candidate.get("narration") or ""),
                ])
                overlap = _longest_shared_phrase_words(article.text, target_text)
            candidate["originality_overlap_words"] = overlap
            if overlap >= 10:
                raise ShortsMakerError(f"원문과 {overlap}어절 연속 유사 표현이 남아 있어 제작을 중단했습니다. 다시 대본을 생성해주세요.")
            return candidate
        except Exception as exc:
            errors.append(f"{model}: {exc}")
    raise ShortsMakerError(" / ".join(errors))


def _split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text)
    return [part.strip(" -•\t") for part in parts if 25 <= len(part.strip()) <= 240]


def _pick_emphasis(text: str, limit: int = 2) -> list[str]:
    stopwords = {
        "그리고", "하지만", "때문에", "관련", "대한", "이번", "사실", "정말", "바로",
        "이유", "소식", "현재", "오늘", "여러분", "어떻게", "합니다", "했습니다",
    }
    tokens = [
        re.sub(r"[^0-9A-Za-z가-힣·]", "", token)
        for token in re.split(r"\s+", text)
    ]
    candidates = [
        token for token in tokens
        if 2 <= len(token) <= 12 and token not in stopwords and not token.endswith(("입니다", "했습니다"))
    ]
    candidates = sorted(_unique(candidates), key=lambda value: (-len(value), tokens.index(value)))
    return candidates[:limit]


def _clean_article_title(title: str) -> str:
    clean = re.sub(r"\s*[|｜·-]\s*(네이버|연합뉴스|뉴스|스포츠|엔터|신문|일보|방송|TV).*$", "", title or "", flags=re.I)
    clean = re.sub(r"\[[^\]]{1,18}\]", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" -|｜·")
    return clean or "오늘의 주요 소식"


def _top_keywords(text: str, title: str, limit: int = 8) -> list[str]:
    stopwords = {
        "기자", "뉴스", "사진", "영상", "관련", "대한", "이번", "현재", "지난", "오늘", "당시",
        "통해", "위해", "것으로", "것이다", "있다", "없다", "했다", "한다고", "하며", "에서", "으로",
        "에게", "까지", "부터", "그리고", "하지만", "때문", "그는", "그녀는", "이날", "밝혔다", "전했다",
        "공개", "소식", "내용", "사실", "경우", "여부", "대해", "따르면", "보도", "오전", "오후",
    }
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9._-]{1,20}|[가-힣]{2,12}|\d+(?:[.,]\d+)?%?", f"{title} {text}")
    score = Counter()
    for token in tokens:
        token = token.strip("._-")
        if token in stopwords or token.lower() in stopwords or re.fullmatch(r"\d{1,2}", token):
            continue
        score[token] += 1
    for token in re.findall(r"[A-Za-z][A-Za-z0-9._-]{1,20}|[가-힣]{2,12}|\d+(?:[.,]\d+)?%?", title):
        if token not in stopwords:
            score[token] += 4
    return [token for token, _ in score.most_common(limit)]


def _extract_numbers(text: str, limit: int = 4) -> list[str]:
    patterns = [
        r"\d{4}년(?:\s*\d{1,2}월(?:\s*\d{1,2}일)?)?",
        r"\d{1,3}(?:,\d{3})+(?:원|달러|명|개|건|회|위|억|만)?",
        r"\d+(?:\.\d+)?%",
        r"\d+(?:\.\d+)?(?:억|만|천)?(?:원|달러|명|개|건|회|위|개월|년|일)",
    ]
    values: list[str] = []
    for pattern in patterns:
        values.extend(re.findall(pattern, text))
    return _unique(values)[:limit]


def _category_context(category: str) -> str:
    return {
        "연예": "인물에 대한 평가보다 확인된 일정과 공식 발언을 중심으로 보는 것이 중요합니다.",
        "국내 이슈": "온라인 반응과 공식 확인 내용은 분리해서 살펴볼 필요가 있습니다.",
        "해외 이슈": "해외 보도는 번역 과정에서 의미가 달라질 수 있어 원발표와 후속 보도를 함께 봐야 합니다.",
        "경제·주식": "숫자의 크기보다 발표 시점과 실제 영향 범위를 함께 확인해야 합니다.",
        "생활정보": "조건과 적용 대상이 사람마다 다를 수 있으므로 세부 기준 확인이 필요합니다.",
        "제품·리뷰": "광고 문구보다 실제 사양과 사용 조건을 구분해서 보는 것이 핵심입니다.",
    }.get(category, "확인된 사실과 해석을 구분해서 보는 것이 중요합니다.")


def _v40_role(idx: int, total: int) -> str:
    if idx == 0: return "HOOK"
    if idx <= max(1, total // 4): return "SETUP"
    if idx <= max(2, total // 2): return "REVEAL"
    if idx < total - 2: return "REACTION"
    if idx == total - 2: return "PAYOFF"
    return "QUESTION"


def _v40_pacing_report(plan: dict[str, Any], duration: int) -> dict[str, Any]:
    scenes = plan.get("scenes") or []
    n = max(1, len(scenes))
    avg = float(duration) / n
    warnings = []
    score = 100
    if n < 9:
        warnings.append(f"컷이 {n}개뿐입니다. 연예 쇼츠는 9~16컷을 권장합니다.")
        score -= 22
    if avg > 3.0:
        warnings.append(f"컷당 평균 {avg:.1f}초로 느립니다. 1~2.5초대 화면 변화를 권장합니다.")
        score -= 20
    hook = str(plan.get("hook") or "")
    if len(hook) > 38:
        warnings.append("첫 후킹 문장이 깁니다. 첫 2초 안에 읽히도록 더 짧게 줄이세요.")
        score -= 12
    captions = [str(x.get("caption") or "") for x in scenes if isinstance(x, dict)]
    if captions and sum(len(c) > 24 for c in captions) >= max(2, n // 3):
        warnings.append("긴 화면 자막이 많습니다. 한 컷 한 메시지 원칙으로 줄이는 편이 좋습니다.")
        score -= 12
    if scenes and "?" not in str(scenes[-1].get("narration") or ""):
        warnings.append("마지막 댓글 유도 질문이 약합니다.")
        score -= 8
    return {"score": max(0, score), "avg_cut": avg, "warnings": warnings}


def _v40_entertainment_plan(article: ArticleData, duration: int, category: str) -> dict[str, Any]:
    """V4.0 entertainment story engine.

    Instead of mechanically summarizing the article, build a short-form story around
    consequence -> origin -> incident -> backlash -> response -> second consequence -> question.
    This is intentionally optimized for entertainment/news controversy and viral-story articles.
    """
    title = _clean_article_title(article.title)
    body = re.sub(r"\s+", " ", article.text or "").strip()
    sentences = _split_sentences(body)

    def pick(*terms: str, exclude: tuple[str, ...] = ()) -> str:
        ranked: list[tuple[int, int, str]] = []
        for idx, sentence in enumerate(sentences):
            if any(x in sentence for x in exclude):
                continue
            score = sum(3 if term in sentence else 0 for term in terms)
            if score:
                ranked.append((score, -idx, sentence))
        return max(ranked, default=(0, 0, ""))[2]

    # Named people: title patterns are more reliable than raw frequency because words such as
    # '아내', '논란', 'SNS' otherwise rise to the top of simple keyword extraction.
    m_primary = re.search(r"([가-힣]{2,4})\s*(?:논란|사건|사고|의혹)", title)
    m_secondary = re.search(r"(?:아내|남편|배우자|연인|멤버)\s*([가-힣]{2,4})", title)
    primary = m_primary.group(1) if m_primary else ""
    secondary = m_secondary.group(1) if m_secondary else ""
    if not primary:
        candidates = [x for x in _top_keywords(body, title, 12)
                      if x not in {"아내", "남편", "논란", "사고", "SNS", "영상", "최근", "기사"}]
        primary = candidates[0] if candidates else "당사자"
    if not secondary:
        m = re.search(r"(?:아내|남편|배우자|연인)\s*([가-힣]{2,4})", body)
        secondary = m.group(1) if m else "가족"

    def topic_particle(word: str) -> str:
        if not word or word == "가족":
            return "은"
        last = ord(word[-1]) - 0xAC00
        return "은" if 0 <= last <= 11171 and last % 28 else "는"

    origin = pick("시작됐", "시작되", "발생", "지난 14일", "KTX")
    incident = pick("휠체어", "하차", "넘어", "경사로")
    upload = pick("CCTV", "유튜브", "올렸다", "공개")
    criticism = pick("비판", "지적", "특정 개인", "공개적인 콘텐츠")
    exposure = pick("노출", "도와", "사회복무요원")
    apology = pick("삭제", "사과", "고개를 숙")
    spillover = pick(secondary, "SNS", "악성 댓글", "2차 피해", "비난의 화살")
    unrelated = pick("직접적인 관련", "무관", "단지", "아내라는 이유")

    # Short, spoken lines. Article sentences are used only to find facts; narration is newly written.
    if secondary != "가족":
        hook = f"{primary} 논란의 불똥이 결국 {secondary}에게까지 튀었습니다."
    else:
        hook = f"{primary} 논란의 불똥이 결국 가족에게까지 번졌습니다."

    lines: list[tuple[str, str, str]] = [
        ("HOOK", hook, f"{secondary if secondary != '가족' else primary} 얼굴 클로즈업 → {primary}로 0.6초 퀵컷, '왜 여기까지?' 대형 타이포"),
        ("SETUP", "시작은 지난 14일 KTX에서 벌어진 사고였습니다.", "KTX 외관/승강장 느낌 자료 + '8월 14일' 날짜 타이포"),
        ("SETUP", f"하차하던 {primary}의 휠체어가 경사로 쪽에서 걸리며 앞으로 넘어졌습니다.", "휠체어 바퀴·경사로를 실루엣/도식으로 2컷 분할"),
        ("REVEAL", f"이후 {primary}는 사고 당시 CCTV 영상을 자신의 채널에 공개했습니다.", "CCTV 프레임 모션 + PLAY 아이콘, 실제 CCTV 재현은 하지 않음"),
        ("REVEAL", "그런데 영상이 공개된 뒤 여론은 예상과 다른 방향으로 움직였습니다.", "화면 순간 정지 → '그런데' 대형 자막 → 댓글 카드 전환"),
        ("REACTION", "이미 사과한 도움 인력의 실수를 공개 콘텐츠로 다룬 게 적절했냐는 비판이 나온 겁니다.", "'이미 사과' / '공개 콘텐츠' 키워드 교차 타이포"),
        ("REACTION", "도움을 주던 사람의 모습이 그대로 노출된 점도 지적됐습니다.", "사람 실루엣을 모자이크 처리한 그래픽 + 경고 아이콘"),
        ("PAYOFF", f"결국 {primary}는 해당 영상을 삭제했습니다.", "DELETE 버튼 애니메이션 + 화면 암전"),
        ("PAYOFF", "그리고 전달 방식이 미숙했고 생각이 짧았다며 사과했습니다.", "사과문 느낌 카드, 핵심 문구만 짧게 표시"),
        ("REVEAL", "여기서 논란이 일단락되는 듯했는데요.", "BGM 다운 구간 + 0.4초 블랙 프레임"),
        ("REVEAL", "문제는 그다음이었습니다.", "'그다음' 한 단어를 크게 띄우고 빠른 HIT 전환"),
        ("REACTION", f"비난이 이번 일과 직접 관련 없는 {secondary}의 SNS까지 번졌습니다.", f"{secondary} 이름 + SNS 피드 형태 그래픽, 댓글은 흐림 처리"),
        ("REACTION", f"{secondary}{topic_particle(secondary)} 단지 {primary}의 가족이라는 이유로 공격받는 상황이 된 겁니다.", "두 인물 이름 사이 화살표 → 빨간 X로 '연좌 비난' 차단 표현"),
        ("REACTION", "사건과 무관한 과거 활동까지 끌어온 비난도 이어졌다고 전해졌습니다.", "과거/현재 카드가 섞이는 효과 후 '관련 없음' 스탬프"),
        ("PAYOFF", f"{primary}에 대한 비판과 {secondary}에게 향한 공격은 구분해서 볼 필요가 있습니다.", "좌우 분할: '당사자 비판' vs '가족 공격' 비교 카드"),
        ("QUESTION", "여러분은 어디까지가 정당한 비판이라고 보시나요?", "두 선택지 카드 + 댓글 아이콘으로 종료"),
    ]

    target = 12 if duration <= 30 else 14 if duration <= 35 else 16 if duration <= 40 else 18 if duration <= 45 else 20
    # For longer durations add context beats without slowing any single visual beat.
    extras = [
        ("SETUP", f"{primary}는 사고 상황을 알리려는 취지로 영상을 올렸지만, 공개 방식 자체가 쟁점이 됐습니다.", "의도/방식 2분할 카드"),
        ("REACTION", "온라인 반응이 커지면서 사고 자체보다 공개 방식에 대한 논쟁이 중심이 됐습니다.", "사고 → 공개 방식으로 초점이 이동하는 화살표 그래픽"),
        ("PAYOFF", "당사자가 사과한 뒤에도 비난이 다른 사람에게 이동하면서 또 다른 문제가 생겼습니다.", "사과 카드에서 SNS 댓글 카드로 빠르게 슬라이드"),
        ("QUESTION", "비판의 대상과 범위를 구분해야 한다는 지적이 나오는 이유입니다.", "대상/범위 키워드 체크 그래픽"),
    ]
    if target > len(lines):
        # Insert extra beats before the final question.
        needed = target - len(lines)
        lines = lines[:-1] + extras[:needed] + [lines[-1]]
    elif target < len(lines):
        # Preserve all crucial beats while trimming secondary reaction beats first.
        removable = {13, 6, 2, 9}
        kept = [x for i, x in enumerate(lines) if i not in removable]
        lines = kept[:target-1] + [lines[-1]] if len(kept) >= target else lines[:target-1] + [lines[-1]]

    keywords = _unique([primary, secondary, "KTX", "CCTV", "SNS", "사과", "논란"])
    scenes: list[dict[str, Any]] = []
    for role, narration, visual in lines:
        caption = _short_caption(narration, 18 if role == "HOOK" else 22)
        scenes.append({
            "caption": caption,
            "emphasis": _pick_emphasis(caption),
            "narration": narration,
            "stock_keywords": " ".join(keywords[:5]),
            "visual_note": visual,
            "role": role,
        })

    narration = " ".join(x[1] for x in lines)
    safe_title = f"{primary} 논란, 왜 {secondary}에게까지 번졌나" if secondary != "가족" else f"{primary} 논란, 왜 가족에게까지 번졌나"
    facts = [
        f"기사 제목: {title}",
        f"핵심 인물: {primary}" + (f", {secondary}" if secondary != "가족" else ""),
    ]
    for label, fact in (("사고", origin or incident), ("영상 공개", upload), ("비판", criticism or exposure), ("사과", apology), ("2차 피해", spillover or unrelated)):
        if fact:
            facts.append(f"{label}: {_short_caption(fact, 70)}")

    return {
        "video_title": safe_title,
        "hook": hook,
        "narration": narration,
        "description": f"공개 기사에 확인된 사실을 바탕으로 사건 흐름을 재구성한 쇼츠입니다.\n\n참고 출처: {article.publisher or article.url}\n{article.url}",
        "hashtags": ["#쇼츠", "#연예뉴스", f"#{primary}"] + ([f"#{secondary}"] if secondary != "가족" else []),
        "core_facts": facts,
        "originality_overlap_words": _longest_shared_phrase_words(article.text, narration),
        "scenes": scenes,
    }

def generate_zero_key_plan(article: ArticleData, duration: int, category: str) -> dict[str, Any]:
    """외부 생성형 AI 없이 제목·핵심어·수치만 구조화해 새 대본을 만든다."""
    if category in {"연예", "아이돌", "배우", "예능", "드라마·영화"}:
        return _v40_entertainment_plan(article, duration, category)
    title = _clean_article_title(article.title)
    body = re.sub(r"\s+", " ", article.text or "").strip()
    keywords = _top_keywords(body, title, 8)
    numbers = _extract_numbers(body, 4)
    subject = " · ".join(keywords[:2]) if keywords else _short_caption(title, 28)
    secondary = " · ".join(keywords[2:5]) if len(keywords) >= 3 else "후속 발표와 공식 확인"
    hooks = {
        "연예": f"{subject}, 지금 왜 다시 주목받고 있을까요?",
        "경제·주식": f"{subject}, 숫자보다 먼저 봐야 할 포인트가 있습니다.",
        "제품·리뷰": f"{subject}, 광고보다 먼저 확인할 부분이 있습니다.",
        "생활정보": f"{subject}, 나에게도 해당되는지 핵심만 짚어보겠습니다.",
    }
    hook = hooks.get(category, f"{subject}, 지금 핵심 쟁점은 무엇일까요?")
    lines = [hook, f"먼저 확인되는 핵심은 {subject}와 관련된 새 소식이 전해졌다는 점입니다."]
    if numbers:
        lines.append(f"기사에서 눈에 띄는 구체적인 수치는 {'·'.join(numbers[:3])}입니다.")
    lines.extend([
        f"내용을 정리하면 {secondary}가 이번 소식의 중심 맥락으로 보입니다.",
        _category_context(category),
        "아직 확인되지 않은 추측은 사실처럼 단정하지 않고, 공식 발표와 후속 내용을 지켜보는 편이 안전합니다.",
    ])
    if duration >= 45:
        lines.append("결국 중요한 건 자극적인 제목보다 실제로 확인된 범위와 앞으로 달라질 가능성을 함께 보는 것입니다.")
    if duration >= 60:
        lines.append("새로운 입장이나 정정 보도가 나오면 기존 해석도 달라질 수 있다는 점을 기억해야 합니다.")
    lines.append("여러분은 이 소식의 핵심을 어떻게 보시나요?")
    target_scenes = 6 if duration <= 30 else 7 if duration <= 45 else 8
    if len(lines) > target_scenes:
        lines = [lines[0], *lines[1:-1][:target_scenes-2], lines[-1]]
    scenes = []
    for idx, line in enumerate(lines):
        caption_text = "핵심만 빠르게 정리합니다" if idx == 0 else line
        scenes.append({
            "caption": _short_caption(caption_text, 26),
            "emphasis": _pick_emphasis(caption_text),
            "narration": line,
            "stock_keywords": " ".join(keywords[:3]),
            "visual_note": f"{category} 주제를 상징하는 독창적인 에디토리얼 그래픽",
        })
    facts = [f"기사 제목: {title}"]
    if keywords:
        facts.append(f"핵심어: {', '.join(keywords[:5])}")
    if numbers:
        facts.append(f"주요 수치: {', '.join(numbers)}")
    narration = " ".join(lines)
    return {
        "video_title": _short_caption(title, 48),
        "hook": hook,
        "narration": narration,
        "description": f"기사에서 확인 가능한 사실과 핵심어를 바탕으로 새롭게 구성한 해설입니다.\n\n참고 출처: {article.publisher or article.url}\n{article.url}",
        "hashtags": ["#쇼츠", "#이슈정리", f"#{category.replace(' ', '').replace('·', '')}"],
        "core_facts": facts[:3],
        "originality_overlap_words": _longest_shared_phrase_words(article.text, narration),
        "scenes": scenes,
    }


def generate_local_plan(article: ArticleData, duration: int, category: str) -> dict[str, Any]:
    return generate_zero_key_plan(article, duration, category)


def plan_from_edited_narration(base_plan: dict[str, Any], narration: str, article: ArticleData, duration: int) -> dict[str, Any]:
    clean = re.sub(r"\s+", " ", narration or "").strip()
    if not clean:
        raise ShortsMakerError("수정 대본이 비어 있습니다.")
    parts = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", clean) if len(part.strip()) >= 5]
    if len(parts) < 3:
        words = clean.split()
        chunk_size = max(8, math.ceil(len(words) / 6))
        parts = [" ".join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)]
    revised = dict(base_plan)
    revised["narration"] = clean
    revised["hook"] = parts[0]
    revised["scenes"] = [{
        "caption": _short_caption(part, 28),
        "emphasis": _pick_emphasis(part),
        "narration": part,
        "stock_keywords": "",
        "visual_note": "대본 내용에 맞춘 독창적인 에디토리얼 그래픽",
    } for part in parts[:10]]
    revised["originality_overlap_words"] = _longest_shared_phrase_words(article.text, clean)
    return normalize_plan(revised, article, duration)


def _short_caption(text: str, limit: int) -> str:
    text = re.sub(r"[\"'“”‘’]", "", re.sub(r"\s+", " ", text)).strip()
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    for sep in (" ", ",", "·", "은", "는", "이", "가"):
        idx = clipped.rfind(sep)
        if idx >= limit // 2:
            clipped = clipped[:idx]
            break
    return clipped.strip() + "…"


def normalize_plan(plan: dict[str, Any], article: ArticleData, duration: int) -> dict[str, Any]:
    scenes_raw = plan.get("scenes") or []
    scenes: list[dict[str, str]] = []
    for scene in scenes_raw:
        if not isinstance(scene, dict):
            continue
        narration = re.sub(r"\s+", " ", str(scene.get("narration") or "")).strip()
        caption = re.sub(r"\s+", " ", str(scene.get("caption") or "")).strip()
        if not narration and caption:
            narration = caption
        if not caption and narration:
            caption = _short_caption(narration, 22)
        if narration:
            emphasis_raw = scene.get("emphasis") or []
            if isinstance(emphasis_raw, str):
                emphasis = [part.strip() for part in re.split(r"[,/|]", emphasis_raw) if part.strip()]
            else:
                emphasis = [str(part).strip() for part in emphasis_raw if str(part).strip()]
            if not emphasis:
                emphasis = _pick_emphasis(caption or narration)
            scenes.append(
                {
                    "caption": caption,
                    "emphasis": emphasis[:2],
                    "narration": narration,
                    "stock_keywords": str(scene.get("stock_keywords") or "").strip(),
                    "visual_note": str(scene.get("visual_note") or "").strip(),
                    "role": str(scene.get("role") or "").strip(),
                }
            )
    if len(scenes) < 3:
        fallback = generate_local_plan(article, duration, "뉴스")
        scenes = fallback["scenes"]

    narration = " ".join(scene["narration"] for scene in scenes).strip()
    if not narration:
        narration = str(plan.get("narration") or article.title)

    hashtags = plan.get("hashtags") or ["#쇼츠", "#뉴스"]
    if isinstance(hashtags, str):
        hashtags = re.findall(r"#\S+", hashtags) or [hashtags]

    return {
        "video_title": str(plan.get("video_title") or article.title).strip(),
        "hook": str(plan.get("hook") or scenes[0]["narration"]).strip(),
        "narration": narration,
        "description": str(plan.get("description") or f"원문 출처: {article.url}").strip(),
        "hashtags": [str(tag).strip() for tag in hashtags if str(tag).strip()],
        "core_facts": [str(x).strip() for x in (plan.get("core_facts") or []) if str(x).strip()][:3],
        "originality_overlap_words": int(plan.get("originality_overlap_words") or 0),
        "scenes": scenes[:16],
    }


def _run_async(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def synthesize_edge_tts(text: str, voice: str, rate: str, output_path: Path) -> str:
    if edge_tts is None:
        raise ShortsMakerError("edge-tts 모듈이 설치되지 않았습니다.")

    async def _save(selected_voice: str) -> None:
        communicate = edge_tts.Communicate(text=text, voice=selected_voice, rate=rate)
        await communicate.save(str(output_path))

    candidates = _unique([voice, "ko-KR-SunHiNeural", "ko-KR-InJoonNeural"])
    errors: list[str] = []
    for candidate in candidates:
        try:
            _run_async(_save(candidate))
            if output_path.exists() and output_path.stat().st_size > 1000:
                return candidate
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    raise ShortsMakerError("AI 성우 생성 실패: " + " / ".join(errors))


def ffprobe_duration(path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    try:
        duration = float(result.stdout.strip())
        if math.isfinite(duration) and duration > 0:
            return duration
    except Exception:
        pass
    raise ShortsMakerError("생성된 음성 길이를 확인하지 못했습니다. ffmpeg 설치 상태를 확인해주세요.")


def _download_image(url: str, output_path: Path) -> bool:
    try:
        response = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": USER_AGENT, "Referer": urllib.parse.urljoin(url, "/")},
        )
        response.raise_for_status()
        if len(response.content) < 3000:
            return False
        with Image.open(io.BytesIO(response.content)) as image:
            image.convert("RGB").save(output_path, format="JPEG", quality=92)
        return True
    except Exception:
        return False


def search_pexels_image(query: str, api_key: str) -> str:
    if not api_key or not query:
        return ""
    try:
        response = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": query, "per_page": 3, "orientation": "portrait"},
            timeout=20,
        )
        response.raise_for_status()
        photos = response.json().get("photos") or []
        if photos:
            src = photos[0].get("src") or {}
            return src.get("portrait") or src.get("large2x") or src.get("large") or ""
    except Exception:
        return ""
    return ""



def find_font(bold: bool = True) -> str:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumSquareB.ttf" if bold else "/usr/share/fonts/truetype/nanum/NanumSquareR.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf" if bold else "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return candidates[-2]


def _fit_cover(image: Image.Image, size: tuple[int, int], center_y: float = 0.45) -> Image.Image:
    return ImageOps.fit(
        image.convert("RGB"),
        size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, min(max(center_y, 0.0), 1.0)),
    )


def _fit_contain(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    result = image.convert("RGB").copy()
    result.thumbnail(size, Image.Resampling.LANCZOS)
    return result


def _gradient_background(width: int, height: int, index: int) -> Image.Image:
    presets = [
        ((22, 28, 52), (81, 54, 158)),
        ((10, 50, 73), (22, 126, 116)),
        ((66, 24, 65), (181, 53, 92)),
        ((35, 39, 57), (128, 83, 47)),
        ((19, 30, 49), (42, 98, 155)),
    ]
    start, end = presets[index % len(presets)]
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(height - 1, 1)
        color = tuple(int(start[i] * (1 - t) + end[i] * t) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    return image


def _paste_rounded(base: Image.Image, foreground: Image.Image, xy: tuple[int, int], radius: int) -> None:
    mask = Image.new("L", foreground.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, foreground.width, foreground.height), radius=radius, fill=255)
    shadow = Image.new("RGBA", (foreground.width + radius * 2, foreground.height + radius * 2), (0, 0, 0, 0))
    shadow_mask = Image.new("L", shadow.size, 0)
    ImageDraw.Draw(shadow_mask).rounded_rectangle(
        (radius, radius, radius + foreground.width, radius + foreground.height),
        radius=radius,
        fill=145,
    )
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(max(8, radius // 2)))
    shadow_layer = Image.new("RGBA", shadow.size, (0, 0, 0, 0))
    shadow_layer.putalpha(shadow_mask)
    base.alpha_composite(shadow_layer, (xy[0] - radius, xy[1] - radius))
    base.paste(foreground.convert("RGBA"), xy, mask)


def _prepare_background(
    image_path: Path | None,
    size: tuple[int, int],
    index: int,
    mode: str,
    template: str,
) -> Image.Image:
    width, height = size
    if not image_path or not image_path.exists():
        return _gradient_background(width, height, index).convert("RGBA")

    with Image.open(image_path) as opened:
        original = opened.convert("RGB")

    canvas_ratio = width / height
    image_ratio = original.width / max(original.height, 1)
    mismatch = max(image_ratio / canvas_ratio, canvas_ratio / max(image_ratio, 0.001))
    selected_mode = mode
    if mode == "auto":
        selected_mode = "blur" if mismatch > 1.48 else "cover"

    if selected_mode == "cover":
        result = _fit_cover(original, size, 0.42).convert("RGBA")
        result = ImageEnhance.Color(result.convert("RGB")).enhance(1.04).convert("RGBA")
        return result

    blurred = _fit_cover(original, size, 0.45).filter(ImageFilter.GaussianBlur(radius=max(18, width // 30)))
    blurred = ImageEnhance.Brightness(blurred).enhance(0.56)
    blurred = ImageEnhance.Color(blurred).enhance(0.84).convert("RGBA")

    if template == "card":
        max_w = int(width * 0.84)
        max_h = int(height * 0.53)
        top = int(height * 0.22)
    else:
        max_w = int(width * 0.91)
        max_h = int(height * 0.62)
        top = int(height * 0.20)
    foreground = _fit_contain(original, (max_w, max_h))
    x = (width - foreground.width) // 2
    y = top + max(0, (max_h - foreground.height) // 2)
    _paste_rounded(blurred, foreground, (x, y), radius=max(18, width // 28))
    return blurred


def _overlay_vertical_gradient(
    base: Image.Image,
    top_alpha: int = 135,
    bottom_alpha: int = 210,
    middle_alpha: int = 15,
) -> Image.Image:
    width, height = base.size
    strip = Image.new("RGBA", (1, height), (0, 0, 0, 0))
    strip_pixels = strip.load()
    for y in range(height):
        pos = y / max(height - 1, 1)
        if pos < 0.34:
            t = pos / 0.34
            alpha = int(top_alpha * (1 - t) + middle_alpha * t)
        elif pos > 0.53:
            t = (pos - 0.53) / 0.47
            alpha = int(middle_alpha * (1 - t) + bottom_alpha * t)
        else:
            alpha = middle_alpha
        strip_pixels[0, y] = (0, 0, 0, alpha)
    layer = strip.resize((width, height), Image.Resampling.NEAREST)
    return Image.alpha_composite(base.convert("RGBA"), layer)


def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, stroke: int = 0) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text or " ", font=font, stroke_width=stroke)
    return box[2] - box[0], box[3] - box[1]


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int = 2,
) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return [""]
    words = clean.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if _measure(draw, candidate, font, 2)[0] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = word
        else:
            chunk = ""
            for char in word:
                test = chunk + char
                if _measure(draw, test, font, 2)[0] <= max_width:
                    chunk = test
                else:
                    if chunk:
                        lines.append(chunk)
                    chunk = char
            current = chunk
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    joined = " ".join(lines)
    if len(joined.replace(" ", "")) < len(clean.replace(" ", "")) and lines:
        last = lines[-1]
        while last and _measure(draw, last + "…", font, 2)[0] > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _fit_font_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    start_size: int,
    min_size: int,
    max_width: int,
    max_lines: int = 2,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    size = start_size
    while size >= min_size:
        font = ImageFont.truetype(font_path, size)
        lines = _wrap_text(draw, text, font, max_width, max_lines)
        if len(lines) <= max_lines and all(_measure(draw, line, font, 3)[0] <= max_width for line in lines):
            return font, lines
        size -= 2
    font = ImageFont.truetype(font_path, min_size)
    return font, _wrap_text(draw, text, font, max_width, max_lines)


def _matches_emphasis(token: str, emphasis: list[str]) -> bool:
    clean = re.sub(r"[^0-9A-Za-z가-힣]", "", token)
    return any(word and (word in clean or clean in word) for word in emphasis)


def _draw_centered_line(
    draw: ImageDraw.ImageDraw,
    y: int,
    line: str,
    font: ImageFont.FreeTypeFont,
    width: int,
    fill: tuple[int, int, int],
    accent: tuple[int, int, int],
    emphasis: list[str],
    stroke_width: int,
    max_width: int,
) -> int:
    words = line.split(" ") if " " in line else [line]
    space_w = _measure(draw, " ", font, stroke_width)[0]
    widths = [_measure(draw, word, font, stroke_width)[0] for word in words]
    total_w = sum(widths) + space_w * max(0, len(words) - 1)
    x = max((width - total_w) // 2, (width - max_width) // 2)
    for idx, word in enumerate(words):
        color = accent if _matches_emphasis(word, emphasis) else fill
        draw.text(
            (x, y),
            word,
            font=font,
            fill=color,
            stroke_width=stroke_width,
            stroke_fill=(5, 7, 12, 220),
        )
        x += widths[idx] + (space_w if idx < len(words) - 1 else 0)
    return _measure(draw, line, font, stroke_width)[1]



def _scene_theme(text: str) -> str:
    value = (text or "").lower()
    if re.search(r"가수|배우|아이돌|콘서트|무대|연예|방송|드라마|영화", value): return "entertainment"
    if re.search(r"주식|경제|매출|금리|원|달러|투자|가격|분양|청약|아파트", value): return "economy"
    if re.search(r"법원|논란|수사|재판|정책|정부|국회|규정", value): return "issue"
    if re.search(r"휴대폰|댓글|sns|온라인|인터넷|앱", value): return "social"
    if re.search(r"항공|공항|여행|해외|출국|입국", value): return "travel"
    if re.search(r"제품|리뷰|출시|구매|기기|서비스", value): return "product"
    return "general"


def _draw_editorial_art(canvas: Image.Image, text: str, index: int, accent: tuple[int, int, int]) -> Image.Image:
    base = canvas.convert("RGBA")
    draw = ImageDraw.Draw(base, "RGBA")
    width, height = base.size
    scale = width / 720
    cx, cy = width // 2, int(height * 0.43)
    theme = _scene_theme(text)
    for radius, alpha in ((210, 25), (150, 38), (90, 55)):
        r = int(radius * scale)
        draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill=(*accent, alpha))
    for n in range(9):
        x = int(width * (0.08 + ((n * 0.137 + index * 0.071) % 0.84)))
        y = int(height * (0.16 + ((n * 0.173 + index * 0.053) % 0.46)))
        rr = int((7 + n % 3 * 5) * scale)
        draw.ellipse((x-rr,y-rr,x+rr,y+rr), fill=(255,255,255,30))
    if theme == "entertainment":
        r=int(70*scale)
        draw.ellipse((cx-r,cy-r-int(35*scale),cx+r,cy+r-int(35*scale)), fill=(248,249,255,225), outline=(*accent,255), width=max(3,int(5*scale)))
        draw.rounded_rectangle((cx-int(34*scale),cy+int(35*scale),cx+int(34*scale),cy+int(170*scale)), radius=int(24*scale), fill=(248,249,255,225))
        draw.line((cx,cy+int(170*scale),cx,cy+int(245*scale)), fill=(*accent,240), width=max(5,int(10*scale)))
        draw.line((cx-int(80*scale),cy+int(245*scale),cx+int(80*scale),cy+int(245*scale)), fill=(*accent,240), width=max(5,int(10*scale)))
    elif theme == "economy":
        left,bottom=int(width*.22),int(height*.62)
        for i,h in enumerate((110,170,235,315)):
            x=left+i*int(92*scale)
            draw.rounded_rectangle((x,bottom-int(h*scale),x+int(58*scale),bottom), radius=int(12*scale), fill=(*accent,170+i*18))
        pts=[(int(width*.20),int(height*.58)),(int(width*.36),int(height*.50)),(int(width*.52),int(height*.53)),(int(width*.70),int(height*.34)),(int(width*.80),int(height*.28))]
        draw.line(pts, fill=(255,255,255,235), width=max(4,int(8*scale)), joint="curve")
    elif theme in {"issue","product"}:
        x1,y1,x2,y2=int(width*.27),int(height*.24),int(width*.73),int(height*.64)
        draw.rounded_rectangle((x1,y1,x2,y2), radius=int(30*scale), fill=(250,250,255,215), outline=(*accent,240), width=max(3,int(5*scale)))
        for i in range(4):
            yy=y1+int((85+i*58)*scale)
            draw.rounded_rectangle((x1+int(55*scale),yy,x2-int(55*scale),yy+int(16*scale)), radius=int(8*scale), fill=(35,38,60,95))
        draw.ellipse((x2-int(120*scale),y2-int(120*scale),x2+int(20*scale),y2+int(20*scale)), fill=(*accent,245))
    elif theme == "social":
        x1,y1,x2,y2=int(width*.30),int(height*.20),int(width*.70),int(height*.68)
        draw.rounded_rectangle((x1,y1,x2,y2), radius=int(52*scale), fill=(20,24,45,210), outline=(255,255,255,190), width=max(3,int(6*scale)))
        draw.rounded_rectangle((x1+int(25*scale),y1+int(55*scale),x2-int(25*scale),y2-int(55*scale)), radius=int(28*scale), fill=(247,248,255,225))
        for i,w in enumerate((230,185,250)):
            yy=y1+int((120+i*95)*scale)
            draw.rounded_rectangle((x1+int(58*scale),yy,x1+int((58+w)*scale),yy+int(48*scale)), radius=int(24*scale), fill=(*accent,155))
    elif theme == "travel":
        draw.arc((int(width*.18),int(height*.20),int(width*.82),int(height*.72)), 205, 338, fill=(255,255,255,180), width=max(3,int(6*scale)))
        pts=[(int(width*.36),int(height*.47)),(int(width*.75),int(height*.31)),(int(width*.61),int(height*.46)),(int(width*.66),int(height*.57)),(int(width*.54),int(height*.50)),(int(width*.44),int(height*.62))]
        draw.polygon(pts, fill=(*accent,235), outline=(255,255,255,160))
    else:
        for i in range(3):
            off=int(i*34*scale)
            draw.rounded_rectangle((int(width*.22)+off,int(height*.25)+off,int(width*.74)+off,int(height*.59)+off), radius=int(36*scale), fill=(255,255,255,45+i*25), outline=(*accent,100+i*35), width=max(2,int(4*scale)))
        draw.ellipse((cx-int(85*scale),cy-int(85*scale),cx+int(85*scale),cy+int(85*scale)), fill=(*accent,210))
        draw.ellipse((cx-int(34*scale),cy-int(34*scale),cx+int(34*scale),cy+int(34*scale)), fill=(255,255,255,235))
    return base

def make_scene_card(
    image_path: Path | None,
    output_path: Path,
    caption: str,
    emphasis: list[str],
    title: str,
    hook: str,
    category: str,
    source: str,
    index: int,
    template: str = "highlight",
    subtitle_style: str = "accent",
    accent: tuple[int, int, int] = (255, 216, 77),
    background_mode: str = "auto",
    show_hook: bool = True,
    show_badge: bool = False,
    size: tuple[int, int] = (720, 1280),
) -> None:
    width, height = size
    scale = width / 720
    margin = int(42 * scale)
    canvas = _prepare_background(image_path, size, index, background_mode, template)
    if image_path is None:
        canvas = _draw_editorial_art(canvas, f"{caption} {title} {hook}", index, accent)
    canvas = _overlay_vertical_gradient(
        canvas,
        top_alpha=170 if template != "card" else 190,
        bottom_alpha=230 if template == "highlight" else 215,
        middle_alpha=16,
    )
    draw = ImageDraw.Draw(canvas)

    font_bold = find_font(True)
    font_regular = find_font(False)
    source_text = _short_caption(f"출처 · {source}" if source else "기사 기반 자체 해설", 54)

    if template == "news":
        title_y = int(62 * scale)
        title_font, title_lines = _fit_font_lines(
            draw,
            _short_caption(title, 54),
            font_bold,
            int(38 * scale),
            int(30 * scale),
            width - margin * 2,
            2,
        )
        for line in title_lines:
            draw.text(
                (margin, title_y),
                line,
                font=title_font,
                fill=(255, 255, 255),
                stroke_width=max(2, int(2 * scale)),
                stroke_fill=(0, 0, 0, 185),
            )
            title_y += int(48 * scale)
        box_top = int(height * 0.72)
        box_bottom = height - int(72 * scale)
        draw.rounded_rectangle(
            (margin, box_top, width - margin, box_bottom),
            radius=int(26 * scale),
            fill=(9, 12, 20, 208),
            outline=(*accent, 185),
            width=max(2, int(3 * scale)),
        )
        caption_y = box_top + int(36 * scale)
        caption_width = width - margin * 2 - int(54 * scale)
    elif template == "card":
        hook_text = hook if show_hook else title
        hook_font, hook_lines = _fit_font_lines(
            draw,
            _short_caption(hook_text, 52),
            font_bold,
            int(40 * scale),
            int(30 * scale),
            width - margin * 2,
            2,
        )
        hook_y = int(116 * scale)
        for line in hook_lines:
            draw.text(
                (margin, hook_y),
                line,
                font=hook_font,
                fill=(255, 255, 255),
                stroke_width=max(2, int(3 * scale)),
                stroke_fill=(0, 0, 0, 195),
            )
            hook_y += int(50 * scale)
        box_top = int(height * 0.73)
        box_bottom = height - int(74 * scale)
        draw.rounded_rectangle(
            (margin, box_top, width - margin, box_bottom),
            radius=int(28 * scale),
            fill=(18, 20, 30, 222),
        )
        draw.rounded_rectangle(
            (margin, box_top, margin + int(10 * scale), box_bottom),
            radius=int(5 * scale),
            fill=(*accent, 255),
        )
        caption_y = box_top + int(34 * scale)
        caption_width = width - margin * 2 - int(66 * scale)
    else:
        if show_hook:
            hook_font, hook_lines = _fit_font_lines(
                draw,
                _short_caption(hook, 52),
                font_bold,
                int(41 * scale),
                int(30 * scale),
                width - margin * 2,
                2,
            )
            hook_y = int(116 * scale)
            for line in hook_lines:
                draw.text(
                    (margin, hook_y),
                    line,
                    font=hook_font,
                    fill=(255, 255, 255),
                    stroke_width=max(2, int(3 * scale)),
                    stroke_fill=(0, 0, 0, 200),
                )
                hook_y += int(50 * scale)
        caption_y = int(height * 0.72)
        caption_width = width - margin * 2 - int(24 * scale)

    caption_font, caption_lines = _fit_font_lines(
        draw,
        caption,
        font_bold,
        int((54 if template == "highlight" else 50) * scale),
        int(38 * scale),
        caption_width,
        2,
    )
    line_h = int(_measure(draw, "가나다", caption_font, 3)[1] * 1.28)
    total_h = line_h * len(caption_lines)

    if template == "highlight":
        caption_y = min(caption_y, height - int(116 * scale) - total_h)
        if subtitle_style == "box":
            pad_y = int(24 * scale)
            draw.rounded_rectangle(
                (
                    margin - int(12 * scale),
                    caption_y - pad_y,
                    width - margin + int(12 * scale),
                    caption_y + total_h + pad_y,
                ),
                radius=int(26 * scale),
                fill=(8, 10, 17, 196),
            )
    else:
        caption_y = min(caption_y, height - int(118 * scale) - total_h)

    if subtitle_style == "clean":
        line_emphasis: list[str] = []
    else:
        line_emphasis = emphasis or _pick_emphasis(caption)

    y = caption_y
    for line in caption_lines:
        _draw_centered_line(
            draw,
            y,
            line,
            caption_font,
            width,
            (255, 255, 255),
            accent,
            line_emphasis,
            max(3, int(4 * scale)),
            caption_width,
        )
        y += line_h

    source_font = ImageFont.truetype(font_regular, max(16, int(18 * scale)))
    source_y = height - int(49 * scale)
    draw.text(
        (margin, source_y),
        source_text,
        font=source_font,
        fill=(224, 226, 234),
        stroke_width=max(1, int(1 * scale)),
        stroke_fill=(0, 0, 0, 145),
    )
    draw.text(
        (width - margin, source_y),
        f"{index + 1:02}",
        anchor="ra",
        font=source_font,
        fill=accent,
        stroke_width=max(1, int(1 * scale)),
        stroke_fill=(0, 0, 0, 145),
    )
    canvas.convert("RGB").save(output_path, quality=96)


def allocate_durations(scenes: list[dict[str, str]], total_duration: float) -> list[float]:
    weights = [max(12, len(scene.get("narration", ""))) for scene in scenes]
    total_weight = sum(weights) or 1
    raw = [max(2.0, total_duration * weight / total_weight) for weight in weights]
    scale = total_duration / sum(raw)
    durations = [round(value * scale, 3) for value in raw]
    durations[-1] += round(total_duration - sum(durations), 3)
    return durations


def make_srt(scenes: list[dict[str, str]], durations: list[float]) -> str:
    def stamp(seconds: float) -> str:
        millis = int(round(seconds * 1000))
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        secs, millis = divmod(millis, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

    entries: list[str] = []
    cursor = 0.0
    for idx, (scene, duration) in enumerate(zip(scenes, durations), 1):
        start = cursor
        cursor += duration
        entries.append(f"{idx}\n{stamp(start)} --> {stamp(cursor)}\n{scene['caption']}\n")
    return "\n".join(entries)


def _run(command: list[str], description: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout)[-1800:]
        raise ShortsMakerError(f"{description} 실패:\n{detail}")



def _voice_filter(preset: str) -> str:
    filters = {
        "clean": (
            "highpass=f=80,lowpass=f=12500,afftdn=nf=-25,"
            "equalizer=f=180:t=q:w=1:g=-2,equalizer=f=3200:t=q:w=1:g=2.5,"
            "acompressor=threshold=-18dB:ratio=2.4:attack=12:release=180:makeup=2,"
            "loudnorm=I=-16:TP=-1.5:LRA=8"
        ),
        "bright": (
            "highpass=f=90,lowpass=f=13000,afftdn=nf=-25,"
            "equalizer=f=220:t=q:w=1:g=-2.5,equalizer=f=3500:t=q:w=1:g=4,"
            "equalizer=f=7000:t=q:w=1:g=1.5,asetrate=48000*1.025,aresample=48000,atempo=0.97561,"
            "acompressor=threshold=-19dB:ratio=2.6:attack=10:release=150:makeup=2.5,"
            "loudnorm=I=-15.5:TP=-1.2:LRA=7"
        ),
        "fun": (
            "highpass=f=100,lowpass=f=13500,afftdn=nf=-24,"
            "equalizer=f=250:t=q:w=1:g=-2,equalizer=f=4200:t=q:w=1:g=4.5,"
            "asetrate=48000*1.06,aresample=48000,atempo=0.9717,"
            "acompressor=threshold=-20dB:ratio=3:attack=8:release=120:makeup=3,"
            "loudnorm=I=-15:TP=-1:LRA=6"
        ),
        "warm": (
            "highpass=f=70,lowpass=f=11000,afftdn=nf=-25,"
            "equalizer=f=140:t=q:w=1:g=2,equalizer=f=2800:t=q:w=1:g=1,"
            "asetrate=48000*0.975,aresample=48000,atempo=1.02564,"
            "acompressor=threshold=-18dB:ratio=2.3:attack=15:release=220:makeup=2,"
            "loudnorm=I=-16:TP=-1.5:LRA=8"
        ),
    }
    return filters.get(preset, filters["clean"])


def process_user_voice(input_path: Path, preset: str, output_path: Path) -> None:
    if not input_path.exists() or input_path.stat().st_size < 1000:
        raise ShortsMakerError("내 목소리 녹음 파일이 비어 있습니다.")
    _run(
        [
            "ffmpeg", "-y", "-i", str(input_path),
            "-af", _voice_filter(preset),
            "-c:a", "libmp3lame", "-b:a", "160k", str(output_path),
        ],
        "내 목소리 보정",
    )


def _music_asset_path(track_label: str) -> Path | None:
    filename = MUSIC_TRACK_OPTIONS.get(track_label)
    if not filename:
        return None
    path = Path(__file__).resolve().parent / "assets" / "music" / filename
    return path if path.exists() else None


def _select_music_label(category: str, requested: str) -> str:
    if requested and requested != "자동 추천":
        return requested
    return MUSIC_AUTO_BY_CATEGORY.get(category, "뉴스·이슈 펄스")


def _copy_or_prepare_music(source: Path, output_path: Path) -> None:
    _run(
        [
            "ffmpeg", "-y", "-i", str(source),
            "-af", "highpass=f=45,lowpass=f=15000,loudnorm=I=-21:TP=-2:LRA=9",
            "-c:a", "libmp3lame", "-b:a", "160k", str(output_path),
        ],
        "배경음악 준비",
    )


def render_video(
    article: ArticleData,
    plan: dict[str, Any],
    voice: str,
    rate: str,
    pexels_key: str = "",
    workdir: Path | None = None,
    progress: Callable[[str], None] | None = None,
    template: str = "highlight",
    subtitle_style: str = "accent",
    accent: tuple[int, int, int] = (255, 216, 77),
    background_mode: str = "auto",
    show_hook: bool = True,
    show_badge: bool = False,
    category: str = "뉴스",
    resolution: tuple[int, int] = (720, 1280),
    narration_audio: Path | None = None,
    voice_preset: str = "clean",
    music_mode: str = "auto",
    music_track: str = "자동 추천",
    music_volume: float = 0.11,
    custom_music: Path | None = None,
    approved_visuals: list[Path] | None = None,
) -> dict[str, Path]:
    progress = progress or (lambda _: None)
    temp_root = Path(workdir or tempfile.mkdtemp(prefix="shortsmaker_"))
    temp_root.mkdir(parents=True, exist_ok=True)

    scenes: list[dict[str, str]] = plan["scenes"]
    narration = plan["narration"]
    audio_path = temp_root / "narration.mp3"
    if narration_audio is not None:
        progress("업로드한 내 목소리를 깨끗하고 듣기 좋게 보정하고 있습니다.")
        process_user_voice(narration_audio, voice_preset, audio_path)
        actual_voice = f"내 목소리 보정 · {voice_preset}"
        voice_mode = "내 목소리"
    else:
        progress("AI 성우 나레이션을 생성하고 있습니다.")
        actual_voice = synthesize_edge_tts(narration, voice, rate, audio_path)
        voice_mode = "AI 성우"

    audio_duration = ffprobe_duration(audio_path)
    durations = allocate_durations(scenes, max(audio_duration + 0.35, 3.0 * len(scenes)))

    progress("저작권 안전 스톡 이미지와 자체 그래픽으로 장면을 구성하고 있습니다.")
    scene_clips: list[Path] = []
    used_visuals: list[str] = []
    for idx, (scene, scene_duration) in enumerate(zip(scenes, durations)):
        image_path: Path | None = None
        safe_visuals = approved_visuals or []
        if idx < len(safe_visuals) and safe_visuals[idx].exists():
            image_path = safe_visuals[idx]
            used_visuals.append(f"장면 {idx + 1}: 권리 검토 통과 사용자 자료 · {image_path.name}")
        else:
            pexels_url = search_pexels_image(scene.get("stock_keywords", ""), pexels_key)
            if pexels_url:
                candidate = temp_root / f"pexels_{idx:02}.jpg"
                if _download_image(pexels_url, candidate):
                    image_path = candidate
                    used_visuals.append(f"장면 {idx + 1}: Pexels 스톡 이미지 · {scene.get('stock_keywords','')}")
        if image_path is None:
            used_visuals.append(f"장면 {idx + 1}: 승인된 실제 자료 없음 · 자체 그래픽 fallback")

        card_path = temp_root / f"scene_{idx:02}.jpg"
        make_scene_card(
            image_path=image_path,
            output_path=card_path,
            caption=scene["caption"],
            emphasis=scene.get("emphasis") or [],
            title=plan["video_title"],
            hook=plan.get("hook") or plan["video_title"],
            category="",
            source=article.publisher or urllib.parse.urlparse(article.url).netloc,
            index=idx,
            template=template,
            subtitle_style=subtitle_style,
            accent=accent,
            background_mode=background_mode,
            show_hook=show_hook,
            show_badge=False,
            size=resolution,
        )
        clip_path = temp_root / f"clip_{idx:02}.mp4"
        _run(
            [
                "ffmpeg", "-y", "-loop", "1", "-i", str(card_path),
                "-t", f"{scene_duration:.3f}",
                "-vf", (
                    f"scale={resolution[0]}:{resolution[1]},"
                    f"zoompan=z='min(zoom+0.00035,1.045)':"
                    f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d=1:s={resolution[0]}x{resolution[1]}:fps=30,format=yuv420p"
                ),
                "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an", str(clip_path),
            ],
            f"{idx + 1}번 장면 렌더링",
        )
        scene_clips.append(clip_path)

    progress("장면을 하나의 세로 영상으로 합치고 있습니다.")
    concat_file = temp_root / "concat.txt"
    concat_file.write_text("\n".join(f"file '{clip.as_posix()}'" for clip in scene_clips), encoding="utf-8")
    silent_video = temp_root / "silent.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(silent_video)], "장면 합치기")

    selected_music_label = "사용 안 함"
    prepared_music: Path | None = None
    music_disclosure = "아니오"
    if music_mode != "none":
        if music_mode == "upload" and custom_music is not None:
            selected_music_label = "사용자가 업로드한 YouTube 오디오 라이브러리/보유 음원"
            music_source = custom_music
            music_disclosure = "음원 성격에 따라 확인"
        else:
            selected_music_label = _select_music_label(category, music_track)
            music_source = _music_asset_path(selected_music_label)
            music_disclosure = "예 · 프로그램 생성형 오리지널 BGM"
        if music_source is not None and music_source.exists():
            prepared_music = temp_root / "background_music.mp3"
            _copy_or_prepare_music(music_source, prepared_music)

    final_video = temp_root / "final_short.mp4"
    if prepared_music is not None:
        fade_out = max(0.0, audio_duration - 1.2)
        volume = min(max(float(music_volume), 0.0), 0.35)
        _run(
            [
                "ffmpeg", "-y", "-i", str(silent_video), "-i", str(audio_path),
                "-stream_loop", "-1", "-i", str(prepared_music),
                "-filter_complex",
                (
                    f"[2:a]volume={volume:.4f},atrim=0:{audio_duration:.3f},"
                    f"afade=t=in:st=0:d=0.7,afade=t=out:st={fade_out:.3f}:d=1.2[bg];"
                    "[1:a][bg]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]"
                ),
                "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", "-movflags", "+faststart", str(final_video),
            ],
            "나레이션과 자동 배경음악 합성",
        )
    else:
        _run(
            [
                "ffmpeg", "-y", "-i", str(silent_video), "-i", str(audio_path),
                "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(final_video),
            ],
            "최종 영상 합성",
        )

    srt_path = temp_root / "subtitles.srt"
    srt_path.write_text(make_srt(scenes, durations), encoding="utf-8-sig")
    script_path = temp_root / "vrew_script.txt"
    script_path.write_text(narration, encoding="utf-8-sig")
    metadata_path = temp_root / "youtube_metadata.txt"
    metadata_path.write_text(
        f"제목\n{plan['video_title']}\n\n설명\n{plan['description']}\n\n"
        f"{' '.join(plan['hashtags'])}\n\n참고 출처\n{article.publisher}\n{article.url}\n",
        encoding="utf-8-sig",
    )
    naver_metadata_path = temp_root / "naver_clip_metadata.txt"
    naver_tags = list(plan.get("hashtags") or [])[:5]
    naver_metadata_path.write_text(
        f"제목\n{plan['video_title']}\n\n설명\n{plan['description'][:300]}\n\n"
        f"해시태그\n{' '.join(naver_tags)}\n\n참고 출처\n{article.publisher}\n{article.url}\n",
        encoding="utf-8-sig",
    )
    source_path = temp_root / "source.txt"
    source_path.write_text(
        f"기사 제목: {article.title}\n매체: {article.publisher}\nURL: {article.url}\n"
        f"나레이션 방식: {voice_mode}\n사용 성우/프리셋: {actual_voice}\n배경음악: {selected_music_label}\n",
        encoding="utf-8-sig",
    )
    visual_path = temp_root / "visual_sources.txt"
    visual_path.write_text(
        "기사 사진 사용: 없음\n방송 화면 사용: 없음\n" + "\n".join(used_visuals) + "\n",
        encoding="utf-8-sig",
    )
    license_path = temp_root / "music_license.txt"
    license_path.write_text(
        "배경음악 사용 내역\n"
        f"선택 음원: {selected_music_label}\n"
        "내장 음원은 제3자 음원 샘플 없이 프로그램용으로 합성한 오리지널 트랙입니다.\n"
        "사용자가 업로드한 음원은 YouTube 오디오 라이브러리의 라이선스/저작자 표시 조건을 직접 확인해야 합니다.\n",
        encoding="utf-8-sig",
    )
    report_path = temp_root / "copyright_check_report.txt"
    report_path.write_text(
        "쇼츠메이커 CLOUD V4.0 저작권 안전 점검\n\n"
        "[대본]\n기사 원문 직접 낭독: 사용 안 함\n사실 기반 재작성: 적용\n"
        f"원문과 최장 연속 유사 어절: {int(plan.get('originality_overlap_words') or 0)}어절\n"
        "[영상]\n기사 대표 사진: 사용 안 함\n방송 캡처/타 유튜브 영상: 사용 안 함\n"
        "Pexels 또는 자체 생성 그래픽: 사용\n"
        f"[음성]\n나레이션: {voice_mode} / {actual_voice}\n"
        f"[음악]\n{selected_music_label}\n생성형 음악 공개 안내 권장: {music_disclosure}\n\n"
        "주의: 이 보고서는 위험을 줄이기 위한 제작 기록이며 법적 무침해를 보증하지 않습니다.\n",
        encoding="utf-8-sig",
    )
    disclosure_path = temp_root / "ai_disclosure.txt"
    disclosure_path.write_text(
        "YouTube 업로드 시 확인\n"
        f"- 내 목소리 단순 보정: {'예' if narration_audio is not None else '해당 없음'} (일반적인 오디오 보정 수준)\n"
        f"- 프로그램 생성형 BGM 사용: {music_disclosure}\n"
        "- 사실적으로 생성된 실존 인물/사건 영상: 사용 안 함\n"
        "생성형 BGM을 사용했다면 YouTube Studio의 변경/합성 콘텐츠 항목을 확인하세요.\n",
        encoding="utf-8-sig",
    )

    zip_path = temp_root / "shorts_result_v40.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in (
            final_video, audio_path, srt_path, script_path, metadata_path, naver_metadata_path, source_path,
            visual_path, license_path, report_path, disclosure_path,
        ):
            archive.write(path, path.name)

    return {
        "video": final_video,
        "audio": audio_path,
        "srt": srt_path,
        "script": script_path,
        "metadata": metadata_path,
        "naver_metadata": naver_metadata_path,
        "source": source_path,
        "visual_sources": visual_path,
        "music_license": license_path,
        "copyright_report": report_path,
        "ai_disclosure": disclosure_path,
        "zip": zip_path,
    }

# 쇼츠메이커 CLOUD V3.0.1
# Streamlit Cloud ImportError hotfix
# shorts_engine 기능을 이 파일 안에 포함해 버전 불일치를 방지합니다.

import hashlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

import streamlit as st


VERSION = "4.0.0-copyright-safe"
STATE_PLAN = "v40_plan"
STATE_RESULT = "v40_result"

st.set_page_config(
    page_title="쇼츠메이커 CLOUD V4.0 BETA",
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
  <h1>⚡ 쇼츠메이커 V4.0 · ENTERTAINMENT</h1>
  <p>연예뉴스를 30~45초짜리 빠른 쇼츠로 재구성합니다. 후킹 → 전개 → 반전/핵심 → 반응 → 질문까지 리듬을 먼저 설계합니다.</p>
  <div class="pills">
    <span class="pill">API 키 입력 없음</span>
    <span class="pill">개인 PC 서버 불필요</span>
    <span class="pill">연예뉴스 전용 스토리보드</span>
    <span class="pill">내 목소리 보정</span>
    <span class="pill">지루함 자동 감지</span>
  </div>
</div>
<div class="cloudbox"><b>접속 방식</b><br>기존 Streamlit 웹주소 하나만 사용합니다. 집에서는 노트북, 회사에서는 회사 PC, 밖에서는 휴대폰으로 같은 주소를 열면 됩니다. 어느 기기도 서버 역할을 하지 않습니다.</div>
<div class="safebox"><b>V4.0 제작 원칙</b><br>기사 낭독형 영상이 아니라 1~2초 단위의 화면 변화가 가능한 스토리보드를 먼저 만듭니다. 확인되지 않은 루머는 단정하지 않고, 기사·공개 발언에서 확인되는 범위만 사용합니다.</div>
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
                "engine": "V4.0 엔터 스토리보드",
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
    category = st.selectbox("콘텐츠 유형", ["연예", "아이돌", "배우", "예능", "드라마·영화", "국내 이슈", "해외 이슈"])
with c2:
    target_duration = st.selectbox("목표 길이", [30, 35, 40, 45, 60], index=2, format_func=lambda x: f"{x}초")

make_plan = st.button("⚡ V4.0 쇼츠 스토리보드 만들기", type="primary", use_container_width=True)
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
        status.success(f"완료: V4.0 엔터 스토리보드 · {len(plan['scenes'])}개 컷")
        st.session_state[STATE_PLAN] = {
            "article": article,
            "plan": plan,
            "category": category,
            "duration": target_duration,
            "engine": "V4.0 엔터 스토리보드",
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
            file_name="shorts_project_v40.json",
            mime="application/json",
            use_container_width=True,
        )
        st.markdown('<div class="note"><b>기기 간 사용</b><br>새 영상은 어느 기기에서든 바로 만들 수 있습니다. 작업 중인 대본을 다른 기기로 옮길 때는 위 프로젝트 파일을 저장한 뒤 불러오세요.</div>', unsafe_allow_html=True)

    st.markdown('<div class="step">3. V4.0 스토리보드 · 지루함 검사</div>', unsafe_allow_html=True)
    pacing = _v40_pacing_report(plan, duration)
    pc1, pc2, pc3 = st.columns(3)
    pc1.metric("리듬 점수", f"{pacing['score']} / 100")
    pc2.metric("총 컷", f"{len(plan.get('scenes') or [])}컷")
    pc3.metric("평균 컷 길이", f"{pacing['avg_cut']:.1f}초")
    if pacing['warnings']:
        for warning in pacing['warnings']:
            st.warning("⚠️ " + warning)
    else:
        st.success("빠른 쇼츠 기준 리듬 검사 통과")
    for idx, scene in enumerate(plan.get("scenes") or [], start=1):
        role = scene.get("role") or _v40_role(idx-1, len(plan.get("scenes") or []))
        with st.expander(f"CUT {idx:02d} · {role} · {scene.get('caption','')}", expanded=idx <= 3):
            st.write(scene.get("narration", ""))
            st.caption("화면 지시: " + (scene.get("visual_note") or "자료 컷 + 키워드 강조"))
            if scene.get("emphasis"):
                st.caption("강조: " + " / ".join(scene.get("emphasis") or []))

    st.markdown('<div class="step">4. 실제 자료 · 저작권 사전검사</div>', unsafe_allow_html=True)
    st.caption("YouTube Shorts + 네이버 클립 동시 게시를 기준으로 보수적으로 검사합니다. 출처 표기만으로 사용 권한이 생기지는 않습니다.")
    uploaded_visuals = st.file_uploader(
        "관련 실제 사진·이미지 업로드 (승인된 자료만 영상에 사용)",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        key="copyright-visuals",
    )
    asset_audits = []
    for idx, up in enumerate(uploaded_visuals or []):
        with st.expander(f"자료 {idx+1:02d} · {up.name}", expanded=idx < 2):
            c1, c2 = st.columns(2)
            with c1:
                source_type = st.selectbox("자료 유형", COPYRIGHT_SOURCE_OPTIONS, key=f"source-type-{idx}")
            with c2:
                basis = st.selectbox("사용 근거", COPYRIGHT_BASIS_OPTIONS, key=f"rights-basis-{idx}")
            audit = audit_visual_rights(source_type, basis)
            audit.update({"name": up.name, "source_type": source_type, "basis": basis, "upload": up})
            asset_audits.append(audit)
            if audit["grade"] == "GREEN": st.success(f"{audit['label']} · {audit['reason']}")
            elif audit["grade"] == "YELLOW": st.warning(f"{audit['label']} · {audit['reason']} · 자동 렌더링에서 제외")
            else: st.error(f"{audit['label']} · {audit['reason']} · 자동 렌더링에서 제외")
    approved_count = sum(1 for row in asset_audits if row["grade"] == "GREEN")
    if uploaded_visuals:
        st.info(f"업로드 {len(uploaded_visuals)}개 중 렌더링 사용 가능 {approved_count}개")
    else:
        st.info("실제 자료가 없으면 안전한 자체 그래픽 fallback을 사용합니다. 연예 콘텐츠는 사용권이 확인된 실제 인물 자료 업로드를 권장합니다.")

    st.markdown('<div class="step">5. 나레이션 선택</div>', unsafe_allow_html=True)
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

    st.markdown('<div class="step">6. 콘텐츠별 자동 배경음악</div>', unsafe_allow_html=True)
    m1, m2, m3 = st.columns(3)
    with m1:
        music_ui = st.selectbox("음악 방식", ["음악 사용 안 함", "콘텐츠에 맞춰 자동 추천", "내장 오리지널 중 직접 선택", "YouTube 오디오 라이브러리 음원 업로드"])
    with m2:
        selected_music = st.selectbox("내장 음악", list(MUSIC_TRACK_OPTIONS), disabled=music_ui != "내장 오리지널 중 직접 선택")
    with m3:
        music_volume_pct = st.slider("배경음악 크기", 3, 25, 10, 1, format="%d%%", disabled=music_ui == "음악 사용 안 함")
    custom_music_upload = None
    attribution_text = ""
    cross_platform_music_confirmed = False
    if music_ui == "YouTube 오디오 라이브러리 음원 업로드":
        c1, c2 = st.columns(2)
        with c1:
            custom_music_upload = st.file_uploader("다운로드한 음원", type=["mp3", "wav", "m4a", "aac", "ogg"], key="music-upload")
        with c2:
            attribution_text = st.text_input("저작자 표시 문구 (필요한 곡만)", placeholder="Music: 곡명 - 아티스트")
        cross_platform_music_confirmed = st.checkbox("이 음원의 YouTube Shorts와 네이버 클립 양쪽 상업적 사용·재편집 조건을 직접 확인했습니다.")

    st.markdown('<div class="step">7. 영상 디자인</div>', unsafe_allow_html=True)
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
    st.caption("V4.0은 스토리보드 컷 역할에 따라 줌·타이포·비교·반응 카드 등 화면 변화를 우선 배치합니다. 실제 기사/방송 자료 사용 시 권리 확인이 필요합니다.")

    music_audit = music_rights_audit(music_ui, cross_platform_music_confirmed)
    st.markdown("#### 🛡️ 최종 저작권 게이트")
    a1, a2, a3 = st.columns(3)
    a1.metric("승인 시각자료", f"{approved_count}개")
    a2.metric("확인 필요/제외", f"{sum(1 for x in asset_audits if x['grade'] != 'GREEN')}개")
    a3.metric("음원", music_audit["label"])
    if music_audit["grade"] == "YELLOW": st.warning(music_audit["reason"])
    elif music_audit["grade"] == "RED": st.error(music_audit["reason"])
    else: st.success("등록된 사용 자료와 음원 기준으로 사전 저작권 게이트를 통과했습니다. 확인 필요/제외 자료는 영상에 사용하지 않습니다.")

    render = st.button("🎬 저작권 안전 자료만 사용해 쇼츠 완성하기", type="primary", use_container_width=True)
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

        if music_audit["grade"] != "GREEN":
            st.error("음원 권리 확인이 완료되지 않았습니다. 음악 사용 안 함을 선택하거나 양 플랫폼 사용 조건을 확인해주세요.")
            st.stop()

        workdir = Path(tempfile.mkdtemp(prefix="shortsmaker_v40_safe_"))
        approved_visual_paths = []
        for idx, row in enumerate(asset_audits):
            if row["grade"] != "GREEN":
                continue
            suffix = Path(row["name"]).suffix.lower() or ".jpg"
            safe_path = workdir / f"approved_visual_{idx:02d}{suffix}"
            safe_path.write_bytes(row["upload"].getvalue())
            approved_visual_paths.append(safe_path)
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
                approved_visuals=approved_visual_paths,
            )
            if attribution_text.strip():
                with files["music_license"].open("a", encoding="utf-8") as fh:
                    fh.write(f"\n사용자 입력 저작자 표시: {attribution_text.strip()}\n")
            safe_rows = [{k:v for k,v in row.items() if k != "upload"} for row in asset_audits]
            safe_report = build_copyright_report(safe_rows, music_audit, int(plan.get("originality_overlap_words") or 0))
            safe_report_path = workdir / "copyright_preflight_v40.txt"
            safe_report_path.write_text(safe_report, encoding="utf-8-sig")
            with files["copyright_report"].open("a", encoding="utf-8") as fh:
                fh.write("\n\n" + safe_report)
            files["copyright_preflight"] = safe_report_path
            with zipfile.ZipFile(files["zip"], "a", zipfile.ZIP_DEFLATED) as archive:
                archive.write(safe_report_path, safe_report_path.name)
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
        st.download_button("⬇️ 결과 전체 ZIP", result["zip"], "shorts_v40_safe_result.zip", "application/zip", type="primary", use_container_width=True, key=f"zip-{result['key']}")
        d1, d2 = st.columns(2)
        with d1:
            st.download_button("MP4 다운로드", result["video"], "final_short_v40_safe.mp4", "video/mp4", use_container_width=True)
        with d2:
            st.download_button("나레이션 다운로드", result["audio"], "narration_v40.mp3", "audio/mpeg", use_container_width=True)
    with right:
        st.subheader("저작권 안전 점검")
        st.text(result["copyright_report"].decode("utf-8-sig", errors="replace"))
        st.download_button("점검 보고서", result["copyright_report"], "copyright_check_report.txt", "text/plain", use_container_width=True)
        st.download_button("AI 공개 안내", result["ai_disclosure"], "ai_disclosure.txt", "text/plain", use_container_width=True)

st.caption("V4.0 COPYRIGHT SAFE · 확인 필요/사용 제외 자료는 자동 렌더링에서 배제합니다. 자동 검사는 위험을 낮추는 보수적 사전 필터이며 법적 무침해를 보증하지 않습니다.")
