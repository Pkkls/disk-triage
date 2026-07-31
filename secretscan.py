"""Scan a git repository's whole history for credentials. Read only.

Scanning the working tree is not enough: a key deleted in a later commit is
still in the history, and still public if the repo is.

Matched values are never printed in full. Placeholders are recognised and
counted separately, because a scanner that cries wolf is a scanner nobody runs.

Usage: python secretscan.py [repo ...] [--head-only] [--quiet]
Exit code is 1 when something real is found, so it fits a pre-push hook.
"""
import argparse
import os
import re
import subprocess
import sys

# High confidence only. Generic "32 hex chars" style rules match commit hashes,
# checksums and minified assets, and drown the real findings.
PATTERNS = {
    "telegram bot token": r"[0-9]{8,10}:AA[A-Za-z0-9_-]{33}",
    "github token": r"gh[pousr]_[A-Za-z0-9]{36}",
    # A capturing group, not (?:...). git grep speaks POSIX ERE, where the
    # non-capturing form is invalid and the whole pattern is rejected.
    "openai/anthropic key": r"sk-(ant-|or-v1-|proj-)?[A-Za-z0-9_-]{20,}",
    "aws access key": r"AKIA[0-9A-Z]{16}",
    "slack token": r"xox[baprs]-[A-Za-z0-9-]{10,}",
    "google api key": r"AIza[0-9A-Za-z_-]{35}",
    "private key block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "discord bot token": r"[MNO][A-Za-z0-9_-]{23}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27}",
}

# Files that should never be committed, whatever is inside them.
SENSITIVE_NAMES = re.compile(
    r"(^|/)(\.env(\.[^/]*)?|[^/]*cookies?\.json|accounts?\.json|credentials?\.json"
    r"|id_rsa|id_ed25519|[^/]*\.pem|[^/]*\.pfx|secrets?\.(json|ya?ml|txt))$",
    re.I,
)
# .env.example and friends are documentation, not leaks.
EXAMPLE_NAME = re.compile(r"(example|sample|template|dist)", re.I)

# git grep speaks POSIX ERE and has no lookbehind, so the boundary is enforced
# in a second pass here. Without it, a token pattern happily matches the middle
# of a base64 blob: a Flask session cookie in a test fixture read as a Discord
# token until this was added.
BOUNDARY = r"(?<![A-Za-z0-9_/+-])"

# Real credentials do not say "your key here".
PLACEHOLDER = re.compile(
    r"(x{4,}|0{6,}|1234567|abcdef|your[_-]?|example|placeholder|changeme|dummy|fake|"
    r"replace[_-]?me|<[^>]*>|\.\.\.|test[_-]?key|sample)",
    re.I,
)


class ScanError(Exception):
    """git could not run the search, which is not the same as finding nothing."""


def run(repo, *args, timeout=300, ok_codes=(0,)):
    """Runs git and refuses to turn a failure into an empty result.

    `git grep` exits 0 when it matches, 1 when it does not, and 2 or more on a
    real error such as a pattern its regex engine rejects. Collapsing all of
    those to "" is how a scanner reports "clean" while never having searched:
    one pattern here used Perl syntax that POSIX ERE rejects, and the tool
    stayed silent about it through every scan.
    """
    try:
        out = subprocess.run(
            ("git", "-C", repo) + args,
            capture_output=True, text=True, timeout=timeout, errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise ScanError(f"git {' '.join(args[:2])}: {err}") from err
    if out.returncode not in ok_codes:
        detail = (out.stderr or "").strip().splitlines()
        raise ScanError(f"git {args[0]} failed ({out.returncode}): {detail[-1] if detail else '?'}")
    return out.stdout


def mask(value):
    """Enough to recognise a secret, never enough to use it."""
    keep = 4 if len(value) > 12 else 1
    return f"{value[:keep]}{'*' * min(len(value) - keep, 24)} ({len(value)} chars)"


def is_placeholder(value, path):
    return bool(PLACEHOLDER.search(value)) or bool(EXAMPLE_NAME.search(path))


def scan_repo(repo, head_only=False, max_revs=400):
    """Returns (findings, placeholders_skipped, revisions_scanned)."""
    if not os.path.exists(os.path.join(repo, ".git")):
        return None, 0, 0

    revs = ["HEAD"] if head_only else [
        r for r in run(repo, "rev-list", "--all").split() if r
    ][:max_revs] or ["HEAD"]

    findings, skipped, seen = [], 0, set()
    for label, pattern in PATTERNS.items():
        # grep exits 1 when a pattern simply matches nothing; anything higher
        # is a real failure and must not pass for a clean result.
        # "-e" is required: the private-key pattern starts with a dash and git
        # would otherwise parse it as an option and print its own help.
        for line in run(repo, "grep", "-I", "-n", "-E", "-e", pattern, *revs,
                        ok_codes=(0, 1)).splitlines():
            # "<rev>:<path>:<lineno>:<content>"
            parts = line.split(":", 3)
            if len(parts) < 4:
                continue
            rev, path, lineno, content = parts
            match = re.search(BOUNDARY + pattern, content)
            if not match:
                # git grep found it mid-blob; the stricter pass says otherwise.
                skipped += 1
                continue
            value = match.group(0)
            if is_placeholder(value, path):
                skipped += 1
                continue
            key = (label, value)
            if key in seen:
                continue
            seen.add(key)
            findings.append({
                "kind": label, "rev": rev[:8], "path": path,
                "line": lineno, "masked": mask(value),
            })

    for path in {p.strip() for p in run(
        repo, "log", "--all", "--diff-filter=A", "--name-only", "--format="
    ).splitlines() if p.strip()}:
        if SENSITIVE_NAMES.search(path) and not EXAMPLE_NAME.search(path):
            findings.append({
                "kind": "sensitive file committed", "rev": "-",
                "path": path, "line": "-", "masked": "",
            })

    return findings, skipped, len(revs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repos", nargs="*", default=["."])
    ap.add_argument("--head-only", action="store_true", help="skip history, scan the checkout only")
    ap.add_argument("--quiet", action="store_true", help="print findings only")
    args = ap.parse_args()

    total = 0
    unreadable = 0
    for repo in args.repos or ["."]:
        findings, skipped, revs = scan_repo(repo, args.head_only)
        if findings is None:
            # Staying silent here would let a pre-push hook pass on a typo.
            print(f"{repo}: not a git repository", file=sys.stderr)
            unreadable += 1
            continue
        if not args.quiet:
            note = f", {skipped} placeholders ignored" if skipped else ""
            print(f"{os.path.abspath(repo)}: {revs} revisions{note}")
        for f in findings:
            total += 1
            where = f"{f['path']}:{f['line']}" if f["line"] != "-" else f["path"]
            print(f"  {f['kind']:26} {f['rev']:8} {where} {f['masked']}")

    if total:
        print(f"\n{total} finding(s). Rotate the credential first, rewriting history does not un-leak it.")
        return 1
    if unreadable:
        print(f"{unreadable} path(s) could not be scanned", file=sys.stderr)
        return 2
    if not args.quiet:
        print("no credentials found")
    return 0


def _selftest():
    import tempfile

    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    with tempfile.TemporaryDirectory() as d:
        def git(*a):
            subprocess.run(("git", "-C", d) + a, capture_output=True, env=env)

        git("init", "-q")

        # Every pattern must be accepted by the engine that actually runs it.
        # git grep speaks POSIX ERE, Python's re does not, and a pattern valid
        # here but rejected there made the scanner silently skip a whole class
        # of credential on every scan it ever performed.
        open(os.path.join(d, "seed.txt"), "w").write("seed\n")
        git("add", "-A")
        git("commit", "-qm", "seed")
        for label, pattern in PATTERNS.items():
            proc = subprocess.run(
                ("git", "-C", d, "grep", "-I", "-n", "-E", "-e", pattern, "HEAD"),
                capture_output=True, text=True, env=env,
            )
            assert proc.returncode in (0, 1), (
                f"pattern {label!r} is rejected by git grep: {proc.stderr.strip()}"
            )
            re.compile(pattern)  # and by Python, used for the boundary pass

        # Each pattern must also match the thing it claims to match.
        samples = {
            "telegram bot token": "8446960541:AA" + "b" * 33,
            "github token": "ghp_" + "c" * 36,
            "openai/anthropic key": "sk-ant-" + "d" * 30,
            "aws access key": "AKIA" + "E" * 16,
            "slack token": "xoxb-" + "1" * 12,
            "google api key": "AIza" + "f" * 35,
            # Assembled rather than written out: a literal here is a real
            # match, and this file is scanned by its own CI.
            "private key block": "-----BEGIN RSA " + "PRIVATE KEY" + "-----",
            "discord bot token": "M" + "g" * 23 + "." + "h" * 6 + "." + "i" * 27,
        }
        for label, pattern in PATTERNS.items():
            assert label in samples, f"no sample for {label}"
            assert re.search(pattern, samples[label]), f"{label} does not match its own sample"

        # A credential-shaped value, committed then deleted: the point of the tool.
        leaked = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        with open(os.path.join(d, "config.py"), "w") as f:
            f.write(f'TOKEN = "{leaked}"\n')
        git("add", "-A")
        git("commit", "-qm", "oops")
        os.remove(os.path.join(d, "config.py"))
        with open(os.path.join(d, "config.py"), "w") as f:
            f.write('TOKEN = os.environ["TOKEN"]\n')
        git("add", "-A")
        git("commit", "-qm", "move to env")

        head_findings, _, _ = scan_repo(d, head_only=True)
        assert head_findings == [], "the checkout is clean, that is the trap"

        findings, _, _ = scan_repo(d)
        assert len(findings) == 1, findings
        assert findings[0]["kind"] == "github token", findings
        assert leaked not in str(findings), "the secret must never be reproduced in full"
        assert findings[0]["masked"].startswith("ghp_"), findings

        # Placeholders must not be reported.
        with open(os.path.join(d, ".env.example"), "w") as f:
            f.write("GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n")
            f.write("OPENAI_API_KEY=sk-your-key-here-000000000000\n")
        git("add", "-A")
        git("commit", "-qm", "document the env")
        findings, skipped, _ = scan_repo(d)
        assert len(findings) == 1, f"placeholders leaked into the report: {findings}"
        assert skipped >= 1, "placeholders should be counted"

        # A real .env is a finding on its name alone.
        with open(os.path.join(d, ".env"), "w") as f:
            f.write("NOTHING_SECRET_LOOKING=1\n")
        git("add", "-f", ".env")
        git("commit", "-qm", "add env")
        findings, _, _ = scan_repo(d)
        kinds = [f["kind"] for f in findings]
        assert "sensitive file committed" in kinds, kinds
        assert sum(1 for k in kinds if k == "sensitive file committed") == 1, kinds

        # A token pattern sitting inside a base64 blob is not a token. A Flask
        # session cookie in a fixture triggered this exact false positive.
        with open(os.path.join(d, "fixture.json"), "w") as f:
            f.write('{"raw":"Set-Cookie: session=eyJ1c2VyX2lkIjoiMTAw'
                    'MzIm2R1c2VyX2lkPTEwMDAxIn0.aBcDeF.gHiJkLmNoPqRsTuVwXyZ012345"}\n')
        git("add", "-A")
        git("commit", "-qm", "add a capture fixture")
        findings, _, _ = scan_repo(d)
        assert not any(f["kind"] == "discord bot token" for f in findings), \
            f"matched inside a base64 blob: {findings}"

    # A path that is not a repository must be loud, not silently clean.
    with tempfile.TemporaryDirectory() as empty:
        findings, _, _ = scan_repo(empty)
        assert findings is None, "a non-repo must be reported as unscannable"

    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
