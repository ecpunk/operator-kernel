import argparse
from pathlib import Path

from operator_runtime.run_index import repair_run_index


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--max-entries",
        type=int,
        default=1200,
        help="Cap run index entries after repair",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    path = repair_run_index(root, max_entries=args.max_entries)
    print("Run index repair complete")
    print(f"- path: {path}")
    print(f"- max_entries: {args.max_entries}")


if __name__ == "__main__":
    main()
