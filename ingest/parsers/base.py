from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Set


@dataclass(frozen=True)
class Parser:
    name: str
    required: frozenset          # columns that identify this export
    parse: Callable              # (path, rows) -> list[Transaction]

    def detect(self, cols: Set[str]) -> bool:
        return self.required <= cols


def evidence(path, row_no: int) -> str:
    return f"{path.name}:row {row_no}"
