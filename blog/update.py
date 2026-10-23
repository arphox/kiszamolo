"""Refresh the local article collection and its independent classifications."""

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "blog/content-mirror/automation"))

import sync
import catalog
from blog.enrichment.automation import classify


DIMENSIONS = classify.DIMENSIONS
REPORT_PREFIXES = {"topics": "", "content_nature": "content_"}


def confirm_reclassification(count):
    """Require an explicit answer from an interactive terminal, never piped input."""
    print(f"{count} korábban besorolt cikk módosult.", flush=True)
    if not sys.stdin.isatty():
        print("Az újbóli besoroláshoz emberi jóváhagyás kell; indítsd újra interaktív terminálból.", flush=True)
        return False
    try:
        return input(f"Újra besorolod ezt a {count} módosult cikket? [igen/nem] ").strip().casefold() == "igen"
    except EOFError:
        return False


def run_update(root=ROOT, *, progress=print, confirm=None):
    root = Path(root).resolve()
    progress("Cikkgyűjtemény frissítése…")
    sync_report = sync.run_sync(root, progress=progress)
    for error in sync_report.get("errors", []):
        progress(f"Letöltési hiba · {error['record']}: {error['error']}")

    with catalog.exclusive_run(root):
        source_versions = catalog.load_index(root)
        processing = classify.processing_store.load_processing(root)
        vocabulary = classify.load_json(root / "blog/enrichment/topics.json")
        allowed_topics = {topic["id"] for topic in vocabulary["topics"]}
        assignments = classify.validate_assignments(
            classify.load_json(root / "blog/enrichment/assignments.json"), allowed_topics)
        posts = sorted(article_id for article_id in sync_report["post_ids"]
                       if str(article_id) in source_versions)

        def pending(article_id):
            return classify.processing_store.pending_dimensions(
                article_id, source_versions[str(article_id)],
                assignments.get(str(article_id), {}), processing)

        scheduled = {d: {"new": set(), "changed": set()} for d in DIMENSIONS}
        for article_id in posts:
            aid = str(article_id)
            assignment = assignments.get(aid, {})
            work = pending(article_id)
            if any(dimension in assignment for dimension in work):
                try:
                    classify.reconcile_source(article_id, root=root, assignments=assignments, processing=processing)
                except classify.CLASSIFICATION_ERRORS:
                    pass  # Per-article processing reports the failure without blocking other articles.
                else:
                    work = pending(article_id)
            for dimension in work:
                kind = "changed" if dimension in assignment else "new"
                scheduled[dimension][kind].add(aid)

        report = dict(new_candidates=len(scheduled["topics"]["new"]),
                      changed_candidates=len(scheduled["topics"]["changed"]),
                      classified=[], reclassified=[], review_required=[], errors=[],
                      reclassification_approved=None,
                      content_new_candidates=len(scheduled["content_nature"]["new"]),
                      content_changed_candidates=len(scheduled["content_nature"]["changed"]),
                      content_classified=[], content_reclassified=[],
                      content_review_required=[], content_errors=[],
                      incomplete_downloads=sync_report["incomplete_records"])

        def refresh_report():
            report["pending_article_ids"] = [article_id for article_id in posts if "topics" in pending(article_id)]
            report["content_pending_article_ids"] = [article_id for article_id in posts if "content_nature" in pending(article_id)]

        def process_article(article_id, dimensions, *, reclassify):
            if not dimensions:
                return
            aid = str(article_id)
            phase = "újrabesorolás" if reclassify else "besorolás"
            progress(f"Besorolás ({', '.join(dimensions)}), {phase}: {aid}")
            for dimension, outcome in classify.process_article(aid, root=root, processing=processing,
                    assignments=assignments, dimensions=dimensions):
                prefix = REPORT_PREFIXES[dimension]
                if isinstance(outcome, Exception):
                    issue_kind = "review_required" if isinstance(outcome, classify.ReviewRequired) else "errors"
                    report[prefix + issue_kind].append(dict(id=article_id, reason=str(outcome)))
                    progress(f"Teendő · {aid}: {outcome}")
                elif outcome.get("saved"):
                    report[prefix + ("reclassified" if reclassify else "classified")].append(article_id)
                refresh_report()

        refresh_report()
        # Complete missing dimensions before asking about bulk reclassification.
        for article_id in posts:
            aid = str(article_id)
            work = [d for d in DIMENSIONS if aid in scheduled[d]["new"]]
            process_article(article_id, work, reclassify=False)

        changed_ids = set().union(*(scheduled[d]["changed"] for d in DIMENSIONS))
        if changed_ids:
            approved = len(changed_ids) <= 5 or (confirm or confirm_reclassification)(len(changed_ids))
            report["reclassification_approved"] = approved
            if approved:
                for article_id in posts:
                    aid = str(article_id)
                    work = [d for d in DIMENSIONS if aid in scheduled[d]["changed"]]
                    process_article(article_id, work, reclassify=True)
            else:
                progress(f"Újbóli besorolás elhalasztva: {len(changed_ids)} cikk függőben maradt.")

        refresh_report()
        progress(f"Kész: {len(report['classified'])} besorolva, {len(report['reclassified'])} újra besorolva; "
                 f"{len(report['pending_article_ids'])} cikk függőben, "
                 f"{report['incomplete_downloads']} hiányos letöltés.")
        progress(f"Tartalmi besorolás: {len(report['content_classified'])} új, "
                 f"{len(report['content_reclassified'])} frissített, "
                 f"{len(report['content_pending_article_ids'])} függőben.")
        return report


def main(argv=None, *, root=ROOT):
    parser = argparse.ArgumentParser(description="A helyi cikkgyűjtemény frissítése és besorolása.")
    parser.parse_args(argv)
    try:
        report = run_update(root)
        return 2 if (report["pending_article_ids"] or report["incomplete_downloads"]
                     or report["review_required"] or report["errors"]
                     or report["content_pending_article_ids"] or report["content_review_required"]
                     or report["content_errors"]) else 0
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
