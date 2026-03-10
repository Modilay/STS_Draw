from __future__ import annotations

from collections.abc import Callable


class GlobalHotkeyManager:
    def __init__(self) -> None:
        self._registered = False

    def register(self, bindings: dict[str, Callable[[], None]]) -> None:
        try:
            import keyboard
        except ImportError as exc:
            raise RuntimeError("keyboard is not installed.") from exc

        if self._registered:
            keyboard.unhook_all_hotkeys()
            self._registered = False
        for hotkey, callback in bindings.items():
            keyboard.add_hotkey(hotkey, callback)
        self._registered = True

    def clear(self) -> None:
        if not self._registered:
            return
        try:
            import keyboard
        except ImportError:
            self._registered = False
            return
        keyboard.unhook_all_hotkeys()
        self._registered = False
