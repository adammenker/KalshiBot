from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEMO_BASE_URL = "https://external-api.demo.kalshi.co/trade-api/v2"
PROD_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"
POLYMARKET_DATA_BASE_URL = "https://data-api.polymarket.com"
LOCAL_LLM_BASE_URL = "http://localhost:11434"
LOCAL_LLM_MODEL = "llama3.1:8b"


@dataclass(frozen=True)
class KalshiConfig:
    api_key_id: str
    private_key_path: Path
    base_url: str
    environment: str


@dataclass(frozen=True)
class PolymarketConfig:
    gamma_base_url: str
    clob_base_url: str
    data_base_url: str


@dataclass(frozen=True)
class LocalLLMConfig:
    base_url: str
    model: str
    timeout_seconds: float


def load_config() -> KalshiConfig:
    load_dotenv()

    environment = os.getenv("KALSHI_ENV", "demo").strip().lower()
    if environment not in {"demo", "prod", "production"}:
        raise ValueError("KALSHI_ENV must be one of: demo, prod, production")

    api_key_id = os.getenv("KALSHI_API_KEY_ID", "").strip()
    private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()

    missing = [
        name
        for name, value in {
            "KALSHI_API_KEY_ID": api_key_id,
            "KALSHI_PRIVATE_KEY_PATH": private_key_path,
        }.items()
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    if api_key_id == "replace-with-your-kalshi-api-key-id":
        raise ValueError("Set KALSHI_API_KEY_ID in .env to the API key ID shown by Kalshi")

    default_base_url = DEMO_BASE_URL if environment == "demo" else PROD_BASE_URL
    base_url = os.getenv("KALSHI_BASE_URL", default_base_url).strip().rstrip("/")

    return KalshiConfig(
        api_key_id=api_key_id,
        private_key_path=Path(private_key_path).expanduser(),
        base_url=base_url,
        environment="prod" if environment == "production" else environment,
    )


def load_polymarket_config() -> PolymarketConfig:
    load_dotenv()

    return PolymarketConfig(
        gamma_base_url=os.getenv("POLYMARKET_GAMMA_BASE_URL", POLYMARKET_GAMMA_BASE_URL)
        .strip()
        .rstrip("/"),
        clob_base_url=os.getenv("POLYMARKET_CLOB_BASE_URL", POLYMARKET_CLOB_BASE_URL)
        .strip()
        .rstrip("/"),
        data_base_url=os.getenv("POLYMARKET_DATA_BASE_URL", POLYMARKET_DATA_BASE_URL)
        .strip()
        .rstrip("/"),
    )


def load_local_llm_config() -> LocalLLMConfig:
    load_dotenv()

    return LocalLLMConfig(
        base_url=os.getenv("LOCAL_LLM_BASE_URL", LOCAL_LLM_BASE_URL).strip().rstrip("/"),
        model=os.getenv("LOCAL_LLM_MODEL", LOCAL_LLM_MODEL).strip(),
        timeout_seconds=float(os.getenv("LOCAL_LLM_TIMEOUT_SECONDS", "30")),
    )
