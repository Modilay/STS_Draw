import base64
import os
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from sts_draw.image_generation_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    OpenAICompatibleClient,
    OpenAICompatibleSettings,
)


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAADCAIAAAA2iEnWAAAAFUlEQVR4nGP8//8/AwMDAwMjI2MAAA8+AgT6j/wUAAAAAElFTkSuQmCC"
)


class FakeHttpResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def read(self) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class OpenAICompatibleClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(__file__).resolve().parent / "_tmp_client_tests"
        self.temp_dir.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        for path in self.temp_dir.glob("*"):
            path.unlink()

    def test_defaults_to_openrouter_configuration(self) -> None:
        client = OpenAICompatibleClient(settings=OpenAICompatibleSettings())

        self.assertEqual(client.settings.base_url, DEFAULT_BASE_URL)
        self.assertEqual(client.settings.model, DEFAULT_MODEL)
        self.assertEqual(client.settings.base_url, "https://openrouter.ai/api/v1")

    def test_requires_api_key(self) -> None:
        client = OpenAICompatibleClient(settings=OpenAICompatibleSettings(api_key=None))

        with self.assertRaisesRegex(RuntimeError, "API key is missing"):
            client.generate_line_art("image.png")

    def test_reads_proxy_from_environment_when_setting_missing(self) -> None:
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:7890"}, clear=False):
            client = OpenAICompatibleClient()

        self.assertEqual(client.settings.proxy_url, "http://127.0.0.1:7890")

    def test_sends_openrouter_chat_completion_request(self) -> None:
        image_path = self.temp_dir / "source.png"
        image_path.write_bytes(PNG_BYTES)
        captured_request = {}
        response_bytes = (
            b'{"choices":[{"message":{"content":[{"type":"image_url",'
            b'"image_url":{"url":"data:image/png;base64,'
            + base64.b64encode(PNG_BYTES)
            + b'"}}]}}]}'
        )

        def fake_urlopen(request):
            captured_request["full_url"] = request.full_url
            captured_request["headers"] = dict(request.header_items())
            captured_request["body"] = request.data
            return FakeHttpResponse(response_bytes)

        client = OpenAICompatibleClient(
            settings=OpenAICompatibleSettings(
                api_key="test-key",
                model="google/gemini-2.0-flash-exp:free",
                base_url="https://openrouter.ai/api/v1/",
            )
        )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = client.generate_line_art(str(image_path))

        self.assertEqual(captured_request["full_url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(captured_request["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(result.size, (2, 3))
        self.assertEqual(result.prompt, DEFAULT_PROMPT)

        payload = captured_request["body"].decode("utf-8")
        self.assertIn('"model": "google/gemini-2.0-flash-exp:free"', payload)
        self.assertIn('"type": "text"', payload)
        self.assertIn('"type": "image_url"', payload)
        self.assertIn('"image_url": {"url": "data:image/png;base64,', payload)
        self.assertIn(base64.b64encode(PNG_BYTES).decode("ascii"), payload)

    def test_uses_explicit_proxy_url_when_present(self) -> None:
        image_path = self.temp_dir / "source.png"
        image_path.write_bytes(PNG_BYTES)
        response_bytes = (
            b'{"choices":[{"message":{"content":[{"type":"image_url",'
            b'"image_url":{"url":"data:image/png;base64,'
            + base64.b64encode(PNG_BYTES)
            + b'"}}]}}]}'
        )
        captured = {}

        class FakeOpener:
            def open(self, request):
                captured["full_url"] = request.full_url
                return FakeHttpResponse(response_bytes)

        def fake_build_opener(proxy_handler):
            captured["proxy_handler"] = proxy_handler
            return FakeOpener()

        client = OpenAICompatibleClient(
            settings=OpenAICompatibleSettings(
                api_key="test-key",
                proxy_url="http://127.0.0.1:7890",
            )
        )

        with patch("urllib.request.build_opener", side_effect=fake_build_opener):
            client.generate_line_art(str(image_path))

        self.assertEqual(captured["full_url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertIsNotNone(captured["proxy_handler"])

    def test_accepts_full_chat_completions_url_without_double_appending_endpoint(self) -> None:
        image_path = self.temp_dir / "source.png"
        image_path.write_bytes(PNG_BYTES)
        captured_request = {}
        response_bytes = (
            b'{"choices":[{"message":{"content":[{"type":"image_url",'
            b'"image_url":{"url":"data:image/png;base64,'
            + base64.b64encode(PNG_BYTES)
            + b'"}}]}}]}'
        )

        def fake_urlopen(request):
            captured_request["full_url"] = request.full_url
            return FakeHttpResponse(response_bytes)

        client = OpenAICompatibleClient(
            settings=OpenAICompatibleSettings(
                api_key="test-key",
                model="google/gemini-2.0-flash-exp:free",
                base_url="https://openrouter.ai/api/v1/chat/completions",
            )
        )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.generate_line_art(str(image_path))

        self.assertEqual(captured_request["full_url"], "https://openrouter.ai/api/v1/chat/completions")

    def test_raises_for_provider_errors(self) -> None:
        image_path = self.temp_dir / "source.png"
        image_path.write_bytes(PNG_BYTES)
        client = OpenAICompatibleClient(
            settings=OpenAICompatibleSettings(api_key="test-key", base_url="https://openrouter.ai/api/v1")
        )

        def fake_urlopen(_request):
            return FakeHttpResponse(b'{"error":{"message":"provider rejected request"}}', status=429)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaisesRegex(RuntimeError, "provider rejected request"):
                client.generate_line_art(str(image_path))

    def test_raises_for_http_error_response_body(self) -> None:
        image_path = self.temp_dir / "source.png"
        image_path.write_bytes(PNG_BYTES)
        client = OpenAICompatibleClient(
            settings=OpenAICompatibleSettings(api_key="test-key", base_url="https://openrouter.ai/api/v1")
        )

        def fake_urlopen(request):
            raise HTTPError(
                request.full_url,
                403,
                "Forbidden",
                hdrs=None,
                fp=BytesIO(b'{"error":{"message":"Invalid credentials"}}'),
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaisesRegex(RuntimeError, "Invalid credentials"):
                client.generate_line_art(str(image_path))

    def test_raises_when_response_has_no_image(self) -> None:
        image_path = self.temp_dir / "source.png"
        image_path.write_bytes(PNG_BYTES)
        client = OpenAICompatibleClient(settings=OpenAICompatibleSettings(api_key="test-key"))

        with patch("urllib.request.urlopen", return_value=FakeHttpResponse(b'{"choices": []}')):
            with self.assertRaisesRegex(RuntimeError, "did not include image data"):
                client.generate_line_art(str(image_path))


if __name__ == "__main__":
    unittest.main()
