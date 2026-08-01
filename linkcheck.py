#!/usr/bin/env python3
"""Check that the links we publish actually lead somewhere.

Written the day every item link in a daily report turned out to be dead. They
answered HTTP 200 and rendered nothing: a single-page application shell of two
kilobytes where a real page returns two hundred. Checking the status code would
have confirmed the mistake rather than caught it, which is why this tool looks
at what came back and not only at what the server called it.

    python linkcheck.py <path> [<path>...]     # markdown, html, or a directory
    python linkcheck.py --json <path>

Exit code is 0 when every link resolved, 1 when any failed, 2 when a link could
not be checked at all. That third value is the point: "could not measure" and
"measured, fine" are different answers, and every tool in this repository
refuses to collapse them.
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

# Markdown [text](url) and HTML href="url" / src="url".
LINK_PATTERNS = [
    re.compile(r"\[[^\]]*\]\(([^)\s]+)"),
    re.compile(r'(?:href|src)="([^"]+)"'),
]

SKIP_SCHEMES = ("mailto:", "tel:", "data:", "javascript:", "#")

# A page this small carrying no visible text is a shell, not content. The
# threshold is deliberately low: it is meant to catch an empty application
# skeleton, not to judge a terse page.
SHELL_BYTES = 4096

TIMEOUT = 20
UA = "linkcheck (github.com/Pkkls/disk-triage)"

OK, FAIL, UNKNOWN = "ok", "FAIL", "?"


def extract(path):
    """Returns [(line_number, url)] for one file."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as err:
        raise RuntimeError(f"illisible: {err}") from err

    found = []
    for n, line in enumerate(lines, 1):
        for pat in LINK_PATTERNS:
            for url in pat.findall(line):
                if url.lower().startswith(SKIP_SCHEMES):
                    continue
                # Gabarits de code : ${...} et {{...}} ne sont pas des liens,
                # ce sont des trous a remplir a l'execution. Les signaler
                # remplit la sortie de faux positifs, et un verificateur qui
                # crie au loup se fait ignorer.
                if "${" in url or "{{" in url:
                    continue
                found.append((n, url))
    return found


def check(url, base_dir):
    """Returns (status, detail). Never raises."""
    if not url.startswith(("http://", "https://")):
        # Relative link: resolve it against the file's directory.
        target = os.path.normpath(os.path.join(base_dir, url.split("#", 1)[0]))
        if not url.split("#", 1)[0]:
            return OK, "ancre locale"
        return (OK, "fichier présent") if os.path.exists(target) else (FAIL, "fichier absent")

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(200_000)
            size = len(body)
            ctype = (r.headers.get("Content-Type") or "").lower()

            # The empty-shell test applies to pages, not to images. A CI badge
            # is an SVG of two kilobytes and is perfectly healthy; the first
            # version of this flagged three of them, which is a false-positive
            # rate that would have taught anyone to ignore the tool. In a
            # checker, a false alarm costs more than a miss.
            is_page = "html" in ctype or (not ctype and b"<html" in body[:400].lower())
            # Une page courte qui pointe ailleurs n'est pas une coquille vide,
            # c'est une redirection : le lien marche, il rebondit. Troisieme
            # faux positif de cette heuristique, apres les badges SVG. Une
            # regle qui se resserre trois fois avant d'etre juste merite d'etre
            # dite, pas lissee.
            redirects = (b"http-equiv=\"refresh\"" in body[:1500].lower()
                         or b"rel=\"canonical\"" in body[:1500].lower())
            if is_page and size < SHELL_BYTES and not redirects:
                return FAIL, f"HTTP {r.status} mais {size} octets — coquille vide ?"
            return OK, f"HTTP {r.status}, {size} octets, {ctype.split(';')[0] or 'type inconnu'}"
    except urllib.error.HTTPError as e:
        # 403 and 429 mean the server refused *us*, which says nothing about
        # whether the link is good. Reporting them as broken would fill the
        # output with false alarms, and a checker that cries wolf gets ignored.
        if e.code in (403, 429):
            return UNKNOWN, f"HTTP {e.code} (accès refusé au vérificateur)"
        return FAIL, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return UNKNOWN, f"{type(e).__name__}"


def gather(paths):
    """Les fichiers a verifier : ceux qui sont publies.

    Un artefact genere localement et ignore par git n'est pas publie, et ses
    liens ne regardent personne. Les inclure remplissait la sortie de deux
    echecs permanents sur une carte de repertoires produite par un autre outil
    de ce depot, ce qui est exactement le bruit que ce fichier evite ailleurs.
    """
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv")]
                files += [os.path.join(root, n) for n in names
                          if n.lower().endswith((".md", ".html", ".htm"))]
        else:
            files.append(p)
    return [f for f in files if not git_ignored(f)]


def git_ignored(path):
    """True si git ignore ce fichier. Faux quand la question ne se pose pas
    (hors depot, ou git indisponible) : dans le doute on verifie plutot que de
    sauter en silence."""
    try:
        r = subprocess.run(["git", "check-ignore", "-q", os.path.basename(path)],
                           cwd=os.path.dirname(path) or ".",
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    jobs = []
    for path in gather(args.paths):
        try:
            for line, url in extract(path):
                jobs.append((path, line, url))
        except RuntimeError as err:
            jobs.append((path, 0, None))
            print(f"  ??  {path}: {err}", file=sys.stderr)

    # Un lien identique n'est teste qu'une fois : le meme depot revient partout.
    seen = {}
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {}
        for path, line, url in jobs:
            if url is None or url in seen:
                continue
            seen[url] = None
            futures[ex.submit(check, url, os.path.dirname(path) or ".")] = url
        for fut in concurrent.futures.as_completed(futures):
            seen[futures[fut]] = fut.result()

    failed = unknown = 0
    for path, line, url in jobs:
        if url is None:
            unknown += 1
            continue
        status, detail = seen[url]
        if status == FAIL:
            failed += 1
        elif status == UNKNOWN:
            unknown += 1
        results.append({"file": path, "line": line, "url": url,
                        "status": status, "detail": detail})

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            if r["status"] != OK:
                mark = " FAIL " if r["status"] == FAIL else "  ??  "
                print(f"{mark} {r['file']}:{r['line']}  {r['url']}\n        {r['detail']}")
        print(f"\n{len(results)} liens ({len(seen)} distincts), "
              f"{failed} cassés, {unknown} non vérifiables")

    return 1 if failed else (2 if unknown else 0)


if __name__ == "__main__":
    sys.exit(main())
