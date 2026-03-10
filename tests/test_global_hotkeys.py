import sys
import unittest

from sts_draw.global_hotkeys import GlobalHotkeyManager


class FakeKeyboardModule:
    def __init__(self) -> None:
        self.added: list[str] = []
        self.unhook_calls = 0

    def add_hotkey(self, hotkey: str, _callback) -> None:
        self.added.append(hotkey)

    def unhook_all_hotkeys(self) -> None:
        self.unhook_calls += 1


class GlobalHotkeyManagerTests(unittest.TestCase):
    def test_register_replaces_existing_hotkeys(self) -> None:
        keyboard = FakeKeyboardModule()
        manager = GlobalHotkeyManager()

        with unittest.mock.patch.dict(sys.modules, {"keyboard": keyboard}):
            manager.register({"ctrl+alt+c": lambda: None})
            manager.register({"ctrl+alt+s": lambda: None})

        self.assertEqual(keyboard.unhook_calls, 1)
        self.assertEqual(keyboard.added, ["ctrl+alt+c", "ctrl+alt+s"])


if __name__ == "__main__":
    unittest.main()
