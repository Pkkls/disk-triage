"""Map directories: size, file count, last activity, git state, project type.

Generates a standalone sortable HTML page. Read only.
Usage: python dirmap.py [root] [--out map.html]
"""
import argparse
import html
import os
import re
import subprocess
import sys
import time

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "target", "dist", "build"}

MARKERS = [
    ("package.json", "node"),
    ("Cargo.toml", "rust"),
    ("go.mod", "go"),
    ("requirements.txt", "python"),
    ("pyproject.toml", "python"),
    ("manifest.json", "extension"),
    ("index.html", "web"),
    ("Dockerfile", "docker"),
]


def scan(path):
    """Returns (total_size, file_count, newest_mtime), ignoring build directories."""
    total = files = 0
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            try:
                st = os.stat(os.path.join(dirpath, name))
            except OSError:
                continue
            total += st.st_size
            files += 1
            newest = max(newest, st.st_mtime)
    return total, files, newest


def kind(path):
    try:
        names = set(os.listdir(path))
    except OSError:
        return "?"
    for marker, label in MARKERS:
        if marker in names:
            return label
    return "-"


def git_state(path):
    """Inspects a checkout. Returns a dict, or None if this is not a repo.

    Beyond "is it dirty", the interesting question is what has never left this
    disk: commits with no upstream to compare against, or ahead of one. That
    work is gone if the drive is.
    """
    # In a worktree or a submodule, .git is a file pointing elsewhere, not a
    # directory. Checking for a directory hides exactly the checkouts most
    # likely to be holding uncommitted work.
    if not os.path.exists(os.path.join(path, ".git")):
        return None

    def run(*args):
        """Output, or None if git could not answer.

        None and "" are different on purpose. `git status` that returns nothing
        means a clean tree; `git status` that could not run means nothing is
        known. Collapsing both to "" made an unreadable repository render as
        "0 uncommitted", which in a tool used to decide what is safe to delete
        is the dangerous direction of the mistake.
        """
        try:
            out = subprocess.run(
                ("git", "-C", path) + args,
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    # One call covers branch, upstream tracking and dirty files.
    raw_status = run("status", "--porcelain=v1", "-b")
    if raw_status is None:
        # Say so rather than describe a repository nobody could read.
        return {"branch": "?", "last": "", "dirty": None, "ahead": 0,
                "commits": 0, "has_remote": False, "has_upstream": False,
                "unreadable": True}
    status = raw_status.splitlines()
    header = status[0] if status and status[0].startswith("##") else ""
    dirty = len([line for line in status[1:] if line.strip()])

    branch = "?"
    ahead = 0
    has_upstream = False
    if header:
        head = header[2:].strip()
        # Shapes: "main...origin/main [ahead 2, behind 1]", "main", "No commits yet on main"
        tracking = re.match(r"(?:No commits yet on )?([^\s.]+)(\.\.\.(\S+))?(?:\s+\[(.+)\])?$", head)
        if tracking:
            branch = tracking.group(1)
            has_upstream = bool(tracking.group(3))
            counts = tracking.group(4) or ""
            match = re.search(r"ahead (\d+)", counts)
            if match:
                ahead = int(match.group(1))

    has_remote = bool(run("remote") or "")
    if not has_upstream:
        # Without an upstream, ahead is not measurable. Counting the whole
        # history as unpushed reads as alarming when the branch may in fact be
        # behind its remote, so only a repo with no remote at all is treated as
        # living solely on this disk.
        ahead = 0
    commits = run("rev-list", "--count", "HEAD") or ""

    return {
        "branch": branch,
        "last": (run("log", "-1", "--format=%cs %s") or "")[:90],
        "dirty": dirty,
        "ahead": ahead,
        "commits": int(commits) if commits.isdigit() else 0,
        "has_remote": has_remote,
        "has_upstream": has_upstream,
        "unreadable": False,
    }


def risk(git):
    """What is at stake here, as (label, count), or None.

    Only two situations mean work exists nowhere else: a repo with no remote at
    all, and commits measurably ahead of an upstream. A branch with no upstream
    but a configured remote is merely untracked, which says nothing either way.
    """
    if not git["has_remote"] and git["commits"]:
        return "no remote", git["commits"]
    if git["has_upstream"] and git["ahead"]:
        return "unpushed", git["ahead"]
    return None


def pitch(path):
    """First line of prose from the README, to recall what the project is for."""
    try:
        names = os.listdir(path)
    except OSError:
        # This walks other people's directories: one unreadable folder must not
        # take down the whole report.
        return ""
    for name in names:
        if name.lower().startswith("readme"):
            try:
                with open(os.path.join(path, name), encoding="utf-8", errors="replace") as f:
                    for raw in f:
                        line = raw.strip()
                        # Skip headings, badges, images and rules: we want the
                        # first sentence that says what the project does.
                        if not line or line.startswith(("#", "![", "[!", "<", "---", "=", "|", "*", "-")):
                            continue
                        return line[:120]
            except OSError:
                return ""
            return ""
    return ""


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024


PAGE = """<!doctype html><meta charset=utf-8><title>{title}</title>
<style>
body{{font:14px/1.5 system-ui;margin:2rem;background:#111;color:#ddd}}
table{{border-collapse:collapse;width:100%}}
th,td{{padding:.35rem .6rem;text-align:left;border-bottom:1px solid #333}}
th{{cursor:pointer;position:sticky;top:0;background:#1a1a1a;user-select:none}}
tr:hover{{background:#1c1c1c}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}}
.cold{{color:#777}}.hot{{color:#6c6}}
.desc{{color:#888;font-size:12px;max-width:38ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.commit{{color:#999;font-size:12px;max-width:44ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.dirty{{color:#d87;font-size:12px}}.nogit{{color:#555}}
.risk{{color:#e55;font-size:12px;font-weight:600}}
</style>
<h1>{title}</h1><p>{summary}</p>
<table><thead><tr>
<th>name</th><th>type</th><th>git</th><th>last commit</th><th>size</th><th>files</th><th>last written</th><th>days</th>
</tr></thead><tbody>
{rows}
</tbody></table>
<script>
document.querySelectorAll('th').forEach((th,i)=>th.onclick=()=>{{
  const tb=th.closest('table').tBodies[0], rows=[...tb.rows];
  th.desc=!th.desc;
  rows.sort((a,b)=>{{
    const x=a.cells[i], y=b.cells[i];
    const nx=x.dataset.v, ny=y.dataset.v;
    const r = nx!==undefined ? nx-ny : x.textContent.localeCompare(y.textContent);
    return th.desc ? -r : r;
  }});
  rows.forEach(r=>tb.appendChild(r));
}});
</script>
"""


def build(root):
    entries = []
    try:
        names = sorted(os.listdir(root))
    except OSError as err:
        raise SystemExit(f"cannot read {root}: {err}")
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isdir(path) or name in SKIP_DIRS:
            continue
        size, files, newest = scan(path)
        entries.append((name, kind(path), size, files, newest, git_state(path), pitch(path)))
    return entries


def render(root, entries):
    now = time.time()
    rows = []
    for name, k, size, files, newest, git, desc in entries:
        days = int((now - newest) / 86400) if newest else 9999
        stamp = time.strftime("%Y-%m-%d", time.localtime(newest)) if newest else "-"
        cls = "cold" if days > 90 else "hot"

        if git:
            git_cell = html.escape(git["branch"])
            if git.get("unreadable"):
                # Never render an unreadable repository as one with nothing to
                # lose: this table is read to decide what can go.
                git_cell += ' <span class="risk">git unreadable</span>'
            elif git["dirty"]:
                git_cell += f' <span class="dirty">{git["dirty"]} changed</span>'
            at_risk = risk(git)
            if at_risk:
                label, count = at_risk
                git_cell += f' <span class="risk">{count} {label}</span>'
            elif git["has_remote"] and not git["has_upstream"]:
                git_cell += ' <span class="dirty">untracked branch</span>'
            commit = html.escape(git["last"]) or "-"
        else:
            git_cell, commit = '<span class="nogit">-</span>', ""

        rows.append(
            f'<tr class="{cls}"><td>{html.escape(name)}'
            f'<div class="desc">{html.escape(desc)}</div></td>'
            f'<td>{k}</td>'
            f'<td>{git_cell}</td>'
            f'<td class="commit">{commit}</td>'
            f'<td class=n data-v="{size}">{human(size)}</td>'
            f'<td class=n data-v="{files}">{files}</td>'
            f'<td data-v="{int(newest)}">{stamp}</td>'
            f'<td class=n data-v="{days}">{days}</td></tr>'
        )
    total = sum(e[2] for e in entries)
    stale = sum(1 for e in entries if (now - e[4]) / 86400 > 90)
    gits = [e[5] for e in entries if e[5]]
    dirty = sum(1 for g in gits if g["dirty"])
    unreadable = sum(1 for g in gits if g.get("unreadable"))
    exposed = [g for g in gits if risk(g)]
    summary = (
        f"{len(entries)} directories, {human(total)} total, "
        f"{len(gits)} git repos of which {dirty} hold uncommitted work and "
        f"{len(exposed)} hold commits that exist nowhere else, "
        f"{stale} untouched for 90+ days (greyed out)."
    )
    # A repository nobody could read is not a repository with nothing in it,
    # and the counts above would otherwise quietly file it under clean.
    if unreadable:
        summary += f" {unreadable} could not be read and are counted in neither."
    return PAGE.format(title=html.escape(root), summary=summary, rows="\n".join(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--out", default="map.html")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    entries = build(root)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(root, entries))
    print(f"{len(entries)} directories -> {os.path.abspath(args.out)}")


def _selftest():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "proj", "node_modules"))
        open(os.path.join(d, "proj", "package.json"), "w").write("{}")
        open(os.path.join(d, "proj", "a.js"), "w").write("x" * 500)
        open(os.path.join(d, "proj", "node_modules", "huge.bin"), "wb").write(b"0" * 10_000)
        open(os.path.join(d, "loose.txt"), "w").write("y")
        open(os.path.join(d, "proj", "README.md"), "w").write("# Title\n\n![badge](x)\nDoes things.\n")

        entries = build(d)
        assert [e[0] for e in entries] == ["proj"], entries
        name, k, size, files, newest, git, desc = entries[0]
        assert k == "node", k
        assert files == 3, files  # node_modules ignored, README counted
        assert newest > 0
        assert git is None, "no .git means no git state"
        assert desc == "Does things.", desc  # heading and badge skipped
        page = render(d, entries)
        assert "package.json" not in page
        assert "Does things." in page

        # A real repo: branch, last commit and modified files.
        repo = os.path.join(d, "repo")
        os.makedirs(repo)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        run = lambda *a: subprocess.run(("git", "-C", repo) + a, capture_output=True, env=env)
        run("init", "-q")
        open(os.path.join(repo, "a.txt"), "w").write("one")
        run("add", "-A")
        run("commit", "-qm", "first pass")
        open(os.path.join(repo, "b.txt"), "w").write("uncommitted")

        state = git_state(repo)
        assert state is not None, "a repo must be detected"
        assert state["branch"] and state["branch"] != "?", state
        assert "first pass" in state["last"], state
        assert state["dirty"] == 1, state
        # No remote at all: the single commit exists only on this disk.
        assert state["has_remote"] is False, state
        assert risk(state) == ("no remote", 1), risk(state)
        assert "first pass" in render(d, build(d))

        # Un depot que git ne peut pas lire ne doit jamais compter comme propre :
        # cette table sert a decider ce qu'on supprime, et "0 changed" est
        # exactement le mensonge qui coute du travail. On simule l'echec en
        # rendant `git` introuvable pour la duree de l'appel.
        real_run = subprocess.run
        try:
            subprocess.run = lambda *a, **k: (_ for _ in ()).throw(OSError("git absent"))
            blind = git_state(repo)
        finally:
            subprocess.run = real_run
        assert blind is not None, "un .git present doit toujours donner un etat"
        assert blind["unreadable"] is True, blind
        assert blind["dirty"] is None, f"un depot illisible affiche {blind['dirty']} modifications"
        assert blind["branch"] == "?", blind
        page_blind = render(d, [(os.path.basename(repo), "python", 1, 1, time.time(), blind, "")])
        assert "git unreadable" in page_blind, "l'illisibilite doit se voir dans la page"
        assert "0 changed" not in page_blind, page_blind


        # With a remote and everything pushed, nothing is at risk.
        origin = os.path.join(d, "origin.git")
        subprocess.run(("git", "init", "-q", "--bare", origin), capture_output=True, env=env)
        run("remote", "add", "origin", origin)
        run("push", "-q", "-u", "origin", "HEAD")
        pushed = git_state(repo)
        assert pushed["has_remote"] and pushed["has_upstream"], pushed
        assert risk(pushed) is None, f"everything is pushed: {pushed}"

        # One more local commit: measurably ahead, and that one is only here.
        open(os.path.join(repo, "c.txt"), "w").write("later")
        run("add", "-A")
        run("commit", "-qm", "local only")
        ahead = git_state(repo)
        assert risk(ahead) == ("unpushed", 1), risk(ahead)
        assert "unpushed" in render(d, build(d)), "the risk must be visible in the report"

        # A branch with a remote but no upstream says nothing about what is
        # pushed: it may even be behind. Calling that "unpushed" cried wolf on a
        # real repo, so it must stay out of the risk list.
        run("checkout", "-qb", "side")
        open(os.path.join(repo, "d.txt"), "w").write("on a side branch")
        run("add", "-A")
        run("commit", "-qm", "side work")
        side = git_state(repo)
        assert side["has_remote"] and not side["has_upstream"], side
        assert risk(side) is None, f"an untracked branch is not proof of anything: {side}"
        assert "untracked branch" in render(d, build(d))
        run("checkout", "-q", "-")

        # In a worktree .git is a file, not a directory. These are the checkouts
        # most likely to hold uncommitted work, so they must not read as "no repo".
        wt = os.path.join(d, "worktree")
        added = subprocess.run(("git", "-C", repo, "worktree", "add", "-q", wt),
                               capture_output=True, env=env)
        if added.returncode == 0:
            assert os.path.isfile(os.path.join(wt, ".git")), "expected .git to be a file here"
            wt_state = git_state(wt)
            assert wt_state is not None, "a worktree must be detected as a repo"
            assert wt_state["last"], wt_state
            assert wt_state["commits"] > 0, wt_state
            # A fresh worktree branch tracks nothing. It inherits the repo's
            # remote, so its history is not stranded and it is not a risk.
            assert wt_state["has_upstream"] is False, wt_state
            assert risk(wt_state) is None, wt_state

        # An unreadable directory must not take down the whole report.
        assert pitch(os.path.join(d, "does-not-exist")) == ""
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
