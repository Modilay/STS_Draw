import sys
import unittest

from sts_draw.global_hotkeys import GlobalHotkeyManager, HotkeyCheckResult


class FakeKeyboardModule:
    def __init__(self) -> None:
        self.added: list[str] = []
        self.unhook_calls = 0
        self.removed: list[int] = []
        self.fail_hotkeys: set[str] = set()
        self.next_handle = 0

    def add_hotkey(self, hotkey: str, _callback) -> int:
        if hotkey in self.fail_hotkeys:
            raise RuntimeError(f"hotkey conflict: {hotkey}")
        self.added.append(hotkey)
        self.next_handle += 1
        return self.next_handle

    def remove_hotkey(self, handle: int) -> None:
        self.removed.append(handle)

    def unhook_all_hotkeys(self) -> None:
        self.unhook_calls += 1


class GlobalHotkeyManagerTests(unittest.TestCase):
    def test_check_hotkey_reports_registration_failure(self) -> None:
        keyboard = FakeKeyboardModule()
        keyboard.fail_hotkeys.add("ctrl+alt+s")
        manager = GlobalHotkeyManager()

        with unittest.mock.patch.dict(sys.modules, {"keyboard": keyboard}):
            result = manager.check_hotkey("ctrl+alt+s")

        self.assertEqual(
            result,
            HotkeyCheckResult(
                hotkey="ctrl+alt+s",
                ok=False,
                conflict_reason="registration_failed",
                message="hotkey conflict: ctrl+alt+s",
            ),
        )

    def test_check_hotkey_uses_temporary_registration_and_cleanup(self) -> None:
        keyboard = FakeKeyboardModule()
        manager = GlobalHotkeyManager()

        with unittest.mock.patch.dict(sys.modules, {"keyboard": keyboard}):
            result = manager.check_hotkey("ctrl+alt+c")

        self.assertTrue(result.ok)
        self.assertEqual(keyboard.added, ["ctrl+alt+c"])
        self.assertEqual(keyboard.removed, [1])

    def test_register_replaces_existing_hotkeys(self) -> None:
        keyboard = FakeKeyboardModule()
        manager = GlobalHotkeyManager()

        with unittest.mock.patch.dict(sys.modules, {"keyboard": keyboard}):
            manager.register({"ctrl+alt+c": lambda: None})
            manager.register({"ctrl+alt+s": lambda: None})

        self.assertEqual(keyboard.unhook_calls, 1)
        self.assertEqual(keyboard.added, ["ctrl+alt+c", "ctrl+alt+c", "ctrl+alt+s", "ctrl+alt+s"])

    def test_register_skips_failed_bindings_and_returns_results(self) -> None:
        keyboard = FakeKeyboardModule()
        keyboard.fail_hotkeys.add("ctrl+alt+s")
        manager = GlobalHotkeyManager()

        with unittest.mock.patch.dict(sys.modules, {"keyboard": keyboard}):
            results = manager.register(
                {
                    "ctrl+alt+c": lambda: None,
                    "ctrl+alt+s": lambda: None,
                }
            )

        self.assertTrue(results["ctrl+alt+c"].ok)
        self.assertFalse(results["ctrl+alt+s"].ok)
        self.assertEqual(results["ctrl+alt+s"].conflict_reason, "registration_failed")


if __name__ == "__main__":
    unittest.main()
