import sys

from app.library import build_index


def main() -> int:
    try:
        manifest = build_index()
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Indexing failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Indexed {manifest['document_count']} documents "
        f"into {manifest['chunk_count']} passages."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
