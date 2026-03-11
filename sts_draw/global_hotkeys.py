from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable


@dataclass(slots=True)
class HotkeyCheckResult:
    hotkey: str
    ok: bool
    conflict_reason: str | None = None
    message: str = "active"


class GlobalHotkeyManager:
    def __init__(self) -> None:
        self._registered = False

    def check_hotkey(self, hotkey: str) -> HotkeyCheckResult:
        keyboard = self._keyboard_module()
        try:
            handle = keyboard.add_hotkey(hotkey, lambda: None)
        except Exception as exc:
            return HotkeyCheckResult(
                hotkey=hotkey,
                ok=False,
                conflict_reason="registration_failed",
                message=str(exc),
            )
        remove_hotkey = getattr(keyboard, "remove_hotkey", None)
        if callable(remove_hotkey):
            remove_hotkey(handle)
        return HotkeyCheckResult(hotkey=hotkey, ok=True)

    def register(self, bindings: dict[str, Callable[[], None]]) -> dict[str, HotkeyCheckResult]:
        keyboard = self._keyboard_module()
        try:
            results = {hotkey: self.check_hotkey(hotkey) for hotkey in bindings}
            if self._registered:
                keyboard.unhook_all_hotkeys()
                self._registered = False
        except Exception:
            raise
        for hotkey, callback in bindings.items():
            if results[hotkey].ok:
                keyboard.add_hotkey(hotkey, callback)
        self._registered = any(result.ok for result in results.values())
        return results

    def clear(self) -> None:
        if not self._registered:
            return
        try:
            keyboard = self._keyboard_module()
        except RuntimeError:
            self._registered = False
            return
        keyboard.unhook_all_hotkeys()
        self._registered = False

    @staticmethod
    def _keyboard_module():
        try:
            import keyboard
        except ImportError as exc:
            raise RuntimeError("keyboard is not installed.") from exc
        return keyboard
