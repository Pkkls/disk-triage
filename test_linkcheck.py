"""Le verificateur doit distinguer un lien mort d'un lien qu'on n'a pas pu voir.

Ecrit le jour ou chaque lien d'un rapport quotidien s'est revele mort en
repondant HTTP 200 : une coquille d'application de deux kilo-octets la ou une
vraie page en rend deux cents. Le code de statut confirmait l'erreur au lieu de
l'attraper.

Lancer : python test_linkcheck.py
"""
import sys

import linkcheck


class _Resp:
    def __init__(self, body, ctype, status=200):
        self._b, self.status = body, status
        self.headers = {"Content-Type": ctype}

    def read(self, n=None):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serving(body, ctype, status=200):
    return lambda req, timeout=None: _Resp(body, ctype, status)


CASES = [
    # (libelle, corps, type, statut attendu, raison)
    ("coquille vide: le defaut qui a motive l'outil",
     b"<!DOCTYPE html><html><head></head><body></body></html>", "text/html",
     linkcheck.FAIL),
    ("vraie page", b"<html>" + b"x" * 50000, "text/html", linkcheck.OK),
    # Un badge CI est un SVG de deux kilo-octets et se porte tres bien. La
    # premiere version en signalait trois, un taux de faux positifs qui apprend
    # a ignorer l'outil.
    ("badge SVG court", b"<svg>" + b"y" * 2000, "image/svg+xml", linkcheck.OK),
    # Une page courte qui pointe ailleurs redirige, elle n'est pas vide.
    ("redirection meta",
     b'<html><head><link rel="canonical" href="/en/"></head></html>', "text/html",
     linkcheck.OK),
]


def main():
    failures = 0
    real = linkcheck.urllib.request.urlopen
    for label, body, ctype, want in CASES:
        linkcheck.urllib.request.urlopen = _serving(body, ctype)
        try:
            got, detail = linkcheck.check("https://exemple.invalide/x", ".")
        finally:
            linkcheck.urllib.request.urlopen = real
        if got != want:
            print(f"FAIL {label}: {got} ({detail}), attendu {want}")
            failures += 1
        else:
            print(f"ok   {label}")

    # Les gabarits de code ne sont pas des liens.
    import tempfile, os
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.html")
        open(p, "w", encoding="utf-8").write(
            '<a href="https://exemple.invalide/${item.icon_url}">a</a>\n'
            '<a href="https://reel.invalide/page">b</a>\n')
        urls = [u for _, u in linkcheck.extract(p)]
        if any("${" in u for u in urls):
            print(f"FAIL gabarit non ecarte: {urls}")
            failures += 1
        elif len(urls) != 1:
            print(f"FAIL extraction: {urls}")
            failures += 1
        else:
            print("ok   gabarits de code ecartes")

    print(f"\n{failures} echec(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
