"""Refresh the local article collection and label new or changed articles."""

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "automation/content-mirror"))
sys.path.insert(0, str(ROOT / "tagging"))

import sync
import label


def confirm_retagging(count):
    """Require an explicit answer from an interactive terminal, never piped input."""
    print(f"{count} korábban címkézett cikk módosult.", flush=True)
    if not sys.stdin.isatty():
        print("Az újracímkézéshez emberi jóváhagyás kell; indítsd újra interaktív terminálból.", flush=True)
        return False
    try:
        return input(f"Újracímkézed ezt a {count} módosult cikket? [igen/nem] ").strip().casefold() == "igen"
    except EOFError:
        return False


def run_update(root=ROOT, *, progress=print, confirm=None):
    root = Path(root).resolve()
    source_root = root / "raw_source/content-mirror"
    articles_root = root / "articles"
    progress("Cikkgyűjtemény frissítése…")
    sync_report = sync.run_sync(source_root, articles_root=articles_root, progress=progress)

    # Hold the mirror's own locks while reading article inputs and acknowledging work.
    # A standalone sync cannot change those inputs during a model call or approval.
    with sync.exclusive_run(source_root), sync.exclusive_run(articles_root):
        state = sync.load_state(source_root)
        vocabulary = label.load_json(root / "tagging/tags.json")
        assignments = label.validate_assignments(
            label.load_json(root / "tagging/assignments.json"),
            {tag["id"] for tag in vocabulary["tags"]})
        posts = sorted((item for item in state["records"].values() if item["type"] == "post"),
                       key=lambda item: int(item["id"]))
        new = [item for item in posts if str(item["id"]) not in assignments]
        changed = [item for item in posts if str(item["id"]) in assignments and item.get("tagging_pending")]
        report = dict(started_at=sync.utc_now().isoformat(), new_candidates=len(new),
                      changed_candidates=len(changed), labeled=[], retagged=[],
                      review_required=[], errors=[], retagging_approved=None,
                      incomplete_downloads=sync_report["incomplete_records"])

        def save_report():
            report["pending_article_ids"] = [item["id"] for item in posts
                if item.get("tagging_pending") or str(item["id"]) not in assignments]
            report["updated_at"] = sync.utc_now().isoformat()
            sync.atomic_write(articles_root / "update-report.json", sync.json_bytes(report))

        def process(items, *, force):
            for number, item in enumerate(items, 1):
                aid = str(item["id"])
                progress(f"{'Újracímkézés' if force else 'Címkézés'}: {number}/{len(items)} · {aid} · {item['title']}")
                try:
                    result = label.label_article(aid, root=root, force=force)
                except label.ReviewRequired as error:
                    report["review_required"].append(dict(id=item["id"], title=item["title"], reason=str(error)))
                except (label.LabelError, OSError, ValueError, subprocess.TimeoutExpired) as error:
                    report["errors"].append(dict(id=item["id"], title=item["title"], reason=str(error)))
                else:
                    # The registry owns pending work; assignments contain only tags.
                    item.pop("tagging_pending", None)
                    sync.atomic_write(source_root / "state.json", sync.json_bytes(state))
                    assignments[aid] = {"tags": result["tags"]}
                    report["retagged" if force else "labeled"].append(item["id"])
                save_report()

        save_report()
        try:
            process(new, force=False)
            if changed:
                approved = len(changed) <= 5 or (confirm or confirm_retagging)(len(changed))
                report["retagging_approved"] = approved
                save_report()
                if approved:
                    process(changed, force=True)
                else:
                    progress(f"Újracímkézés elhalasztva: {len(changed)} cikk függőben maradt.")
        finally:
            sync.publish_article_index(articles_root, state)

        report["finished_at"] = sync.utc_now().isoformat()
        save_report()
        progress(f"Kész: {len(report['labeled'])} címkézve, {len(report['retagged'])} újracímkézve; "
                 f"{len(report['pending_article_ids'])} cikk függőben, "
                 f"{report['incomplete_downloads']} hiányos letöltés.")
        for kind in ("review_required", "errors"):
            for issue in report[kind]:
                progress(f"Teendő · {issue['id']} · {issue['title']}: {issue['reason']}")
        progress(f"Részletek: {articles_root / 'update-report.json'}")
        return report


def main(argv=None, *, root=ROOT):
    parser = argparse.ArgumentParser(description="A helyi cikkgyűjtemény frissítése és címkézése.")
    parser.parse_args(argv)
    try:
        report = run_update(root)
        return 2 if (report["pending_article_ids"] or report["incomplete_downloads"]
                     or report["review_required"] or report["errors"]) else 0
    except KeyboardInterrupt:
        print("Megszakítva; a megmaradt teendőkhöz indítsd újra a parancsot.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"A frissítés megállt: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    raise SystemExit(main())
