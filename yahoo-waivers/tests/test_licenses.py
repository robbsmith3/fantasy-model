"""No AGPL anywhere in the installed dependency tree (runtime and dev)."""

import re
from importlib import metadata

ROOTS = ["requests", "nflreadpy", "polars", "python-dotenv", "pytest"]


def _closure(roots):
    seen, todo = {}, list(roots)
    while todo:
        name = re.split(r"[ ;<>=!\[~(]", todo.pop())[0].lower()
        if not name or name in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen[name] = dist
        todo += [r for r in (dist.requires or []) if "extra ==" not in r]
    return seen


def _license(dist) -> str:
    m = dist.metadata
    parts = [m.get("License-Expression") or "", m.get("License") or ""]
    parts += [c for c in m.get_all("Classifier") or [] if c.startswith("License")]
    return " ".join(parts)


def test_no_agpl_dependencies():
    found = _closure(ROOTS)
    assert "requests" in found and "polars" in found
    bad = {n: _license(d) for n, d in found.items() if "AGPL" in _license(d)
           or "Affero" in _license(d)}  # fmt: skip
    assert not bad, bad
