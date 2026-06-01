"""Centralizovaný LLM helper — default provider Google Gemini (bezplatný free tier).

Všetky discovery zdroje (llm_probe, RSS/Reddit/GSC summarizácia, profesia/labor_market
extrakcia fráz) volajú `llm_complete()`. Provider je teda na JEDNOM mieste — výmena
modelu/poskytovateľa je zmena tu, nie v každom zdroji.

Auth: `GEMINI_API_KEY` (alt. `GOOGLE_API_KEY`). Kľúč sa zadarmo vygeneruje na
https://aistudio.google.com/apikey (Google účet, bez platobnej karty). Bez kľúča
`llm_complete()` vráti None → volajúci elegantne degraduje (vráti [] alebo raw tituly).

Model sa dá prepísať cez `GEMINI_MODEL` env (default: gemini-2.5-flash).
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Načítaj .env do os.environ nezávisle od poradia importov (absolútna cesta na
# koreň projektu). Idempotentné; na serveri .env neexistuje (no-op) a kľúč príde
# zo secrets/env.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


def llm_available() -> bool:
    """True ak je nakonfigurovaný API kľúč pre LLM provider."""
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def llm_complete(
    prompt: str,
    *,
    max_tokens: int = 2048,
    json_output: bool = False,
    model: str | None = None,
) -> str | None:
    """
    Vráti textovú odpoveď LLM na `prompt`, alebo None ak LLM nie je dostupný
    (chýba kľúč, nie je nainštalovaný SDK, alebo volanie zlyhá).

    `json_output=True` požiada Gemini o čistý JSON (response_mime_type) — vhodné
    pre štruktúrované odpovede. Volajúci to ešte prežene cez `parse_json_block()`.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        logger.info("GEMINI_API_KEY not set — skipping LLM call")
        return None

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.warning("google-genai not installed — run: uv add google-genai")
        return None

    try:
        client = genai.Client(api_key=api_key)
        model_name = model or DEFAULT_MODEL
        config_kwargs: dict = {"max_output_tokens": max_tokens}
        if json_output:
            config_kwargs["response_mime_type"] = "application/json"
        # Vypni "thinking" pre 2.5 modely — inak thinking tokeny zožerú output
        # budget a odrežú (truncate) JSON. Bezpečné pre gemini-2.5-*.
        if "2.5" in model_name:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        resp = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        text = (resp.text or "").strip()
        return text or None
    except Exception as e:
        logger.error("Gemini LLM call failed: %s", e)
        return None


def parse_json_block(text: str | None):
    """
    Vyparsuje JSON z LLM odpovede — toleruje markdown code-fence (```json ... ```).
    Vráti parsovaný objekt, alebo None ak sa nedá rozparsovať.
    """
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("LLM JSON parse failed: %s", e)
        return None
