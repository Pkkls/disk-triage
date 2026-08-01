#!/usr/bin/env python3
"""Catch the shell mistakes this agent keeps making, from outside the agent.

The autonomy log holds twenty-eight documented errors. Three of them are the
same defect committed three times (a verification step that silently did
nothing), and the third instance was written minutes after re-reading the
warning against it in the file being edited. Writing an error down, publishing
it, and loading it into memory at the start of every session did not stop it
from happening again.

So this does not try to make anyone remember. It matches the shapes on the way
past. Each rule comes from an error that actually occurred, is named after its
ledger id, and fires on the real command that caused it.

    python errorguard.py --check '<command>'     # exit 1 if a rule fires
    python errorguard.py --selftest              # historical commands, both ways
    python errorguard.py --corpus <file>         # false-positive rate on real history

Rules earn their place by firing on the real failure and staying quiet on five
thousand commands that were fine. A guard with a false-positive rate gets
switched off, and then it protects nothing.

A third rule was written and dropped. `git add -A` followed by a commit swept
an unrelated 128-line change into a commit whose message described something
else, on 2026-08-01. But the same command is correct whenever the whole tree
is yours, which is most of the time: it fired on 117 of 5084 real commands,
nearly all of them fine. The dangerous case is not visible in the command text
at all, only in what the repository happens to hold at that moment. Dropped
rather than shipped at a rate that would teach anyone to ignore the output.
"""
import argparse
import re
import sys

# Commands whose exit code is the whole point of running them. Piping one of
# these into a pager throws that exit code away and hands back the pager's,
# which is 0 whether or not the check passed.
VERIFIERS = r"(secretscan|linkcheck|healthcheck|errorguard|pytest|go\s+vet|go\s+test|go\s+build|bash\s+-n|sh\s+-n|node\s+--check|python\s+-m\s+py_compile|--selftest|--check\b|npm\s+test|cargo\s+(test|check))"
PAGERS = r"(head|tail|less|more|sort|uniq|wc|grep)"

# What must not sit downstream of a lost exit code.
ACTIONS = re.compile(r"(git\s+(push|commit|tag)|scp|ssh|npm\s+publish|deploy|rm\s+-rf|install)", re.I)
# Write these as raw strings. An earlier version of this line was built by a
# script that put "\b" inside a non-raw string, so the compiled pattern held
# a literal backspace where the word boundary was meant to be. It compiled, it
# ran against every command, and it could never match anything. A rule that
# measures nothing while returning a clean answer is the exact defect this
# file exists to catch, and it sat here for an hour.
SUCCESS_CLAIM = re.compile(r"echo\s+[\"']?[^|&;]*?\b(ok|valid|success|passe|clean|green|pret|ready)\b", re.I)

RULES = []


def rule(rid, why):
    def deco(fn):
        RULES.append((rid, why, fn))
        return fn
    return deco


@rule("E18/E20", "exit code lu depuis le pipe, pas depuis le controle")
def exit_code_through_pipe(cmd):
    """A checker piped into a pager, then chained on its success.

    E18: a credential scanner piped through tail; the && that followed read
    tail's exit 0, printed "1 finding(s)", and pushed anyway. E20: node --check
    piped through tail reported "syntax valid" on a file that did not exist.
    Both times the component worked and the plumbing lost the answer.
    """
    if not re.search(VERIFIERS, cmd, re.I):
        return None
    # Chaining a lost exit code into another read is untidy. Chaining it into
    # something irreversible, or into a claim that the check passed, is the
    # defect itself. Measured over five thousand real commands, the loose form
    # fires 192 times; this one fires 66, and those 66 are all genuine.
    for m in re.finditer(r"\|\s*" + PAGERS + r"\b", cmd):
        rest = cmd[m.end():]
        if not re.search(r"(&&|\|\||\$\?|;\s*(if|then)\b)", rest):
            continue
        verifier = re.search(VERIFIERS, cmd, re.I).group(0)
        if ACTIONS.search(rest):
            return f"'{verifier}' pipe dans '{m.group(1)}', puis action irreversible"
        if SUCCESS_CLAIM.search(rest):
            return f"'{verifier}' pipe dans '{m.group(1)}', puis annonce un succes"
    return None


@rule("E27", "variable de shell inline a travers wsl.exe")
def wsl_inline_variable(cmd):
    """A loop or variable inside a single-quoted block crossing wsl.exe.

    E27: thirteen existence tests run as `[ -e "" ]` because wsl.exe stripped
    the loop variable, printing thirteen confident "absent". The warning
    against this is written in a script the agent had read the same hour. The
    tight signature is a variable *defined inside* the quoted block: remote
    environment variables like $HOME survive and are not the failure.
    """
    if not re.search(r"\bwsl(\.exe)?\b", cmd):
        return None
    # Assembling the script and passing it base64-encoded is the documented way
    # round this, and it works. Do not shout at the fix.
    if "base64" in cmd:
        return None
    for block in re.findall(r"'([^']*)'", cmd):
        # Defined in the block: for-loop variable, or an explicit assignment.
        names = set(re.findall(r"\bfor\s+([A-Za-z_]\w*)\s+in\b", block))
        names |= set(re.findall(r"^\s*([a-z_]\w*)=", block, re.M))
        for n in names:
            if re.search(r"\$\{?" + re.escape(n) + r"\b", block):
                return f"variable ${n} definie et utilisee dans un bloc simple-quote traversant wsl"
    return None


def check(cmd):
    """Returns [(rule_id, why, detail)] for one command."""
    hits = []
    for rid, why, fn in RULES:
        try:
            detail = fn(cmd)
        except re.error:
            continue
        if detail:
            hits.append((rid, why, detail))
    return hits


# --- the historical commands, kept verbatim ----------------------------------
# Positives are the real thing that went wrong. Negatives are commands close
# enough to the positives that a sloppy rule would catch them too.

POSITIVES = [
    ("E18", "python secretscan.py . --head-only | tail -3 && git push origin HEAD"),
    ("E20", "node --check /tmp/dash.js 2>&1 | tail -5 && echo 'syntax valid'"),
    ("E27", """wsl ssh -i /home/kil/.ssh/nano_key root@192.168.1.46 'for f in /root/watchdog.sh /etc/init.d/S82club_bot; do [ -e "$f" ] && echo "PRESENT $f" || echo "ABSENT  $f"; done'"""),
]

NEGATIVES = [
    # Piped, but the result is read by a human, not chained into a decision.
    "python secretscan.py . --head-only | tail -3",
    "go test ./... | tail -20",
    # Chained, but not piped: the exit code survives.
    "bash -n bin/nano-backup.sh && echo 'syntaxe ok'",
    "go vet ./... && go build ./...",
    # wsl with no shell variable at all: the form the fix uses.
    "wsl ssh -i /home/kil/.ssh/nano_key root@192.168.1.46 'ls -la /root/health.py /etc/init.d/S50crond'",
    # wsl with a remote environment variable, which survives and is intended.
    "wsl bash -c 'echo $HOME && ls $HOME/.ssh'",
    # Ordinary work.
    "grep -rn 'addn-hosts' /etc/dnsmasq.conf",
    "python healthcheck.py --selftest",
]


def selftest():
    bad = 0
    print("--- doit se declencher (commandes reelles du journal)")
    for want, cmd in POSITIVES:
        hits = check(cmd)
        got = any(want in rid for rid, _, _ in hits)
        bad += not got
        print(f"  {'ok  ' if got else 'RATE'} {want:<7} {cmd[:66]}")
    print("--- doit rester muet")
    for cmd in NEGATIVES:
        hits = check(cmd)
        bad += bool(hits)
        mark = "ok  " if not hits else "CRIE"
        extra = "" if not hits else "  <- " + hits[0][0]
        print(f"  {mark} {'':<7} {cmd[:66]}{extra}")
    print(f"\n{len(POSITIVES)} positifs, {len(NEGATIVES)} negatifs, {bad} en echec")
    return 1 if bad else 0


def corpus(path, dump=None):
    """Firing rate over every command actually run, ever.

    Writes the matches to a file rather than the console: the corpus contains
    commands with emoji in them, and printing one killed the first run of this
    with a codec error, which is a tool falling over on its own input.
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        cmds = [c for c in f.read().split("\n\x00\n") if c.strip()]
    fired = {}
    for c in cmds:
        for rid, _, detail in check(c):
            fired.setdefault(rid, []).append((c, detail))
    print(f"{len(cmds)} commandes reelles")
    total = 0
    for rid, hits in sorted(fired.items()):
        total += len(hits)
        print(f"  {rid:<10} {len(hits):>4} declenchement(s)  {100*len(hits)/len(cmds):.2f}%")
    if not fired:
        print("  aucun declenchement")
    print(f"  {'total':<10} {total:>4} sur {len(cmds)} ({100*total/len(cmds):.2f}%)")
    if dump:
        with open(dump, "w", encoding="utf-8") as f:
            for rid, hits in sorted(fired.items()):
                for c, d in hits:
                    f.write(f"### {rid} | {d}\n{c}\n\n")
        print(f"  -> detail dans {dump}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", metavar="CMD")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--corpus", metavar="FILE")
    ap.add_argument("--dump", metavar="FILE", help="ecrit les declenchements pour triage")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.corpus:
        return corpus(args.corpus, args.dump)
    if args.check:
        hits = check(args.check)
        for rid, why, detail in hits:
            print(f"{rid}: {why}\n       {detail}", file=sys.stderr)
        return 1 if hits else 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
