Run from the project root:

```powershell
python .\automation\2026-10-08-article-inventory-check\check.py
```

Compares `articles/index.jsonl` and file existence with current public published WordPress posts. Does not read article files or write results. Reports all differences to the terminal; ANSI colors are disabled for redirected output or `NO_COLOR`.

Uses only the API, normalizing hostname case and trailing slashes without resolving redirects. Pagination/count errors abort the report. Count checks cannot detect every simultaneous website edit. Data collection/comparison and rendering are separate.

Exit code: 0 for a completed check (including differences), 1 for an error. Tests: `python -B -m unittest discover -s automation/2026-10-08-article-inventory-check -p "test_*.py"` from the project root.
