"""Download the two pinned public models, then verify their exact bytes."""
import argparse
from retrieval_models import DEFAULT_MODELS, prepare_models, verify_models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", default=str(DEFAULT_MODELS))
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.verify_only:
            verify_models(args.models_dir)
        else:
            prepare_models(args.models_dir)
        print("Models verified. Indexing and search now run without model downloads.")
        return 0
    except Exception as exc:
        print(f"Model preparation failed ({type(exc).__name__}). Check model access and retry; no fallback model is used.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
