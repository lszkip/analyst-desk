"""Search verified filing evidence. Does not generate advice or calculate numbers."""
import argparse
import json
from pathlib import Path
from dotenv import load_dotenv

from filing_index import DEFAULT_INDEX, connect
from filing_passages import DEFAULT_PASSAGES_DIR
from filing_corpus import DEFAULT_CORPUS_DIR
from filing_retrieval import SearchEngine, SearchRequest, plan_search
from retrieval_data import PassageCatalogue
from retrieval_models import DEFAULT_MODELS, LocalModels


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("--years", nargs="+", type=int, required=True)
    parser.add_argument("--items", nargs="*", default=[])
    parser.add_argument("--kind", choices=["all", "text", "table"], default="all")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--no-decompose", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--mode", choices=["bm25", "dense", "hybrid", "hybrid_rerank"], default="hybrid_rerank")
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--passages-dir", type=Path, default=DEFAULT_PASSAGES_DIR)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS)
    args = parser.parse_args(argv)
    client = None
    try:
        request = SearchRequest(query=args.query, tickers=args.tickers, years=args.years,
                                items=args.items, kind=args.kind, limit=args.limit, decompose=not args.no_decompose)
        if args.plan_only:
            result = {"status": "ok", "plan": plan_search(request)}
        else:
            load_dotenv(Path(__file__).resolve().parent / ".env")
            client = connect(args.qdrant_url, read_only=True)
            engine = SearchEngine(PassageCatalogue(args.passages_dir, args.corpus_dir), LocalModels(args.models_dir),
                                  client, index_dir=args.index_dir)
            result = engine.search(request, mode=args.mode)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Search stopped ({type(exc).__name__}): {exc}")
        return 1
    except Exception as exc:
        print(f"Search service failed ({type(exc).__name__}); no substitute result was returned.")
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
