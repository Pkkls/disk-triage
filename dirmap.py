"""Map directories: size, file count, last activity, git state, project type.

Generates a standalone sortable HTML page. Read only.
Usage: python dirmap.py [root] [--out map.html]
"""
import argparse
import html
import os
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
    """Returns (branch, last commit, modified file count), or None if not a repo."""
    if not os.path.isdir(os.path.join(path, ".git")):
        return None

    def run(*args):
        try:
            out = subprocess.run(
                ("git", "-C", path) + args,
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return out.stdout.strip() if out.returncode == 0 else ""

    branch = run("rev-parse", "--abbrev-ref", "HEAD") or "?"
    last = run("log", "-1", "--format=%cs %s")
    dirty = run("status", "--porcelain")
    return branch, last[:90], len(dirty.splitlines()) if dirty else 0


def pitch(path):
    """First line of prose from the README, to recall what the project is for."""
    for name in os.listdir(path) if os.path.isdir(path) else []:
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
    for name in sorted(os.listdir(root)):
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
            branch, last, dirty = git
            git_cell = html.escape(branch)
            if dirty:
                git_cell += f' <span class="dirty">{dirty} changed</span>'
            commit = html.escape(last) or "-"
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
    repos = sum(1 for e in entries if e[5])
    dirty = sum(1 for e in entries if e[5] and e[5][2])
    summary = (
        f"{len(entries)} directories, {human(total)} total, "
        f"{repos} git repos of which {dirty} hold uncommitted work, "
        f"{stale} untouched for 90+ days (greyed out)."
    )
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
        branch, last, dirty = state
        assert branch and branch != "?", branch
        assert "first pass" in last, last
        assert dirty == 1, dirty
        assert "first pass" in render(d, build(d))
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
