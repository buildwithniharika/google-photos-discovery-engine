"""Versioned prompt files: prompts/{name}_v{version}.md. The id is stored with every output."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str

    @property
    def id(self) -> str:
        return f"{self.name}_v{self.version}"


def load_prompt(name: str, version: int, prompts_dir: Path) -> Prompt:
    path = prompts_dir / f"{name}_v{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Prompt file is empty: {path}")
    return Prompt(name=name, version=version, text=text)
