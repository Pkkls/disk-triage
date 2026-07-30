# disk-triage

Two read-only scripts for a download folder that got out of hand. Standard library only, no dependencies, nothing is ever deleted.

## dirmap.py

Builds a sortable HTML page of every directory under a root: size, file count, last write, detected project type, git branch, last commit, uncommitted file count, and the first line of the README so you can remember what a project was.

```sh
python dirmap.py ~/Downloads --out map.html
```

Directories untouched for 90+ days are greyed out. `node_modules`, `dist`, `target` and friends are excluded from the size, so the number reflects actual code rather than build output. The summary line calls out how many repos hold uncommitted work, which tends to be the useful part.

## dupescan.py

Finds byte-identical files and reports what reclaiming them would save, largest saving first.

```sh
python dupescan.py ~/Downloads --min-mb 5 --out duplicates.md
```

Files are grouped by size, then by a hash of their first 64 KB, and only then hashed in full, so most candidates are eliminated cheaply. The report is markdown listing every path in each group. Deleting is left to you on purpose: vendored toolchains and cache directories are legitimately duplicated and should not be touched.

## Verifying

```sh
python dirmap.py --selftest
python dupescan.py --selftest
```

Both build a temporary tree, including a real git repo for `dirmap`, and assert on the results.

## License

MIT
