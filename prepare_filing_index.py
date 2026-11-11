"""Replay-verify saved passages, then build or verify their Qdrant index."""
import argparse
import fcntl
import json
from pathlib import Path
from dotenv import load_dotenv

from company_registry import DATA_DIR
from filing_corpus import DEFAULT_CORPUS_DIR
from filing_passages import DEFAULT_PASSAGES_DIR
from filing_index import DEFAULT_INDEX, connect, prepare_index, load_index
from retrieval_data import PassageCatalogue
from retrieval_models import DEFAULT_MODELS, LocalModels, verify_models


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--plain-text", action="store_true", help="Separate table-ablation representation; use a separate --index-dir")
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--passages-dir", type=Path, default=DEFAULT_PASSAGES_DIR)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)
    if args.verify_only and args.refresh:
        parser.error("--refresh cannot be used with --verify-only.")
    if args.plain_text and args.index_dir.resolve() == DEFAULT_INDEX.resolve():
        parser.error("Use a separate --index-dir for --plain-text to preserve the table-aware index.")
    load_dotenv(Path(__file__).resolve().parent / ".env")
    client = None
    try:
        catalogue = PassageCatalogue(args.passages_dir, args.corpus_dir)
        client = connect(args.qdrant_url, read_only=args.verify_only)
        if args.verify_only:
            catalogue.replay(args.data_dir)
            manifest, units = load_index(catalogue, verify_models(args.models_dir), client,
                                         index_dir=args.index_dir, verify_vectors=True)
            catalogue.verify_unchanged()
            result = {"status": "ok", "verified_search_windows": len(units),
                      "collection": manifest["collection"], "coverage": catalogue.summary()}
        else:
            args.index_dir.mkdir(parents=True, exist_ok=True)
            with (args.index_dir / "build.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                models = LocalModels(args.models_dir, args.threads)
                result = prepare_index(catalogue, models, client, index_dir=args.index_dir,
                    data_dir=args.data_dir, refresh=args.refresh, plain_text=args.plain_text)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Index preparation stopped ({type(exc).__name__}): {exc}")
        return 1
    except Exception as exc:
        print(f"Qdrant/index operation failed ({type(exc).__name__}). Check that Qdrant is running and keys match; the previous manifest is retained.")
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
