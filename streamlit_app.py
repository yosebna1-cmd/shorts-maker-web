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

TITLE_STYLE_OPTIONS = [
    "강한 후킹형 · 클릭 유도 추천",
    "균형형 · 주목도와 신뢰감",
    "정보형 · 과장 최소화",
]

TITLE_BANNED_WORDS = {
    "충격", "소름", "역대급", "무조건", "100%", "대박", "레전드", "실화냐", "미쳤다",
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



def _title_subject(title: str, keywords: list[str]) -> str:
    """기사 제목의 첫 핵심 구절을 우선 사용해 대상이 어색하게 쪼개지지 않도록 한다."""
    clean_title = _clean_article_title(title)
    clean_title = re.sub(r"^\s*\[[^\]]{1,20}\]\s*", "", clean_title)
    clean_title = re.sub(r"^\s*[【〔(][^】〕)]{1,20}[】〕)]\s*", "", clean_title)
    clean_title = re.sub(r"[\r\n]+", " ", clean_title).strip()

    # 연예 기사 제목은 대개 첫 쉼표/말줄임표 앞에 인물·작품명이 온다.
    first_clause = re.split(r"\s*(?:,|…|\.{2,}|｜|\||:)\s*", clean_title, maxsplit=1)[0]
    first_clause = re.sub(r"^[\"'“”‘’]+|[\"'“”‘’]+$", "", first_clause).strip()
    first_clause = re.sub(r"\s+", " ", first_clause)
    generic_only = {"단독", "공식", "종합", "이슈", "연예", "뉴스", "오늘", "속보"}
    if 2 <= len(first_clause) <= 28 and first_clause not in generic_only:
        return _short_caption(first_clause, 28)

    preferred = [
        word for word in keywords
        if not re.fullmatch(r"\d+(?:[.,]\d+)?%?", word)
        and word not in generic_only
    ]
    if preferred:
        subject = " ".join(preferred[:2])
    else:
        subject = _short_caption(clean_title, 26)
    subject = re.sub(r"\s+", " ", subject).strip(" -|｜·,:;")
    return _short_caption(subject or "오늘의 이슈", 28)


def _impactful_title_numbers(numbers: list[str]) -> list[str]:
    """날짜처럼 클릭 제목에서 의미가 약한 숫자를 빼고 금액·비율·순위 등만 남긴다."""
    result: list[str] = []
    for value in numbers:
        value = str(value).strip()
        if not value:
            continue
        if re.fullmatch(r"\d{4}년(?:\s*\d{1,2}월(?:\s*\d{1,2}일)?)?", value):
            continue
        if re.fullmatch(r"\d{1,2}(?:일|월|년)", value):
            continue
        if re.search(r"%|원|달러|억|천만|백만|만|명|개|건|회|위|배", value):
            result.append(value)
    return _unique(result)[:3]


def _title_score(title: str, subject: str, numbers: list[str], style: str) -> int:
    score = 0
    length = len(title)
    if 17 <= length <= 34:
        score += 25
    elif 13 <= length <= 40:
        score += 14
    else:
        score -= abs(length - 27)
    if subject and subject.split(" · ")[0] in title:
        score += 12
    if any(number in title for number in numbers):
        score += 10
    for phrase in ("주목하는 이유", "핵심은 따로", "결정적 포인트", "놓치면 안 될", "확인된 사실", "진짜 이유", "한 가지"):
        if phrase in title:
            score += 7
    if "?" in title or "…" in title:
        score += 3
    if style.startswith("강한"):
        score += sum(3 for phrase in ("진짜 이유", "핵심은 따로", "결정적", "이것") if phrase in title)
    elif style.startswith("정보형"):
        score += sum(4 for phrase in ("핵심 정리", "확인된 사실", "30초") if phrase in title)
        score -= sum(3 for phrase in ("진짜 이유", "결정적") if phrase in title)
    if any(word in title for word in TITLE_BANNED_WORDS):
        score -= 50
    return score


def generate_attention_titles(
    article: ArticleData,
    category: str,
    keywords: list[str] | None = None,
    numbers: list[str] | None = None,
    style: str = "강한 후킹형 · 클릭 유도 추천",
) -> list[str]:
    """확인된 핵심 대상과 쟁점만 이용해 강하지만 허위가 없는 제목 후보를 만든다."""
    clean_title = _clean_article_title(article.title)
    body = re.sub(r"\s+", " ", article.text or "").strip()
    keywords = keywords or _top_keywords(body, clean_title, 8)
    numbers = numbers or _extract_numbers(body, 4)
    title_numbers = _impactful_title_numbers(numbers)
    subject = _title_subject(clean_title, keywords)
    source_text = f"{clean_title} {body}"

    strong = [
        f"{subject}, 지금 반응이 쏟아지는 이유",
        f"{subject}, 모두가 놓친 핵심은 따로 있었다",
        f"{subject}, 이 소식이 갑자기 커진 이유",
        f"{subject}, 알려진 내용보다 중요한 한 가지",
        f"{subject}, 지금 꼭 확인해야 할 결정적 포인트",
        f"{subject}, 사람들이 가장 궁금해한 건 이것",
    ]
    balanced = [
        f"{subject}, 사람들이 주목하는 이유를 정리했습니다",
        f"{subject} 소식의 핵심, 30초 안에 정리",
        f"{subject}, 놓치기 쉬운 핵심 포인트",
        f"{subject}, 현재까지 확인된 내용은 여기까지",
        f"{subject}, 지금 알아야 할 핵심 한 가지",
        f"{subject} 소식은 왜 다시 주목받고 있을까요?",
    ]
    factual = [
        f"{subject} 핵심 정리, 확인된 사실만 봤습니다",
        f"{subject} 소식 30초 요약",
        f"{subject}, 공식 내용과 추측을 구분해 봤습니다",
        f"{subject} 관련 확인된 핵심 포인트",
        f"{subject} 소식에서 꼭 알아야 할 내용",
        f"{subject}, 기사 핵심만 빠르게 정리",
    ]

    candidates = list(strong if style.startswith("강한") else factual if style.startswith("정보형") else balanced)

    if title_numbers:
        number = title_numbers[0]
        candidates.extend([
            f"{subject}, {number}보다 더 중요한 핵심",
            f"{number}까지 언급된 {subject}, 꼭 볼 부분",
        ])
    if re.search(r"논란|의혹|갑론을박|비판|갈등", source_text):
        candidates = [
            f"{subject} 논란, 일이 커진 결정적 이유",
            f"{subject} 의혹, 사실과 추측을 나눠 봤습니다",
            *candidates,
        ]
    if re.search(r"해명|반박|사과|입장", source_text):
        candidates.insert(0, f"{subject}, 공식 입장에서 놓치면 안 될 부분")
    if re.search(r"결혼|열애|이혼|결별|복귀|컴백|출연|캐스팅", source_text):
        candidates.insert(0, f"{subject}, 갑자기 관심이 집중된 이유")
    if re.search(r"급등|급락|상승|하락|주가|실적", source_text):
        candidates = [f"{subject}, 숫자가 움직인 진짜 배경", f"{subject}, 수치보다 먼저 봐야 할 한 가지", *candidates]
    if re.search(r"확정|발표|공개|출시|시행", source_text):
        candidates.insert(0, f"{subject}, 공식 발표에서 놓치면 안 될 핵심")

    category_candidates = {
        "연예": [f"{subject}, 반응이 커진 진짜 이유", f"{subject}, 모두가 궁금해한 핵심만 정리"],
        "국내 이슈": [f"{subject}, 지금 반드시 알아야 할 핵심", f"{subject} 이슈, 핵심은 따로 있었습니다"],
        "해외 이슈": [f"{subject}, 해외 보도에서 놓치면 안 될 부분", f"{subject}, 국내 보도만 보면 놓치는 핵심"],
        "경제·주식": [f"{subject}, 숫자보다 먼저 봐야 할 한 가지", f"{subject}, 투자자가 놓치기 쉬운 핵심"],
        "생활정보": [f"{subject}, 나에게 해당되는지 30초 확인", f"{subject}, 모르고 지나치면 놓치는 조건"],
        "제품·리뷰": [f"{subject}, 사기 전에 반드시 볼 포인트", f"{subject}, 광고보다 먼저 확인할 한 가지"],
    }
    candidates.extend(category_candidates.get(category, []))

    cleaned: list[str] = []
    for candidate in candidates:
        candidate = re.sub(r"\s+", " ", candidate).strip(" -|｜")
        if not candidate or any(word in candidate for word in TITLE_BANNED_WORDS):
            continue
        if len(candidate) > 44:
            candidate = _short_caption(candidate, 42)
        if candidate not in cleaned:
            cleaned.append(candidate)

    cleaned.sort(key=lambda item: _title_score(item, subject, title_numbers, style), reverse=True)
    return cleaned[:8] or [_short_caption(clean_title, 42)]


def generate_zero_key_plan(article: ArticleData, duration: int, category: str, title_style: str = "강한 후킹형 · 클릭 유도 추천") -> dict[str, Any]:
    """외부 생성형 AI 없이 제목·핵심어·수치만 구조화해 새 대본을 만든다."""
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
    title_candidates = generate_attention_titles(article, category, keywords, numbers, title_style)
    return {
        "video_title": title_candidates[0],
        "title_candidates": title_candidates,
        "title_style": title_style,
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

    title_candidates = [str(x).strip() for x in (plan.get("title_candidates") or []) if str(x).strip()]
    selected_title = str(plan.get("video_title") or article.title).strip()
    if selected_title and selected_title not in title_candidates:
        title_candidates.insert(0, selected_title)
    return {
        "video_title": selected_title,
        "title_candidates": title_candidates[:8] or [selected_title],
        "title_style": str(plan.get("title_style") or "강한 후킹형 · 클릭 유도 추천"),
        "hook": str(plan.get("hook") or scenes[0]["narration"]).strip(),
        "narration": narration,
        "description": str(plan.get("description") or f"원문 출처: {article.url}").strip(),
        "hashtags": [str(tag).strip() for tag in hashtags if str(tag).strip()],
        "core_facts": [str(x).strip() for x in (plan.get("core_facts") or []) if str(x).strip()][:3],
        "originality_overlap_words": int(plan.get("originality_overlap_words") or 0),
        "scenes": scenes[:10],
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
        pexels_url = search_pexels_image(scene.get("stock_keywords", ""), pexels_key)
        if pexels_url:
            candidate = temp_root / f"pexels_{idx:02}.jpg"
            if _download_image(pexels_url, candidate):
                image_path = candidate
                used_visuals.append(f"장면 {idx + 1}: Pexels 스톡 이미지 · {scene.get('stock_keywords','')}")
        if image_path is None:
            used_visuals.append(f"장면 {idx + 1}: 프로그램 자체 생성 에디토리얼 그래픽")

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
        "쇼츠메이커 CLOUD V3.2 저작권 안전 점검\n\n"
        "[대본]\n기사 원문 직접 낭독: 사용 안 함\n사실 기반 재작성: 적용\n"
        f"원문과 최장 연속 유사 어절: {int(plan.get('originality_overlap_words') or 0)}어절\n"
        "[영상]\n기사 대표 사진: 사용 안 함\n방송 캡처/타 유튜브 영상: 사용 안 함\n"
        "프로그램 자체 생성 에디토리얼 그래픽: 사용\n"
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

    zip_path = temp_root / "shorts_result_v32.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in (
            final_video, audio_path, srt_path, script_path, metadata_path, source_path,
            visual_path, license_path, report_path, disclosure_path,
        ):
            archive.write(path, path.name)

    return {
        "video": final_video,
        "audio": audio_path,
        "srt": srt_path,
        "script": script_path,
        "metadata": metadata_path,
        "source": source_path,
        "visual_sources": visual_path,
        "music_license": license_path,
        "copyright_report": report_path,
        "ai_disclosure": disclosure_path,
        "zip": zip_path,
    }

# 쇼츠메이커 CLOUD V3.2
# 제목 자동 적용 + 나레이션 선택 복원 버전
# 기사 링크를 붙여넣고 Enter를 누르면 제목/대본을 자동 생성합니다.

import hashlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

import streamlit as st


VERSION = "3.2"
STATE_PLAN = "v32_plan"
STATE_RESULT = "v32_result"
STATE_AUTO_REQUEST = "v32_auto_request"


def _request_article_analysis() -> None:
    """기사 링크 입력을 마치면 분석 예약 플래그만 설정한다."""
    if str(st.session_state.get("v32-url", "")).strip():
        st.session_state[STATE_AUTO_REQUEST] = True


def _safe_download_name(title: str, suffix: str) -> str:
    clean = re.sub(r"[\\/:*?\"<>|]+", " ", title or "쇼츠영상")
    clean = re.sub(r"\s+", " ", clean).strip(" .")
    clean = clean[:55] or "쇼츠영상"
    return f"{clean}{suffix}"


st.set_page_config(
    page_title="쇼츠메이커 CLOUD V3.2",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
.block-container {max-width:1180px; padding-top:1rem; padding-bottom:4rem;}
.hero {padding:27px 30px; border-radius:24px; background:linear-gradient(135deg,#10172c,#34266d 56%,#7556df); color:#fff; margin-bottom:16px; box-shadow:0 15px 38px rgba(37,28,91,.2)}
.hero h1 {font-size:2.05rem; margin:0 0 8px; letter-spacing:-.035em}
.hero p {font-size:1rem; margin:0; opacity:.95}
.pills {display:flex; flex-wrap:wrap; gap:8px; margin-top:14px}
.pill {padding:7px 11px; border:1px solid rgba(255,255,255,.2); background:rgba(255,255,255,.13); border-radius:999px; font-size:.82rem}
.step {font-size:1.14rem; font-weight:850; margin:17px 0 8px}
.info-box {padding:14px 16px; border-radius:15px; background:#eef8ff; border:1px solid #c5e4fb; color:#184e72; margin:8px 0 15px}
.safe-box {padding:14px 16px; border-radius:15px; background:#effbf4; border:1px solid #c4ead2; color:#165c37; margin:8px 0 15px}
.title-card {padding:20px 22px; border-radius:19px; background:linear-gradient(135deg,#fff4e6,#fff9f2); border:2px solid #ffb45b; margin:12px 0 16px; box-shadow:0 9px 22px rgba(224,117,29,.10)}
.title-card .label {font-size:.86rem; color:#a65300; font-weight:850; margin-bottom:7px}
.title-card .title {font-size:1.42rem; line-height:1.38; color:#2b1b11; font-weight:900; letter-spacing:-.025em}
.title-card .sub {font-size:.84rem; color:#7b5b43; margin-top:8px}
.note {padding:13px 15px; border-radius:14px; background:#f5f2ff; border:1px solid #ddd5ff; color:#49377a; font-size:.91rem}
.stButton > button {border-radius:14px; min-height:52px; font-weight:800}
[data-testid="stDownloadButton"] button {border-radius:12px; font-weight:750}
@media (max-width:700px) {.hero{padding:21px 19px}.hero h1{font-size:1.62rem}.block-container{padding-left:.8rem;padding-right:.8rem}.title-card .title{font-size:1.18rem}}
</style>
<div class="hero">
  <h1>🎬 쇼츠메이커 CLOUD V3.2</h1>
  <p>기사 링크만 넣으면 주목형 제목을 자동 적용하고, AI 성우 또는 내 목소리를 직접 선택해 제작합니다.</p>
  <div class="pills">
    <span class="pill">링크 입력 후 제목 자동 생성</span>
    <span class="pill">AI 성우 10종 선택</span>
    <span class="pill">내 목소리 보정 선택</span>
    <span class="pill">자동 BGM</span>
    <span class="pill">기사 사진 미사용</span>
  </div>
</div>
<div class="info-box"><b>이번 수정 핵심</b><br>나레이션 선택을 첫 화면에 다시 배치했습니다. 기사 링크를 붙여넣고 Enter를 누르거나 분석 버튼을 누르면, 가장 주목도 높은 제목이 즉시 영상 제목으로 자동 적용됩니다.</div>
<div class="safe-box"><b>과장 방지</b><br>클릭을 유도하되 기사에 없는 사실을 만들거나 ‘충격·무조건·100%’ 같은 허위·과장 표현은 사용하지 않습니다.</div>
""",
    unsafe_allow_html=True,
)

with st.expander("📱 휴대폰·PC에 바로가기 설치", expanded=False):
    st.markdown(
        """
- **안드로이드 Chrome:** 오른쪽 위 `⋮` → `홈 화면에 추가` 또는 `앱 설치`
- **아이폰 Safari:** `공유` → `홈 화면에 추가`
- **Windows Chrome/Edge:** 주소창 오른쪽 설치 또는 바로가기 아이콘

집·회사·휴대폰에서 동일한 Streamlit 주소를 사용합니다.
"""
    )

with st.expander("다른 기기에서 이어서 수정할 프로젝트 불러오기", expanded=False):
    project_upload = st.file_uploader("프로젝트 JSON", type=["json"], key="v32-project-import")
    if project_upload is not None and st.button("프로젝트 불러오기", use_container_width=True, key="v32-project-load"):
        try:
            payload = json.loads(project_upload.getvalue().decode("utf-8"))
            article = ArticleData(**payload["article"])
            plan = normalize_plan(payload["plan"], article, int(payload.get("duration", 45)))
            st.session_state[STATE_PLAN] = {
                "article": article,
                "plan": plan,
                "category": payload.get("category", "연예"),
                "duration": int(payload.get("duration", 45)),
                "title_style": payload.get("title_style", TITLE_STYLE_OPTIONS[0]),
                "engine": "무키 자체 해설 엔진",
            }
            for key in ("v32-title-choice", "v32-custom-title", "v32-script"):
                st.session_state.pop(key, None)
            st.session_state.pop(STATE_RESULT, None)
            st.success("프로젝트를 불러왔습니다.")
            st.rerun()
        except Exception as exc:
            st.error(f"프로젝트 파일을 읽지 못했습니다: {exc}")

# 1. 기사 입력
st.markdown('<div class="step">1. 기사 링크와 제작 조건</div>', unsafe_allow_html=True)
url = st.text_input(
    "대표 기사 링크 · 붙여넣고 Enter를 누르면 자동 분석",
    placeholder="https://m.entertain.naver.com/article/...",
    key="v32-url",
    on_change=_request_article_analysis,
)
with st.expander("링크 분석이 막힐 때만 기사 제목·본문 직접 입력", expanded=False):
    manual_title = st.text_input("기사 제목", placeholder="기사 제목", key="v32-manual-title")
    manual_text = st.text_area(
        "기사 본문",
        placeholder="본문 추출이 막힌 경우에만 붙여넣으세요. 기사 사진은 사용하지 않습니다.",
        height=135,
        key="v32-manual-text",
    )

c1, c2, c3 = st.columns(3)
with c1:
    category = st.selectbox("콘텐츠 유형", ["연예", "국내 이슈", "해외 이슈", "경제·주식", "생활정보", "제품·리뷰"], key="v32-category")
with c2:
    target_duration = st.selectbox("목표 길이", [30, 45, 60], index=1, format_func=lambda x: f"{x}초", key="v32-duration")
with c3:
    title_style = st.selectbox(
        "자동 제목 스타일",
        TITLE_STYLE_OPTIONS,
        index=0,
        help="가장 높은 점수의 제목이 영상에 자동 적용됩니다.",
        key="v32-title-style",
    )

# 2. 나레이션 선택 - 계획 생성 전부터 항상 표시
st.markdown('<div class="step">2. 나레이션 선택</div>', unsafe_allow_html=True)
narration_mode = st.radio(
    "사용할 목소리",
    ["AI 성우 중 선택", "내 목소리 녹음·업로드 후 보정"],
    horizontal=True,
    key="v32-narration-mode",
)
voice_label = list(VOICE_OPTIONS)[0]
rate_label = "쇼츠 추천"
voice_preset_label = "밝고 듣기 좋게"
uploaded_voice = None
recorded_audio = None
if narration_mode == "AI 성우 중 선택":
    v1, v2 = st.columns(2)
    with v1:
        voice_label = st.selectbox("AI 성우", list(VOICE_OPTIONS), index=0, key="v32-ai-voice")
    with v2:
        rate_label = st.selectbox("말하기 속도", list(RATE_OPTIONS), index=2, key="v32-ai-rate")
    st.caption("AI 성우 10종 중 선택할 수 있으며, 별도 API 키 입력은 없습니다.")
else:
    v1, v2 = st.columns(2)
    with v1:
        voice_preset_label = st.selectbox("내 목소리 보정 스타일", list(VOICE_PRESET_OPTIONS), index=1, key="v32-my-preset")
    with v2:
        uploaded_voice = st.file_uploader("대본 전체를 읽은 음성 파일", type=["wav", "mp3", "m4a", "aac", "ogg"], key="v32-my-upload")
    if hasattr(st, "audio_input"):
        recorded_audio = st.audio_input("또는 브라우저에서 직접 녹음", key="v32-my-record")
    st.caption("짧은 샘플로 목소리를 복제하는 방식이 아니라, 대본 전체 녹음을 선택한 분위기로 보정합니다.")

# 3. 음악
st.markdown('<div class="step">3. 배경음악</div>', unsafe_allow_html=True)
m1, m2, m3 = st.columns(3)
with m1:
    music_ui = st.selectbox(
        "음악 방식",
        ["콘텐츠에 맞춰 자동 추천", "내장 오리지널 중 직접 선택", "YouTube 오디오 라이브러리 음원 업로드", "음악 사용 안 함"],
        key="v32-music-mode",
    )
with m2:
    selected_music = st.selectbox("내장 음악", list(MUSIC_TRACK_OPTIONS), disabled=music_ui != "내장 오리지널 중 직접 선택", key="v32-music-track")
with m3:
    music_volume_pct = st.slider("배경음악 크기", 3, 25, 10, 1, format="%d%%", disabled=music_ui == "음악 사용 안 함", key="v32-music-volume")
custom_music_upload = None
attribution_text = ""
if music_ui == "YouTube 오디오 라이브러리 음원 업로드":
    c1, c2 = st.columns(2)
    with c1:
        custom_music_upload = st.file_uploader("다운로드한 음원", type=["mp3", "wav", "m4a", "aac", "ogg"], key="v32-music-upload")
    with c2:
        attribution_text = st.text_input("저작자 표시 문구 (필요한 곡만)", placeholder="Music: 곡명 - 아티스트", key="v32-attribution")

# 4. 디자인
st.markdown('<div class="step">4. 영상 디자인</div>', unsafe_allow_html=True)
d1, d2, d3 = st.columns(3)
with d1:
    template_label = st.selectbox("영상 템플릿", list(TEMPLATE_OPTIONS), index=0, key="v32-template")
with d2:
    subtitle_label = st.selectbox("자막 스타일", list(SUBTITLE_STYLE_OPTIONS), index=0, key="v32-subtitle")
with d3:
    accent_label = st.selectbox("강조 색상", list(ACCENT_COLOR_OPTIONS), index=0, key="v32-accent")
d4, d5 = st.columns(2)
with d4:
    resolution_label = st.selectbox("출력 해상도", list(RESOLUTION_OPTIONS), index=0, key="v32-resolution")
with d5:
    show_hook = st.toggle("자동 생성 제목을 영상 상단에 표시", value=True, key="v32-show-title")
st.caption("노란색 ‘연예’ 배지는 표시하지 않습니다. 기사 사진 대신 프로그램 자체 에디토리얼 그래픽을 사용합니다.")

make_plan = st.button("🔥 기사 분석 + 주목형 제목·대본 자동 생성", type="primary", use_container_width=True, key="v32-make-plan")
auto_requested = bool(st.session_state.pop(STATE_AUTO_REQUEST, False))
should_make_plan = make_plan or (auto_requested and url.strip())

if should_make_plan:
    if not url.strip() and not manual_text.strip():
        st.error("기사 링크 또는 기사 본문을 입력해주세요.")
        st.stop()
    progress = st.progress(0, text="기사에서 확인 가능한 핵심을 분석하고 있습니다.")
    status = st.empty()
    try:
        if url.strip():
            try:
                article = fetch_article(url.strip())
            except Exception as exc:
                if not manual_text.strip():
                    raise ShortsMakerError(f"기사 본문을 불러오지 못했습니다. 직접 입력 영역에 제목과 본문을 붙여넣어 주세요.\n\n{exc}")
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

        progress.progress(45, text="주목도가 높은 제목 후보를 평가하고 있습니다.")
        raw = generate_zero_key_plan(article, target_duration, category, title_style)
        plan = normalize_plan(raw, article, target_duration)
        # 1위 제목을 즉시 영상 제목으로 자동 확정한다.
        if plan.get("title_candidates"):
            plan["video_title"] = plan["title_candidates"][0]
        progress.progress(100, text="제목과 대본을 자동 적용했습니다.")
        status.success(f"자동 적용 제목: {plan['video_title']}")
        st.session_state[STATE_PLAN] = {
            "article": article,
            "plan": plan,
            "category": category,
            "duration": target_duration,
            "title_style": title_style,
            "engine": "무키 자체 해설 엔진",
        }
        st.session_state.pop(STATE_RESULT, None)
        for key in ("v32-title-choice", "v32-custom-title", "v32-script"):
            st.session_state.pop(key, None)
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
    active_title_style = plan_state.get("title_style", plan.get("title_style", TITLE_STYLE_OPTIONS[0]))

    st.divider()
    st.markdown('<div class="step">5. 자동 적용 제목과 대본 확인</div>', unsafe_allow_html=True)

    candidates = plan.get("title_candidates") or [plan.get("video_title") or article.title]
    selected_candidate = st.radio(
        "제목 후보를 바꾸면 영상 제목도 즉시 변경됩니다",
        candidates,
        index=candidates.index(plan["video_title"]) if plan.get("video_title") in candidates else 0,
        key="v32-title-choice",
    )
    custom_title = st.text_input(
        "직접 수정할 제목 (비워두면 위에서 선택한 제목 사용)",
        value="",
        placeholder="직접 바꾸고 싶은 경우에만 입력",
        key="v32-custom-title",
    )
    applied_title = custom_title.strip() or selected_candidate
    plan["video_title"] = applied_title

    st.markdown(
        f'<div class="title-card"><div class="label">🔥 현재 영상에 자동 적용되는 제목</div><div class="title">{html_lib.escape(applied_title)}</div><div class="sub">영상 상단 제목·YouTube 업로드 정보·다운로드 파일명에 반영됩니다.</div></div>',
        unsafe_allow_html=True,
    )

    t1, t2 = st.columns([1, 1])
    with t1:
        if st.button("🔄 다른 분위기의 제목 후보 만들기", use_container_width=True, key="v32-new-title-style"):
            style_index = TITLE_STYLE_OPTIONS.index(active_title_style) if active_title_style in TITLE_STYLE_OPTIONS else 0
            next_style = TITLE_STYLE_OPTIONS[(style_index + 1) % len(TITLE_STYLE_OPTIONS)]
            refreshed = generate_attention_titles(article, category, style=next_style)
            plan["title_candidates"] = refreshed
            plan["video_title"] = refreshed[0]
            plan["title_style"] = next_style
            st.session_state[STATE_PLAN]["title_style"] = next_style
            st.session_state[STATE_PLAN]["plan"] = plan
            for key in ("v32-title-choice", "v32-custom-title"):
                st.session_state.pop(key, None)
            st.rerun()
    with t2:
        st.caption(f"현재 제목 스타일: {active_title_style}")

    edited_script = st.text_area(
        "읽을 전체 대본 · 직접 수정 가능",
        value=plan["narration"],
        height=270,
        key="v32-script",
    )

    col_info, col_save = st.columns([1.2, 1])
    with col_info:
        st.markdown("**프로그램이 확인한 정보**")
        for fact in plan.get("core_facts") or [article.title]:
            st.write(f"• {fact}")
        overlap = int(plan.get("originality_overlap_words") or 0)
        if overlap >= 8:
            st.warning(f"기사 원문과 최대 {overlap}어절이 연속으로 겹칠 수 있습니다. 대본을 조금 더 수정하는 편이 안전합니다.")
        else:
            st.success(f"원문 연속 유사 표현 검사: 최대 {overlap}어절")
    with col_save:
        project_payload = {
            "version": VERSION,
            "article": asdict(article),
            "plan": {**plan, "video_title": applied_title, "narration": edited_script},
            "category": category,
            "duration": duration,
            "title_style": active_title_style,
        }
        st.download_button(
            "💾 다른 기기용 프로젝트 저장",
            json.dumps(project_payload, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name="shorts_project_v32.json",
            mime="application/json",
            use_container_width=True,
        )
        selected_voice_summary = voice_label if narration_mode == "AI 성우 중 선택" else f"내 목소리 · {voice_preset_label}"
        st.markdown(
            f'<div class="note"><b>현재 제작 설정</b><br>제목: {html_lib.escape(applied_title)}<br>목소리: {html_lib.escape(selected_voice_summary)}<br>음악: {html_lib.escape(music_ui)}</div>',
            unsafe_allow_html=True,
        )

    render = st.button("🎬 이 제목·목소리로 쇼츠 완성하기", type="primary", use_container_width=True, key="v32-render")
    if render:
        source_audio = uploaded_voice or recorded_audio
        if narration_mode != "AI 성우 중 선택" and source_audio is None:
            st.error("내 목소리 방식을 선택하셨습니다. 대본 전체를 읽은 음성 파일을 올리거나 직접 녹음해주세요.")
            st.stop()
        if music_ui == "YouTube 오디오 라이브러리 음원 업로드" and custom_music_upload is None:
            st.error("사용할 YouTube 오디오 라이브러리 음원을 업로드해주세요.")
            st.stop()

        plan["video_title"] = applied_title
        plan = plan_from_edited_narration(plan, edited_script, article, duration)
        plan["video_title"] = applied_title
        if applied_title not in plan.get("title_candidates", []):
            plan.setdefault("title_candidates", []).insert(0, applied_title)
        st.session_state[STATE_PLAN]["plan"] = plan

        workdir = Path(tempfile.mkdtemp(prefix="shortsmaker_cloud_v32_"))
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
                "title": applied_title,
                **{name: path.read_bytes() for name, path in files.items()},
            }
        except ShortsMakerError as exc:
            progress.empty(); status.empty(); st.error(str(exc))
        except Exception as exc:
            progress.empty(); status.empty(); st.exception(exc)

result = st.session_state.get(STATE_RESULT)
if result:
    st.divider()
    st.subheader("완성 영상")
    st.markdown(
        f'<div class="title-card"><div class="label">YouTube 업로드 제목</div><div class="title">{html_lib.escape(result.get("title") or result["plan"]["video_title"])}</div></div>',
        unsafe_allow_html=True,
    )
    left, right = st.columns([1, 1])
    with left:
        st.video(result["video"], format="video/mp4")
        st.download_button(
            "⬇️ MP4 영상 다운로드",
            result["video"],
            _safe_download_name(result.get("title") or "쇼츠영상", ".mp4"),
            "video/mp4",
            type="primary",
            use_container_width=True,
            key=f"v32-video-{result['key']}",
        )
        st.download_button(
            "⬇️ 결과 전체 ZIP",
            result["zip"],
            _safe_download_name(result.get("title") or "쇼츠영상", "_전체파일.zip"),
            "application/zip",
            use_container_width=True,
            key=f"v32-zip-{result['key']}",
        )
    with right:
        st.download_button("대본 TXT", result["script"], "script.txt", "text/plain", use_container_width=True, key=f"v32-script-dl-{result['key']}")
        st.download_button("자막 SRT", result["srt"], "subtitles.srt", "text/plain", use_container_width=True, key=f"v32-srt-{result['key']}")
        st.download_button("YouTube 제목·설명", result["metadata"], "youtube_metadata.txt", "text/plain", use_container_width=True, key=f"v32-meta-{result['key']}")
        st.download_button("저작권 점검 보고서", result["copyright_report"], "copyright_check_report.txt", "text/plain", use_container_width=True, key=f"v32-copy-{result['key']}")
        st.markdown('<div class="note"><b>업로드 순서</b><br>MP4를 YouTube Shorts에 올리고, `youtube_metadata.txt`의 제목·설명·해시태그를 복사하세요.</div>', unsafe_allow_html=True)
