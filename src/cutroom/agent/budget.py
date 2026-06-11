"""Budget ledger: prices every tool result so the agent always knows what it can still afford.

Costs are denominated in characters of tool output — a cheap, deterministic proxy
for context tokens that needs no tokenizer.
"""

from __future__ import annotations


class Ledger:
    """Char-denominated budget for one editing session."""

    FRAME_COST = 1500  # chars charged per frame viewed (images cost context like text does)

    def __init__(self, total_chars: int = 120_000):
        self.total_chars = total_chars
        self.spent = 0
        self._by_label: dict[str, int] = {}

    def charge(self, label: str, n_chars: int) -> None:
        self.spent += n_chars
        self._by_label[label] = self._by_label.get(label, 0) + n_chars

    @property
    def remaining(self) -> int:
        return max(0, self.total_chars - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.total_chars

    @property
    def breakdown(self) -> dict[str, int]:
        """Chars charged per label, e.g. {"view_frames": 3000, "read_transcript": 812}."""
        return dict(self._by_label)

    def line(self) -> str:
        return f"[budget: {self.remaining:,}/{self.total_chars:,} chars left]"
