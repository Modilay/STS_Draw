from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

from sts_draw.models import LineArtResult


DEFAULT_PROMPT = (
    "Convert this image into clean black-and-white line art suitable for redrawing with a mouse. "
    "Remove shading and color, preserve major contours, and keep the background white."
)


@dataclass(slots=True)
class GeminiSettings:
    api_key: str | None = None
    model: str = "gemini-2.0-flash-exp"


class GeminiClient:
    def __init__(self, settings: GeminiSettings | None = None) -> None:
        self.settings = settings or GeminiSettings(api_key=os.getenv("GEMINI_API_KEY"))

    def generate_line_art(self, image_path: str, prompt: str | None = None) -> LineArtResult:
        api_key = self.settings.api_key
        if not api_key:
            raise RuntimeError("Gemini API key is missing.")

        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("google-genai is not installed.") from exc

        image_bytes = Path(image_path).read_bytes()
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=self.settings.model,
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt or DEFAULT_PROMPT},
                        {
                            "inline_data": {
                                "mime_type": _guess_mime_type(image_path),
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
        )
        inline_data = _extract_inline_data(response)
        width, height = _probe_png_size(inline_data)
        return LineArtResult(
            image_bytes=inline_data,
            mime_type="image/png",
            width=width,
            height=height,
            prompt=prompt or DEFAULT_PROMPT,
        )


def _extract_inline_data(response) -> bytes:
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            if inline_data and getattr(inline_data, "data", None):
                data = inline_data.data
                return data if isinstance(data, bytes) else base64.b64decode(data)
    raise RuntimeError("Gemini response did not include image data.")


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
