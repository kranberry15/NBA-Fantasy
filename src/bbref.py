"""Player ages from Basketball Reference season pages (only 1 request per season, cached 30 days).

BBRef asks for < 20 requests/minute; we sleep between requests and cache hard.
"""
from __future__ import annotations

import io
import time

import pandas as pd
import subprocess

from .common import RAW, norm_name

URL = "https://www.basketball-reference.com/leagues/NBA_{year}_per_game.html"


def _html(year: int, max_age_days: float = 30) -> str | None:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"bbref_per_game_{year}.html"
    if path.exists() and (time.time() - path.stat().st_mtime) < max_age_days * 86400:
        return path.read_text()
    try:
        # BBRef rejects python-requests' TLS fingerprint; curl is accepted.
        r = subprocess.run(["curl", "-sS", "-f", "-m", "30", "-A", "Mozilla/5.0", URL.format(year=year)],
                           capture_output=True, text=True, check=True)
        path.write_text(r.stdout)
        time.sleep(4)
        return r.stdout
    except Exception as e:
        e = getattr(e, "stderr", None) or e
        print(f"[WARN] Basketball Reference {year} fetch failed ({e}). Ages for that season unavailable.")
        return path.read_text() if path.exists() else None


def ages(current_season: int = 2027) -> dict[str, float]:
    """norm_name -> age during current season (BBRef age is as of Feb 1 of the season)."""
    out: dict[str, float] = {}
    for year in (current_season - 2, current_season - 1):  # older first, newer overwrites
        html = _html(year)
        if not html:
            continue
        df = pd.read_html(io.StringIO(html), attrs={"id": "per_game_stats"})[0]
        df = df[df["Player"] != "Player"]
        for name, age in zip(df["Player"], pd.to_numeric(df["Age"], errors="coerce")):
            if pd.notna(age):
                out[norm_name(str(name))] = float(age) + (current_season - year)
    return out
