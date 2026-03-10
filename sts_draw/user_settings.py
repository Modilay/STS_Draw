from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from sts_draw.models import DEFAULT_DRAW_MOUSE_BUTTON, DEFAULT_HOTKEYS, ExecutionSession


@dataclass(slots=True)
class UserSettings:
    hotkeys: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))
    draw_mouse_button: str = DEFAULT_DRAW_MOUSE_BUTTON

    @classmethod
    def from_session(cls, session: ExecutionSession) -> "UserSettings":
        return cls(hotkeys=dict(session.hotkeys), draw_mouse_button=_normalize_mouse_button(session.draw_mouse_button))


class UserSettingsStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_settings_path()

    def load(self) -> UserSettings:
        if not self.path.exists():
            return UserSettings()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return UserSettings()
        return _settings_from_payload(payload)

    def save(self, settings: UserSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "hotkeys": dict(settings.hotkeys),
                    "draw_mouse_button": _normalize_mouse_button(settings.draw_mouse_button),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def default_settings_path() -> Path:
    if os.name == "nt":
        base_dir = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        base_dir = Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base_dir / "STS_Draw" / "settings.json"


def _settings_from_payload(payload: object) -> UserSettings:
    if not isinstance(payload, dict):
        return UserSettings()

    hotkeys = dict(DEFAULT_HOTKEYS)
    raw_hotkeys = payload.get("hotkeys")
    if isinstance(raw_hotkeys, dict):
        for action in DEFAULT_HOTKEYS:
            value = raw_hotkeys.get(action)
            if isinstance(value, str) and value.strip():
                hotkeys[action] = value.strip().lower()

    return UserSettings(
        hotkeys=hotkeys,
        draw_mouse_button=_normalize_mouse_button(payload.get("draw_mouse_button")),
    )


def _normalize_mouse_button(value: object) -> str:
    if isinstance(value, str) and value.lower() == "right":
        return "right"
    return DEFAULT_DRAW_MOUSE_BUTTON
