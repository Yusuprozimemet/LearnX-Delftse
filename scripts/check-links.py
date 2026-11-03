"""Check that every relative link in the given Markdown files points at a file that exists.

A link is each `](target)` whose target is not a URL or an anchor; it resolves against its
file's directory. Prints each broken one and the count, and exits 1 if any is broken.
Usage: python scripts/check-links.py README.md docs/*.md
"""
import re
import sys
from pathlib import Path

LINK = re.compile(r"\]\(([^)\s]+)")
URL = re.compile(r"[a-z][a-z0-9+.-]*:")


def check(path):
    """Return the file's relative links and the broken ones, as `file:line: target`."""
    total, broken = 0, []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for target in LINK.findall(line):
            file_part = target.split("#")[0]
            if URL.match(target) or not file_part:
                continue
            total += 1
            if not (path.parent / file_part).exists():
                broken.append(f"{path}:{number}: {target}")
    return total, broken


def main(args):
    if not args:
        sys.exit(__doc__)
    total, broken = 0, []
    for arg in args:
        path = Path(arg)
        if not path.is_file():
            print(f"no such file: {arg}", file=sys.stderr)
            return 2
        file_total, file_broken = check(path)
        total += file_total
        broken += file_broken
    print("\n".join(broken + [f"{len(broken)} of {total} relative links broken"]))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
