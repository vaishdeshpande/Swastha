"""Loader for config/languages.yaml — the single source of truth for
per-language TTS voice, model, and greeting. Add a new language there only;
no agent code needs to change."""

import logging
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "languages.yaml"

DEFAULT_LANG = "hi-IN"


@lru_cache(maxsize=1)
def _load_all_languages() -> dict:
    logger.debug("language_config: loading languages.yaml from %s", CONFIG_PATH)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["languages"]


def load_language_config(lang_code: str) -> dict:
    """Returns {name, tts_voice, tts_model, greeting, enabled} for lang_code.
    Falls back to DEFAULT_LANG if lang_code is missing or disabled."""
    languages = _load_all_languages()
    config = languages.get(lang_code)
    if not config or not config.get("enabled", False):
        logger.info("language_config: lang_code=%s not found/disabled, falling back to %s", lang_code, DEFAULT_LANG)
        config = languages[DEFAULT_LANG]
    return config
