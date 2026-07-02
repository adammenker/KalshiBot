from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from kalshibot.discovery.models import KalshiDiscoveryMarket, PolymarketDiscoveryMarket


class EmbeddingUnavailable(RuntimeError):
    """Raised when optional embedding dependencies are not installed."""


@dataclass
class KalshiEmbeddingIndex:
    tickers: list[str]
    titles: list[str]
    embeddings: Any
    encoder: Any
    faiss_index: Any | None = None

    @classmethod
    def build(
        cls,
        kalshi_markets: list[KalshiDiscoveryMarket],
        *,
        encoder: Any | None = None,
    ) -> KalshiEmbeddingIndex:
        encoder = encoder or SentenceTransformerEncoder()
        titles = [market.full_title for market in kalshi_markets]
        embeddings = normalized_embeddings(encoder, titles)
        return cls(
            tickers=[market.ticker for market in kalshi_markets],
            titles=titles,
            embeddings=embeddings,
            encoder=encoder,
            faiss_index=build_faiss_index(embeddings),
        )

    @classmethod
    def load(cls, path: Path, *, encoder: Any | None = None) -> KalshiEmbeddingIndex:
        np = import_numpy()
        metadata_path = path / "metadata.json"
        embeddings_path = path / "embeddings.npy"
        if not metadata_path.exists() or not embeddings_path.exists():
            raise FileNotFoundError(path)
        metadata = json.loads(metadata_path.read_text())
        embeddings = np.load(embeddings_path)
        return cls(
            tickers=list(metadata["tickers"]),
            titles=list(metadata["titles"]),
            embeddings=embeddings,
            encoder=encoder or SentenceTransformerEncoder(),
            faiss_index=build_faiss_index(embeddings),
        )

    def save(self, path: Path) -> None:
        np = import_numpy()
        path.mkdir(parents=True, exist_ok=True)
        metadata = {"tickers": self.tickers, "titles": self.titles}
        (path / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True))
        np.save(path / "embeddings.npy", self.embeddings)
        if self.faiss_index is not None:
            try:
                import faiss  # type: ignore[import-not-found]
            except ImportError:
                return
            faiss.write_index(self.faiss_index, str(path / "faiss.index"))

    def add_markets(self, kalshi_markets: list[KalshiDiscoveryMarket]) -> bool:
        existing = set(self.tickers)
        new_markets = [market for market in kalshi_markets if market.ticker not in existing]
        if not new_markets:
            return False
        np = import_numpy()
        new_titles = [market.full_title for market in new_markets]
        new_embeddings = normalized_embeddings(self.encoder, new_titles)
        self.tickers.extend(market.ticker for market in new_markets)
        self.titles.extend(new_titles)
        self.embeddings = np.vstack([self.embeddings, new_embeddings])
        if self.faiss_index is not None:
            self.faiss_index.add(new_embeddings.astype("float32"))
        else:
            self.faiss_index = build_faiss_index(self.embeddings)
        return True

    def search(
        self,
        polymarket_market: PolymarketDiscoveryMarket,
        kalshi_markets: list[KalshiDiscoveryMarket],
    ) -> list[tuple[KalshiDiscoveryMarket, float]]:
        market_by_ticker = {market.ticker: market for market in kalshi_markets}
        query = normalized_embeddings(self.encoder, [polymarket_market.title])
        if self.faiss_index is not None:
            scores, indexes = self.faiss_index.search(query.astype("float32"), len(self.tickers))
            ranked_indexes = list(zip(indexes[0].tolist(), scores[0].tolist(), strict=True))
        else:
            ranked_indexes = numpy_ranked_indexes(self.embeddings, query[0])

        ranked: list[tuple[KalshiDiscoveryMarket, float]] = []
        seen: set[str] = set()
        for index, score in ranked_indexes:
            if index < 0 or index >= len(self.tickers):
                continue
            ticker = self.tickers[index]
            market = market_by_ticker.get(ticker)
            if market is None or ticker in seen:
                continue
            seen.add(ticker)
            ranked.append((market, float(score)))
        return ranked


class SentenceTransformerEncoder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingUnavailable(
                "Install the discovery extras to enable embedding search: "
                "pip install -e '.[discovery]'"
            ) from exc
        self.model = SentenceTransformer(model_name)

    def encode(self, titles: list[str]) -> Any:
        return self.model.encode(titles, convert_to_numpy=True, show_progress_bar=False)


def load_or_build_kalshi_embedding_index(
    kalshi_markets: list[KalshiDiscoveryMarket],
    *,
    index_path: Path | None,
) -> KalshiEmbeddingIndex | None:
    try:
        if index_path is None:
            return KalshiEmbeddingIndex.build(kalshi_markets)
        try:
            index = KalshiEmbeddingIndex.load(index_path)
        except FileNotFoundError:
            index = KalshiEmbeddingIndex.build(kalshi_markets)
            index.save(index_path)
            return index
        changed = index.add_markets(kalshi_markets)
        if changed:
            index.save(index_path)
        return index
    except EmbeddingUnavailable:
        return None


def normalized_embeddings(encoder: Any, titles: list[str]) -> Any:
    np = import_numpy()
    embeddings = np.asarray(encoder.encode(titles), dtype="float32")
    if embeddings.ndim == 1:
        embeddings = embeddings.reshape(1, -1)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return embeddings / norms


def build_faiss_index(embeddings: Any) -> Any | None:
    try:
        import faiss  # type: ignore[import-not-found]
    except ImportError:
        return None
    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings.astype("float32"))
    return index


def numpy_ranked_indexes(embeddings: Any, query_embedding: Any) -> list[tuple[int, float]]:
    np = import_numpy()
    scores = embeddings @ query_embedding
    indexes = np.argsort(-scores)
    return [(int(index), float(scores[index])) for index in indexes]


def import_numpy() -> Any:
    try:
        import numpy as np
    except ImportError as exc:
        raise EmbeddingUnavailable(
            "Install the discovery extras to enable embedding search: pip install -e '.[discovery]'"
        ) from exc
    return np
