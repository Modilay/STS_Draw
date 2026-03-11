import json
import unittest
from pathlib import Path


class UserSettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(__file__).resolve().parent / "_tmp_settings_tests"
        self.temp_dir.mkdir(exist_ok=True)
        self.settings_path = self.temp_dir / "settings.json"

    def tearDown(self) -> None:
        for path in self.temp_dir.glob("*"):
            path.unlink(missing_ok=True)

    def test_load_returns_defaults_when_file_missing(self) -> None:
        from sts_draw.user_settings import UserSettingsStore

        settings = UserSettingsStore(self.settings_path).load()

        self.assertEqual(settings.hotkeys["calibrate"], "ctrl+alt+c")
        self.assertEqual(settings.hotkeys["start"], "ctrl+alt+d")
        self.assertEqual(settings.hotkeys["stop"], "ctrl+alt+s")
        self.assertEqual(settings.draw_mouse_button, "left")
        self.assertEqual(settings.api_key, "")
        self.assertIsNone(settings.proxy_url)
        self.assertTrue(settings.model)
        self.assertTrue(settings.base_url)

    def test_save_and_load_round_trip(self) -> None:
        from sts_draw.user_settings import UserSettings, UserSettingsStore

        store = UserSettingsStore(self.settings_path)
        store.save(
            UserSettings(
                hotkeys={
                    "calibrate": "ctrl+shift+c",
                    "start": "ctrl+shift+d",
                    "stop": "ctrl+shift+s",
                },
                draw_mouse_button="right",
                api_key="sk-test",
                proxy_url="http://127.0.0.1:7890",
                model="google/gemini-custom",
                base_url="https://openrouter.ai/api/v1",
            )
        )

        loaded = store.load()

        self.assertEqual(loaded.hotkeys["calibrate"], "ctrl+shift+c")
        self.assertEqual(loaded.hotkeys["start"], "ctrl+shift+d")
        self.assertEqual(loaded.hotkeys["stop"], "ctrl+shift+s")
        self.assertEqual(loaded.draw_mouse_button, "right")
        self.assertEqual(loaded.api_key, "sk-test")
        self.assertEqual(loaded.proxy_url, "http://127.0.0.1:7890")
        self.assertEqual(loaded.model, "google/gemini-custom")
        self.assertEqual(loaded.base_url, "https://openrouter.ai/api/v1")

    def test_invalid_file_falls_back_to_defaults(self) -> None:
        from sts_draw.user_settings import UserSettingsStore

        self.settings_path.write_text("{bad json", encoding="utf-8")

        loaded = UserSettingsStore(self.settings_path).load()

        self.assertEqual(loaded.hotkeys["stop"], "ctrl+alt+s")
        self.assertEqual(loaded.draw_mouse_button, "left")
        self.assertEqual(loaded.api_key, "")
        self.assertIsNone(loaded.proxy_url)

    def test_load_uses_defaults_for_missing_runtime_fields(self) -> None:
        from sts_draw.user_settings import UserSettingsStore

        self.settings_path.write_text(
            json.dumps(
                {
                    "hotkeys": {"stop": "ctrl+shift+s"},
                    "draw_mouse_button": "right",
                }
            ),
            encoding="utf-8",
        )

        loaded = UserSettingsStore(self.settings_path).load()

        self.assertEqual(loaded.hotkeys["stop"], "ctrl+shift+s")
        self.assertEqual(loaded.draw_mouse_button, "right")
        self.assertEqual(loaded.api_key, "")
        self.assertIsNone(loaded.proxy_url)
        self.assertTrue(loaded.model)
        self.assertTrue(loaded.base_url)


if __name__ == "__main__":
    unittest.main()
