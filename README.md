# disk-triage

[![ci](https://github.com/Pkkls/disk-triage/actions/workflows/ci.yml/badge.svg)](https://github.com/Pkkls/disk-triage/actions/workflows/ci.yml)

Two read-only scripts for a download folder that got out of hand. Standard library only, no dependencies, nothing is ever deleted.

## dirmap.py

Builds a sortable HTML page of every directory under a root: size, file count, last write, detected project type, git branch, last commit, uncommitted file count, and the first line of the README so you can remember what a project was.

```sh
python dirmap.py ~/Downloads --out map.html
```

Worktrees and submodules count as repos, where `.git` is a file rather than a directory. They are often the checkouts holding forgotten uncommitted work, so treating them as "not a repo" would miss the point of the report.

Directories untouched for 90+ days are greyed out. `node_modules`, `dist`, `target` and friends are excluded from the size, so the number reflects actual code rather than build output. The summary line calls out how many repos hold uncommitted work, which tends to be the useful part.

## dupescan.py

Finds byte-identical files and reports what reclaiming them would save, largest saving first.

```sh
python dupescan.py ~/Downloads --min-mb 5 --out duplicates.md
```

Files are grouped by size, then by a hash of their first 64 KB, and only then hashed in full, so most candidates are eliminated cheaply. The report is markdown listing every path in each group. Deleting is left to you on purpose: vendored toolchains and cache directories are legitimately duplicated and should not be touched.

## secretscan.py

Scans a repository's whole history for credentials, not just the checkout. A key removed in a later commit is still in the history, and still public if the repo is.

```sh
python secretscan.py .                 # this repo, full history
python secretscan.py ../a ../b         # several at once
python secretscan.py . --head-only     # checkout only, much faster
```

Exit code 1 on a finding and 2 when a path could not be read, so it works as a pre-push hook without silently passing on a typo.

Matched values are never printed in full, only a masked prefix and a length: a scan report is itself a place a secret can leak. Placeholders are recognised and counted rather than reported, and patterns are anchored so they cannot match inside a base64 blob. Both rules come from real false positives: `ghp_xxxx...` in an `.env.example`, and a Flask session cookie in a test fixture that read as a Discord token.

Only high-confidence patterns are included. Rules like "32 hex characters" match commit hashes and minified assets, and a scanner that cries wolf is one nobody runs.

## Verifying

```sh
python dirmap.py --selftest
python dupescan.py --selftest
python secretscan.py --selftest
```

Each builds a temporary tree and asserts on the results, including real git repositories for `dirmap` and `secretscan`. The `secretscan` selftest commits a credential-shaped value, deletes it in a later commit, and checks it is still found: the deleted case is the whole point.

## License

MIT
