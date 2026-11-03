"""Replay the day closed by a push, or an explicitly requested day on main."""
import argparse
import subprocess
import sys
from pathlib import Path

from evidence import close_commit, closing_spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--day", default="", help="optional day number to replay on main")
    args = parser.parse_args()
    try:
        commit = args.commit
        if args.day:
            if not args.day.isascii() or not args.day.isdecimal() or not 0 <= int(args.day) <= 99:
                raise ValueError("day must be a number from 0 to 99")
            day = f"{int(args.day):02d}"
            commit = close_commit(day, "main")
            if not commit:
                raise ValueError(f"day-{day}: no closing commit on main")
        spec = closing_spec(commit)
    except ValueError as error:
        print(f"FAIL  {error}")
        return 1
    if spec is None:
        print("not a closing commit")
        return 0
    return subprocess.call([sys.executable, str(Path(__file__).with_name("evidence.py")),
                            spec, "--ref", "main", "--json", "evidence.json"])


if __name__ == "__main__":
    sys.exit(main())
