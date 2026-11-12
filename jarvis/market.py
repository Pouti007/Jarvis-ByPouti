"""Live market data via the Finnhub API (free tier).

Get a free API key at https://finnhub.io/register and put it in config.yaml
under `finnhub: api_key`. Without a key, get_quote returns a helpful notice
instead of raising.
"""

from __future__ import annotations

import requests

FINNHUB_BASE = "https://finnhub.io/api/v1"

# Finnhub's free tier doesn't quote raw indices, so map common index names to
# liquid ETF proxies that it *does* quote.
INDEX_PROXIES = {
    "s&p 500": ("SPY", "S&P 500 (SPY)"),
    "s&p": ("SPY", "S&P 500 (SPY)"),
    "sp500": ("SPY", "S&P 500 (SPY)"),
    "dow": ("DIA", "Dow Jones (DIA)"),
    "dow jones": ("DIA", "Dow Jones (DIA)"),
    "nasdaq": ("QQQ", "Nasdaq 100 (QQQ)"),
    "russell 2000": ("IWM", "Russell 2000 (IWM)"),
}


def _get(path: str, params: dict, api_key: str, timeout: int = 8) -> dict:
    resp = requests.get(f"{FINNHUB_BASE}{path}", params={**params, "token": api_key}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _resolve_symbol(query: str, api_key: str) -> tuple[str, str]:
    """Return (symbol, display_name) for a ticker, company name, or index."""
    key = query.strip().lower()
    if key in INDEX_PROXIES:
        return INDEX_PROXIES[key]

    raw = query.strip().upper()
    # A clean 1-5 letter token is almost certainly already a ticker.
    if raw.isalpha() and 1 <= len(raw) <= 5:
        return raw, raw

    # Otherwise resolve the company name to a symbol via search.
    data = _get("/search", {"q": query}, api_key)
    results = data.get("result", [])
    if results:
        best = results[0]
        return best["symbol"], best.get("description", best["symbol"])
    return raw, raw


def get_quote(query: str, api_key: str) -> str:
    """Look up a live quote for a ticker, company name, or major US index."""
    if not api_key:
        return (
            "I don't have a Finnhub API key yet. Add a free one from finnhub.io under "
            "'finnhub: api_key' in config.yaml, and I'll be able to pull live quotes."
        )

    symbol, name = _resolve_symbol(query, api_key)
    q = _get("/quote", {"symbol": symbol}, api_key)

    current = q.get("c")
    if not current:  # 0 or None => no data for this symbol
        return f"I couldn't find live quote data for '{query}' (symbol {symbol})."

    change = q.get("d") or 0.0
    pct = q.get("dp") or 0.0
    sign = "+" if change >= 0 else ""
    return (
        f"{name}: ${current:,.2f}, {sign}{change:,.2f} ({sign}{pct:.2f}%) today. "
        f"Day range ${q.get('l', 0):,.2f}–${q.get('h', 0):,.2f}, previous close ${q.get('pc', 0):,.2f}."
    )
