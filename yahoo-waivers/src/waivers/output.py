"""Terminal text for recommendations, always ending with Yahoo's attribution."""

from __future__ import annotations

from waivers import FOOTER
from waivers.scoring import Rec


def league_block(name: str, week: int, faab: int | None, recs: list[Rec], notes: list[str]) -> str:
    head = f"{name} | week {week}" + (f" | FAAB ${faab}" if faab is not None else "")
    lines = [head, "-" * len(head)]
    if not recs:
        lines.append("No add improves your lineup over the next three weeks.")
    for r in recs:
        p = r.player
        drop = r.drop.name if r.drop else "nobody (open spot)"
        bid = f"${r.bid_low}-${r.bid_high}" if r.bid_high else "$0"
        lines.append(f"{r.priority}. {p.name} ({p.position}, {p.team or 'FA'})  bid {bid}  "
                     f"drop {drop}")  # fmt: skip
        lines.append(f"   {r.rationale}")
    lines += [f"note: {n}" for n in notes]
    return "\n".join(lines)


def render(blocks: list[str]) -> str:
    return "\n\n".join([*blocks, FOOTER]) + "\n"
