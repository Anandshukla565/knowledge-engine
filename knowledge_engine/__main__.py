"""Enable ``python -m knowledge_engine``."""

from knowledge_engine.apps.cli.entrypoint import main


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
