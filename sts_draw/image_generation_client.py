from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request

from sts_draw.models import LineArtResult


DEFAULT_PROMPT = """
请将输入图像转换为单线条骨架图（Centerline Drawing），目标是提取适合绘制的矢量路径。

技术执行要求：

单线渲染： 仅勾勒物体中心路径，绝对禁止将色块的“内外边缘”同时提取形成双线。忽略一切填充区域的轮廓。
线条逻辑： 将图像理解为由一维线条构成的图形，而非二维色块的边缘组合。
极简处理： 仅保留表达主体结构的极少量线条。对于粗笔触区域，只取其中心轨迹，不取其填充边界。
风格统一： 全图保持统一的单像素级线条粗细，不随原图笔触动态变化。
排除干扰： 彻底忽略肤色分区线、无结构意义的平滑过渡线、阴影边缘、非结构性色块边缘。
输出规范： 输出纯黑线条（#000000）与纯白背景（#FFFFFF），完全二值化，无灰度、无抗锯齿模糊。
""".strip()

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-3.1-flash-image-preview"


@dataclass(slots=True)
class OpenAICompatibleSettings:
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    proxy_url: str | None = None


class OpenAICompatibleClient:
    def __init__(self, settings: OpenAICompatibleSettings | None = None) -> None:
        self.settings = settings or OpenAICompatibleSettings(
            api_key=_env_first("OPENROUTER_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"),
            model=_env_first("LLM_MODEL", "OPENROUTER_MODEL", "MODEL") or DEFAULT_MODEL,
            base_url=_env_first("LLM_BASE_URL", "OPENROUTER_BASE_URL", "OPENAI_BASE_URL") or DEFAULT_BASE_URL,
            proxy_url=_env_first("HTTPS_PROXY", "HTTP_PROXY"),
        )

    def generate_line_art(self, image_path: str, prompt: str | None = None) -> LineArtResult:
        api_key = self.settings.api_key
        if not api_key:
            raise RuntimeError("Provider API key is missing.")

        image_bytes = Path(image_path).read_bytes()
        payload = self._build_payload(image_path=image_path, image_bytes=image_bytes, prompt=prompt or DEFAULT_PROMPT)
        response = self._post_json(payload)
        inline_data = _extract_image_bytes(response)
        width, height = _probe_png_size(inline_data)
        return LineArtResult(
            image_bytes=inline_data,
            mime_type="image/png",
            width=width,
            height=height,
            prompt=prompt or DEFAULT_PROMPT,
        )

    def get_cached_line_art(self, image_path: str) -> LineArtResult | None:
        try:
            source_bytes = Path(image_path).read_bytes()
            cached_bytes = _line_art_cache_path(source_bytes).read_bytes()
        except OSError:
            return None

        width, height = _probe_png_size(cached_bytes)
        if width <= 0 or height <= 0:
            return None
        return LineArtResult(
            image_bytes=cached_bytes,
            mime_type="image/png",
            width=width,
            height=height,
            prompt=DEFAULT_PROMPT,
        )

    def save_cached_line_art(self, image_path: str, line_art: LineArtResult) -> None:
        try:
            source_bytes = Path(image_path).read_bytes()
            cache_path = _line_art_cache_path(source_bytes)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(line_art.image_bytes)
        except OSError:
            return

    def _build_payload(self, image_path: str, image_bytes: bytes, prompt: str) -> dict[str, object]:
        mime_type = _guess_mime_type(image_path)
        base64_image = base64.b64encode(image_bytes).decode("ascii")
        return {
            "model": self.settings.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}},
                    ],
                }
            ],
            "modalities": ["image", "text"],
        }

    def _post_json(self, payload: dict[str, object]) -> dict[str, object]:
        endpoint = _chat_completions_endpoint(self.settings.base_url)
        http_request = request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        urlopen = self._urlopen()
        try:
            with urlopen(http_request) as response:
                response_body = response.read()
                response_data = json.loads(response_body.decode("utf-8"))
                if getattr(response, "status", 200) >= 400:
                    message = _extract_error_message(response_data) or f"Provider request failed with status {response.status}."
                    raise RuntimeError(message)
                return response_data
        except error.HTTPError as exc:
            response_data = _load_json_body(exc)
            message = _extract_error_message(response_data) or f"Provider request failed with status {exc.code}."
            raise RuntimeError(message) from exc

    def _urlopen(self):
        proxy_url = self.settings.proxy_url
        if not proxy_url:
            return request.urlopen
        opener = request.build_opener(request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        return opener.open


def _extract_error_message(response_data: dict[str, object]) -> str | None:
    error = response_data.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return None


def _chat_completions_endpoint(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return normalized + "/chat/completions"


def _load_json_body(response) -> dict[str, object]:
    try:
        raw_body = response.read()
    except Exception:
        return {}
    if not raw_body:
        return {}
    try:
        return json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _extract_image_bytes(response_data: dict[str, object]) -> bytes:
    for item in response_data.get("data", []):
        if isinstance(item, dict):
            b64_json = item.get("b64_json")
            if isinstance(b64_json, str) and b64_json:
                return base64.b64decode(b64_json)

    choices = response_data.get("choices", [])
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue

            images = message.get("images", [])
            if isinstance(images, list):
                for image in images:
                    image_bytes = _decode_image_url_entry(image)
                    if image_bytes is not None:
                        return image_bytes

            content = message.get("content", [])
            if isinstance(content, list):
                for part in content:
                    image_bytes = _decode_image_url_entry(part)
                    if image_bytes is not None:
                        return image_bytes

    raise RuntimeError("Provider response did not include image data.")


def _decode_image_url_entry(entry: object) -> bytes | None:
    if not isinstance(entry, dict):
        return None
    image_url = entry.get("image_url")
    if isinstance(image_url, dict):
        url = image_url.get("url")
    else:
        url = image_url
    if isinstance(url, str) and url.startswith("data:") and "," in url:
        return base64.b64decode(url.split(",", 1)[1])
    return None


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def default_settings_path() -> Path:
    from sts_draw.user_settings import default_settings_path as user_default_settings_path

    return user_default_settings_path()


def _line_art_cache_path(source_bytes: bytes) -> Path:
    return default_settings_path().parent / "line_art_cache" / f"{_cache_key(source_bytes)}.png"


def _cache_key(source_bytes: bytes) -> str:
    return hashlib.sha256(source_bytes).hexdigest()


def _guess_mime_type(image_path: str) -> str:
    suffix = Path(image_path).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


def _probe_png_size(image_bytes: bytes) -> tuple[int, int]:
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        width = int.from_bytes(image_bytes[16:20], "big")
        height = int.from_bytes(image_bytes[20:24], "big")
        return width, height
    return 0, 0
