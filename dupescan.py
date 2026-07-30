"""Find duplicate files (identical content) under a directory. Read only.

Usage: python dupescan.py [root] [--min-mb 1] [--out report.md]
"""
import argparse
import hashlib
import os
import sys
from collections import defaultdict

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", "target"}


def walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            yield path, size


def digest(path, limit=None):
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb") as f:
            if limit:
                h.update(f.read(limit))
            else:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def group(paths, limit=None):
    """Groups a list of paths by digest."""
    out = defaultdict(list)
    for p in paths:
        d = digest(p, limit)
        if d:
            out[d].append(p)
    return [v for v in out.values() if len(v) > 1]


def find_dupes(root, min_bytes):
    by_size = defaultdict(list)
    for path, size in walk(root):
        if size >= min_bytes:
            by_size[size].append(path)

    dupes = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        # ponytail: prefilter on the first 64 KB before hashing whole files
        for candidates in group(paths, limit=65536):
            for exact in group(candidates):
                dupes.append((size, exact))
    dupes.sort(key=lambda x: x[0] * (len(x[1]) - 1), reverse=True)
    return dupes


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--min-mb", type=float, default=1.0)
    ap.add_argument("--out")
    args = ap.parse_args()

    dupes = find_dupes(args.root, int(args.min_mb * 1024 * 1024))
    wasted = sum(size * (len(paths) - 1) for size, paths in dupes)

    lines = [
        f"# Duplicates under {os.path.abspath(args.root)}",
        "",
        f"{len(dupes)} groups, {human(wasted)} reclaimable (threshold {args.min_mb} MB)",
        "",
    ]
    for size, paths in dupes:
        lines.append(f"## {human(size)} x{len(paths)} -> {human(size * (len(paths) - 1))} reclaimable")
        lines.extend(f"- {p}" for p in sorted(paths))
        lines.append("")

    text = "\n".join(lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"{len(dupes)} groups, {human(wasted)} reclaimable -> {args.out}")
    else:
        sys.stdout.write(text)


def _selftest():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        blob = os.urandom(200_000)
        for name in ("a.bin", "sub/b.bin"):
            p = os.path.join(d, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(blob)
        open(os.path.join(d, "c.bin"), "wb").write(os.urandom(200_000))

        res = find_dupes(d, 1000)
        assert len(res) == 1, res
        size, paths = res[0]
        assert size == 200_000 and len(paths) == 2, res
        assert find_dupes(d, 500_000) == []
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
