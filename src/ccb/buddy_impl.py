"""Buddy system for ccb-py.

Virtual coding companion with personality, state, and animations.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


# ASCII art pets
PETS = {
    "cat": {
        "idle": [
            r"  /\_/\  ",
            r" ( o.o ) ",
            r"  > ^ <  ",
        ],
        "happy": [
            r"  /\_/\  ",
            r" ( ^.^ ) ",
            r"  > ~ <  ",
        ],
        "sleeping": [
            r"  /\_/\  ",
            r" ( -.- ) ",
            r"  > z <  ",
        ],
        "thinking": [
            r"  /\_/\  ",
            r" ( o.? ) ",
            r"  > . <  ",
        ],
    },
    "dog": {
        "idle": [
            r" /^ ^\  ",
            r"/ 0 0 \ ",
            r"V\ Y /V ",
            r"  / - \  ",
        ],
        "happy": [
            r" /^ ^\  ",
            r"/ ^ ^ \ ",
            r"V\ W /V ",
            r"  / ~ \  ",
        ],
        "sleeping": [
            r" /v v\  ",
            r"/ - - \ ",
            r"V\ o /V ",
            r"  / z \  ",
        ],
    },
    "duck": {
        "idle": [
            r"  __     ",
            r" /o \>   ",
            r"(_,_/    ",
            r" || ||   ",
        ],
        "happy": [
            r"  __     ",
            r" /^ \>   ",
            r"(_,_/    ",
            r" |  |    ",
        ],
    },
    "robot": {
        "idle": [
            r" [o_o]  ",
            r"/|   |\ ",
            r"  d b    ",
        ],
        "happy": [
            r" [^_^]  ",
            r"/|   |\ ",
            r"  d b    ",
        ],
        "thinking": [
            r" [o_?]  ",
            r"/|   |\ ",
            r"  d b    ",
        ],
    },
}

BUDDY_MESSAGES = {
    "greeting": [
        "Hey! Ready to code?",
        "Let's build something!",
        "What are we working on?",
    ],
    "idle": [
        "...",
        "*stretches*",
        "*looks around*",
        "Need help?",
    ],
    "happy": [
        "Nice work!",
        "That looks good!",
        "Great progress!",
    ],
    "error": [
        "Oops! Let me help.",
        "Hmm, that's tricky.",
        "We'll figure it out!",
    ],
    "farewell": [
        "See you later!",
        "Good session!",
        "Bye bye!",
    ],
}


@dataclass
class BuddyState:
    enabled: bool = False
    pet: str = "cat"
    name: str = "Buddy"
    mood: str = "idle"  # idle, happy, sleeping, thinking
    xp: int = 0
    level: int = 1
    messages_helped: int = 0
    last_interaction: float = 0.0

    def gain_xp(self, amount: int = 10) -> bool:
        """Add XP, returns True if leveled up."""
        self.xp += amount
        needed = self.level * 100
        if self.xp >= needed:
            self.xp -= needed
            self.level += 1
            return True
        return False


class Buddy:
    """Virtual coding companion."""

    def __init__(self) -> None:
        self._state = BuddyState()
        self._config_path = Path.home() / ".claude" / "buddy.json"
        self._load()

    def _load(self) -> None:
        if self._config_path.exists():
            try:
                data = json.loads(self._config_path.read_text())
                for k, v in data.items():
                    if hasattr(self._state, k):
                        setattr(self._state, k, v)
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(json.dumps(asdict(self._state), indent=2))

    @property
    def state(self) -> BuddyState:
        return self._state

    @property
    def enabled(self) -> bool:
        return self._state.enabled

    def toggle(self) -> bool:
        self._state.enabled = not self._state.enabled
        if self._state.enabled:
            self._state.mood = "idle"
        self._save()
        return self._state.enabled

    def set_pet(self, pet: str) -> bool:
        if pet not in PETS:
            return False
        self._state.pet = pet
        self._save()
        return True

    def set_name(self, name: str) -> None:
        self._state.name = name
        self._save()

    def get_art(self) -> list[str]:
        pet_art = PETS.get(self._state.pet, PETS["cat"])
        mood_art = pet_art.get(self._state.mood, pet_art.get("idle", []))
        return mood_art

    def get_message(self) -> str:
        msgs = BUDDY_MESSAGES.get(self._state.mood, BUDDY_MESSAGES["idle"])
        return random.choice(msgs)

    def render(self) -> str:
        """Render buddy with art and message."""
        if not self._state.enabled:
            return ""
        art = self.get_art()
        msg = self.get_message()
        name = self._state.name
        level = f"Lv.{self._state.level}"
        lines = [f"  {name} ({level})"]
        lines.extend(f"  {line}" for line in art)
        lines.append(f"  \"{msg}\"")
        return "\n".join(lines)

    def on_message(self) -> None:
        self._state.messages_helped += 1
        self._state.last_interaction = time.time()
        self._state.mood = "happy"
        self._state.gain_xp(5)
        self._save()

    def on_error(self) -> None:
        self._state.mood = "thinking"
        self._save()

    def on_idle(self) -> None:
        if time.time() - self._state.last_interaction > 300:
            self._state.mood = "sleeping"
        else:
            self._state.mood = "idle"

    def on_thinking(self) -> None:
        self._state.mood = "thinking"

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._state.enabled,
            "pet": self._state.pet,
            "name": self._state.name,
            "mood": self._state.mood,
            "level": self._state.level,
            "xp": self._state.xp,
            "xp_needed": self._state.level * 100,
            "messages_helped": self._state.messages_helped,
            "available_pets": list(PETS.keys()),
        }


# Module singleton
_buddy: Buddy | None = None


def get_buddy() -> Buddy:
    global _buddy
    if _buddy is None:
        _buddy = Buddy()
    return _buddy
