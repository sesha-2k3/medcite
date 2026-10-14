"""Versioned prompt files. ``name_vN.md`` is loaded as ``Prompt(name, "vN", text)``."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: str
    text: str

    @property
    def id(self) -> str:
        return f"{self.name}@{self.version}"


@cache
def load_prompt(name: str, version: str) -> Prompt:
    text = resources.files(__name__).joinpath(f"{name}_{version}.md").read_text("utf-8")
    return Prompt(name=name, version=version, text=text.strip())
