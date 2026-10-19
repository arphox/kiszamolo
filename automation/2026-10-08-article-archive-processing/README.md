Run from the project root:

```powershell
python .\automation\2026-10-08-article-archive-processing\process.py
```

Splits `raw_source/kiszamolo_cikkek-1.txt` into `articles/YYYY/MM/YYYY-MM-DD HH.MM Title.txt` and creates `articles/index.jsonl` (title, original timezone-unspecified date, URL, relative path).

Only leading/trailing blank or whitespace-only lines are removed; all retained content is preserved. Filenames delete `?` and `"`, replace `:` with ` - ` and other invalid characters with spaces, collapse whitespace, and trim boundary spaces/dots/hyphens. Original titles remain in the articles and index.

Existing articles are skipped without content checks; an existing index must match exactly. Nothing is overwritten. Input and paths are validated before writing; interrupted writes can leave incomplete files, which must be manually deleted before rerunning.
