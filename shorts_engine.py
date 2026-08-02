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
- 기사 문장을 길게 베끼지 말고 사실만 새 문장으로 설명합니다.
- 확인되지 않은 추측, 과장, 명예훼손 표현을 넣지 않습니다.
- 첫 장면은 2초 이내의 강한 궁금증형 후킹 문장입니다.
- 전체 나레이션은 자연스러운 한국어 구어체이며 {duration}초 안에 읽을 수 있어야 합니다.
- 장면은 6~9개, 장면별 화면 자막은 18자 안팎으로 짧게 씁니다.
- caption은 최대 2줄로 자연스럽게 나눌 수 있는 문장으로 작성합니다.
- emphasis는 화면에서 강조할 핵심 단어 1~2개를 배열로 작성합니다.
- 마지막은 시청자 의견을 묻는 한 문장으로 끝냅니다.
- stock_keywords는 이미지 검색에 쓸 영어 단어 2~4개로 작성합니다.

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
            return _extract_json(_response_text(_gemini_request(api_key, model, payload)))
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


def generate_local_plan(article: ArticleData, duration: int, category: str) -> dict[str, Any]:
    sentences = _split_sentences(article.text)
    filtered = [
        sentence
        for sentence in sentences
        if not re.search(r"무단전재|재배포|기자|Copyright|구독|로그인|광고", sentence, re.I)
    ]
    selected: list[str] = []
    for sentence in filtered:
        if any(sentence[:25] in existing or existing[:25] in sentence for existing in selected):
            continue
        selected.append(sentence)
        if len(selected) >= 6:
            break
    if not selected:
        selected = [article.title]

    hook = f"지금 화제가 된 건, 바로 {article.title[:35]}입니다."
    closing = "여러분은 이 소식, 어떻게 보시나요?"
    narration_parts = [hook, *selected[:5], closing]
    narration = " ".join(narration_parts)
    scenes = [
        {
            "caption": _short_caption(part, 24),
            "emphasis": _pick_emphasis(part),
            "narration": part,
            "stock_keywords": category,
            "visual_note": "기사 이미지 또는 정보형 그래픽",
        }
        for part in narration_parts
    ]
    return {
        "video_title": _short_caption(article.title, 48),
        "hook": hook,
        "narration": narration,
        "description": f"{article.title}\n\n원문 출처: {article.url}",
        "hashtags": ["#쇼츠", "#뉴스", f"#{category.replace(' ', '')}"],
        "core_facts": selected[:3],
        "scenes": scenes,
    }


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

    return {
        "video_title": str(plan.get("video_title") or article.title).strip(),
        "hook": str(plan.get("hook") or scenes[0]["narration"]).strip(),
        "narration": narration,
        "description": str(plan.get("description") or f"원문 출처: {article.url}").strip(),
        "hashtags": [str(tag).strip() for tag in hashtags if str(tag).strip()],
        "core_facts": [str(x).strip() for x in (plan.get("core_facts") or []) if str(x).strip()][:3],
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


def _draw_badge(
    canvas: Image.Image,
    text: str,
    x: int,
    y: int,
    accent: tuple[int, int, int],
    scale: float,
) -> tuple[int, int, int, int]:
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(find_font(True), max(18, int(21 * scale)))
    text_w, text_h = _measure(draw, text, font)
    pad_x, pad_y = int(18 * scale), int(9 * scale)
    rect = (x, y, x + text_w + pad_x * 2, y + text_h + pad_y * 2)
    draw.rounded_rectangle(rect, radius=int(18 * scale), fill=(*accent, 238))
    draw.text((x + pad_x, y + pad_y - int(2 * scale)), text, font=font, fill=(18, 19, 24))
    return rect


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
    show_badge: bool = True,
    size: tuple[int, int] = (720, 1280),
) -> None:
    width, height = size
    scale = width / 720
    margin = int(42 * scale)
    canvas = _prepare_background(image_path, size, index, background_mode, template)
    canvas = _overlay_vertical_gradient(
        canvas,
        top_alpha=170 if template != "card" else 190,
        bottom_alpha=230 if template == "highlight" else 215,
        middle_alpha=16,
    )
    draw = ImageDraw.Draw(canvas)

    font_bold = find_font(True)
    font_regular = find_font(False)
    source_text = _short_caption(f"출처 · {source}" if source else "기사 기반 AI 요약", 54)

    if template == "news":
        if show_badge:
            _draw_badge(canvas, category or "NEWS", margin, int(48 * scale), accent, scale)
        title_y = int(118 * scale) if show_badge else int(62 * scale)
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
        if show_badge:
            _draw_badge(canvas, category or "INFO", margin, int(48 * scale), accent, scale)
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
        if show_badge:
            _draw_badge(canvas, category or "TREND", margin, int(48 * scale), accent, scale)
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
        caption_width = width - margin * 2

    caption_font, caption_lines = _fit_font_lines(
        draw,
        caption,
        font_bold,
        int((58 if template == "highlight" else 52) * scale),
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
    show_badge: bool = True,
    category: str = "뉴스",
    resolution: tuple[int, int] = (720, 1280),
) -> dict[str, Path]:
    progress = progress or (lambda _: None)
    temp_root = Path(workdir or tempfile.mkdtemp(prefix="shortsmaker_"))
    temp_root.mkdir(parents=True, exist_ok=True)

    scenes: list[dict[str, str]] = plan["scenes"]
    narration = plan["narration"]
    audio_path = temp_root / "narration.mp3"
    progress("AI 성우 나레이션을 생성하고 있습니다.")
    actual_voice = synthesize_edge_tts(narration, voice, rate, audio_path)
    audio_duration = ffprobe_duration(audio_path)
    durations = allocate_durations(scenes, max(audio_duration + 0.35, 3.0 * len(scenes)))

    progress("기사 이미지와 장면을 구성하고 있습니다.")
    downloaded_article_images: list[Path] = []
    for idx, image_url in enumerate(article.images[:10]):
        path = temp_root / f"article_{idx:02}.jpg"
        if _download_image(image_url, path):
            downloaded_article_images.append(path)

    scene_clips: list[Path] = []
    for idx, (scene, scene_duration) in enumerate(zip(scenes, durations)):
        image_path: Path | None = None
        pexels_url = search_pexels_image(scene.get("stock_keywords", ""), pexels_key)
        if pexels_url:
            candidate = temp_root / f"pexels_{idx:02}.jpg"
            if _download_image(pexels_url, candidate):
                image_path = candidate
        if image_path is None and downloaded_article_images:
            image_path = downloaded_article_images[idx % len(downloaded_article_images)]

        card_path = temp_root / f"scene_{idx:02}.jpg"
        make_scene_card(
            image_path=image_path,
            output_path=card_path,
            caption=scene["caption"],
            emphasis=scene.get("emphasis") or [],
            title=plan["video_title"],
            hook=plan.get("hook") or plan["video_title"],
            category=category,
            source=article.publisher or urllib.parse.urlparse(article.url).netloc,
            index=idx,
            template=template,
            subtitle_style=subtitle_style,
            accent=accent,
            background_mode=background_mode,
            show_hook=show_hook,
            show_badge=show_badge,
            size=resolution,
        )
        clip_path = temp_root / f"clip_{idx:02}.mp4"
        _run(
            [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                str(card_path),
                "-t",
                f"{scene_duration:.3f}",
                "-vf",
                (
                    f"scale={resolution[0]}:{resolution[1]},"
                    f"zoompan=z='min(zoom+0.00035,1.045)':"
                    f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d=1:s={resolution[0]}x{resolution[1]}:fps=30,format=yuv420p"
                ),
                "-r",
                "30",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-an",
                str(clip_path),
            ],
            f"{idx + 1}번 장면 렌더링",
        )
        scene_clips.append(clip_path)

    progress("장면과 나레이션을 하나의 세로 영상으로 합치고 있습니다.")
    concat_file = temp_root / "concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{clip.as_posix()}'" for clip in scene_clips),
        encoding="utf-8",
    )
    silent_video = temp_root / "silent.mp4"
    _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(silent_video),
        ],
        "장면 합치기",
    )

    final_video = temp_root / "final_short.mp4"
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(silent_video),
            "-i",
            str(audio_path),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(final_video),
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
        f"{' '.join(plan['hashtags'])}\n\n원문\n{article.url}\n",
        encoding="utf-8-sig",
    )
    source_path = temp_root / "source.txt"
    source_path.write_text(
        f"기사 제목: {article.title}\n매체: {article.publisher}\nURL: {article.url}\n"
        f"사용 성우: {actual_voice}\n",
        encoding="utf-8-sig",
    )

    zip_path = temp_root / "shorts_result.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in (final_video, audio_path, srt_path, script_path, metadata_path, source_path):
            archive.write(path, path.name)

    return {
        "video": final_video,
        "audio": audio_path,
        "srt": srt_path,
        "script": script_path,
        "metadata": metadata_path,
        "source": source_path,
        "zip": zip_path,
    }
