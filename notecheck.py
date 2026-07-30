"""Audit a directory of markdown notes: dead wiki links, frontmatter drift,
stale paths, leaked credentials. Read only.

Written for an agent's own memory directory, but the failure modes are the same
in any Zettelkasten or Obsidian vault: links that quietly point nowhere, a
`name:` that stopped matching its filename, a path to a project that moved.

None of these break anything, which is exactly why they rot. A dead link does
not error, it just never delivers the context it promises.

Usage: python notecheck.py <dir> [--fix-names] [--quiet]
Exit 1 when something is wrong, 2 when the directory cannot be read.
"""
import argparse
import os
import re
import sys

WIKILINK = re.compile(r"\[\[([^\]|\n]+)\]\]")
MDLINK = re.compile(r"\[[^\]]*\]\(([^)]+\.md)\)")
# Paths may contain spaces ("02 - Projects"), so the match runs to the end of
# the line and the trailing prose is trimmed back afterwards. Stopping at the
# first space produced a false positive on every such path, which is the exact
# failure class this tool exists to find.
WINPATH = re.compile(r"[A-Za-z]:\\\w[^\n`\"'*?<>|]*\\\w[^\n`\"'*?<>|]*")
SECRETS = {
    "telegram bot token": r"[0-9]{8,10}:AA[A-Za-z0-9_-]{33}",
    "github token": r"gh[pousr]_[A-Za-z0-9]{36}",
    "aws access key": r"AKIA[0-9A-Z]{16}",
    "slack token": r"xox[baprs]-[A-Za-z0-9-]{10,}",
    "private key block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}
PLACEHOLDER = re.compile(r"(x{4,}|your|example|placeholder|changeme|dummy|<)", re.I)


def trim_to_existing(raw):
    """Returns the longest existing prefix of a matched path, or None.

    A path in prose is followed by the rest of the sentence. Words are dropped
    from the end until something on disk answers, so "C:\\...\\02 - Projects\\x
    (checked today)" resolves rather than being reported as missing.
    """
    candidate = raw.rstrip(" .,;:)`’\u2019")
    while len(candidate) > 12:
        if os.path.exists(candidate):
            return candidate
        cut = max(candidate.rfind(" "), candidate.rfind("\\"))
        if cut <= 3:
            return None
        candidate = candidate[:cut].rstrip(" .,;:)`’\u2019")
    return None


def slug_for(filename):
    """The convention: a note's name is its filename in kebab-case."""
    return os.path.splitext(filename)[0].replace("_", "-")


def read_notes(directory):
    notes = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".md"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as f:
                notes[name] = f.read()
        except OSError:
            continue
    return notes


def declared_name(text):
    m = re.search(r"^name:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else None


def audit(directory, index_name="MEMORY.md"):
    notes = read_notes(directory)
    if not notes:
        return None

    problems = {"names": [], "links": [], "paths": [], "secrets": [], "index": []}
    names = {}
    for filename, text in notes.items():
        if filename == index_name:
            continue
        want = slug_for(filename)
        got = declared_name(text)
        names[filename] = got
        if got != want:
            problems["names"].append((filename, got, want))

    valid = {n for n in names.values() if n}
    for filename, text in notes.items():
        for target in WIKILINK.findall(text):
            # Links containing spaces or commas are prose, not references.
            if re.search(r"[,\s]", target):
                continue
            if target not in valid:
                problems["links"].append((filename, target))

        for raw in set(WINPATH.findall(text)):
            path = trim_to_existing(raw)
            if path is None:
                problems["paths"].append((filename, raw.rstrip(" .,;:)`\u2019")[:70]))

        for label, pattern in SECRETS.items():
            for m in re.finditer(pattern, text):
                if not PLACEHOLDER.search(m.group(0)):
                    problems["secrets"].append((filename, label))
                    break

    # The index is the only file loaded every session, so it is the one that
    # must not lie: no entry pointing at a missing file, no note left out.
    if index_name in notes:
        listed = set(MDLINK.findall(notes[index_name]))
        for target in listed:
            if target not in notes:
                problems["index"].append(("missing target", target))
        for filename in notes:
            if filename != index_name and filename not in listed:
                problems["index"].append(("not indexed", filename))

    return {"notes": notes, "problems": problems, "names": names}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--index", default="MEMORY.md")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    result = audit(args.directory, args.index)
    if result is None:
        print(f"{args.directory}: no markdown notes found", file=sys.stderr)
        return 2

    p = result["problems"]
    total = sum(len(v) for v in p.values())
    links = sum(
        1
        for text in result["notes"].values()
        for t in WIKILINK.findall(text)
        if not re.search(r"[,\s]", t)
    )

    if not args.quiet:
        alive = links - len(p["links"])
        print(f"{len(result['notes'])} notes, {links} internal links, {alive} of them alive")

    for filename, got, want in p["names"]:
        print(f"  name drift      {filename}: {got!r} should be {want!r}")
    for filename, target in p["links"]:
        print(f"  dead link       {filename} -> [[{target}]]")
    for filename, path in p["paths"]:
        print(f"  stale path      {filename} -> {path}")
    for filename, label in p["secrets"]:
        print(f"  CREDENTIAL      {filename}: {label}")
    for kind, item in p["index"]:
        print(f"  index {kind:12} {item}")

    if total:
        print(f"\n{total} problem(s)")
        return 1
    if not args.quiet:
        print("clean")
    return 0


def _selftest():
    import tempfile

    def note(d, name, body):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(body)

    with tempfile.TemporaryDirectory() as d:
        note(d, "project_alpha.md", "---\nname: project-alpha\n---\nSee [[project-beta]].\n")
        note(d, "project_beta.md", "---\nname: project-beta\n---\nNothing.\n")
        note(d, "MEMORY.md", "- [Alpha](project_alpha.md)\n- [Beta](project_beta.md)\n")

        p = audit(d)["problems"]
        assert not any(p.values()), f"a clean vault must report nothing: {p}"

        # A link to a note that does not exist.
        note(d, "project_alpha.md", "---\nname: project-alpha\n---\nSee [[project-gamma]].\n")
        p = audit(d)["problems"]
        assert p["links"] == [("project_alpha.md", "project-gamma")], p

        # A name that drifted from its filename, which is what breaks links.
        note(d, "project_beta.md", "---\nname: Beta The Second\n---\nx\n")
        p = audit(d)["problems"]
        assert p["names"] == [("project_beta.md", "Beta The Second", "project-beta")], p

        # Prose in double brackets is not a reference.
        note(d, "project_alpha.md", "---\nname: project-alpha\n---\nformat [[date, price]]\n")
        p = audit(d)["problems"]
        assert p["links"] == [], f"prose must not count as a link: {p['links']}"

        # A credential, and a placeholder that must not be reported.
        note(d, "project_alpha.md",
             "---\nname: project-alpha\n---\ntoken 8601039089:AA" + "b" * 33 + "\n")
        assert audit(d)["problems"]["secrets"], "a real token must be caught"
        note(d, "project_alpha.md",
             "---\nname: project-alpha\n---\ntoken ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n")
        assert not audit(d)["problems"]["secrets"], "a placeholder must not be reported"

        # A note absent from the index, and an index entry with no file.
        note(d, "project_delta.md", "---\nname: project-delta\n---\nx\n")
        note(d, "MEMORY.md", "- [Alpha](project_alpha.md)\n- [Ghost](project_ghost.md)\n")
        kinds = dict(audit(d)["problems"]["index"])
        assert "project_ghost.md" in kinds.values() or any(
            k == "missing target" for k in kinds
        ), kinds
        idx = audit(d)["problems"]["index"]
        assert ("missing target", "project_ghost.md") in idx, idx
        assert ("not indexed", "project_delta.md") in idx, idx

        # A stale filesystem path.
        note(d, "project_alpha.md",
             "---\nname: project-alpha\n---\nrepo C:\\Users\\nobody\\gone\\project\n")
        assert audit(d)["problems"]["paths"], "a path that does not exist must be reported"

        # A real path containing spaces, followed by prose. Stopping at the
        # first space reported every such path as missing.
        spaced = os.path.join(d, "two words")
        os.makedirs(spaced)
        note(d, "project_alpha.md",
             f"---\nname: project-alpha\n---\nlives in `{spaced}` (checked today)\n")
        assert audit(d)["problems"]["paths"] == [], \
            f"a real path with spaces must not be flagged: {audit(d)['problems']['paths']}"

        # Prose that merely mentions a path shape is not a path.
        note(d, "project_alpha.md",
             "---\nname: project-alpha\n---\nuse C:\\... form, or C:\\ then /mnt/c/\n")
        assert audit(d)["problems"]["paths"] == [], \
            f"an elided path in prose must not be flagged: {audit(d)['problems']['paths']}"

    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
