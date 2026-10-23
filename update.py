"""Refresh the local article collection and its independent classifications."""

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "automation/content-mirror"))

import sync
from classification import classify


DIMENSIONS = ("topics", "content_nature")
CLASSIFICATION_ERRORS = (classify.ClassificationError, OSError, ValueError, subprocess.TimeoutExpired)
PENDING_FLAGS = {"topics": "classification_pending",
                 "content_nature": "content_classification_pending"}
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
    source_root = root / "raw_source/content-mirror"
    articles_root = root / "articles"
    progress("Cikkgyűjtemény frissítése…")
    sync_report = sync.run_sync(source_root, articles_root=articles_root, progress=progress)

    # Hold both locks while preparing input, calling the model, and saving progress.
    with sync.exclusive_run(source_root), sync.exclusive_run(articles_root):
        state = sync.load_state(source_root)
        vocabulary = classify.load_json(root / "classification/topics.json")
        allowed_topics = {topic["id"] for topic in vocabulary["topics"]}
        assignments = classify.validate_assignments(
            classify.load_json(root / "classification/assignments.json"), allowed_topics)
        posts = sorted((item for item in state["records"].values() if item["type"] == "post"),
                       key=lambda item: int(item["id"]))
        scheduled = {}
        for dimension in DIMENSIONS:
            flag = PENDING_FLAGS[dimension]
            missing = {str(item["id"]) for item in posts if str(item["id"]) not in assignments
                       or dimension not in assignments[str(item["id"])]}
            changed = {str(item["id"]) for item in posts if str(item["id"]) in assignments
                       and dimension in assignments[str(item["id"])] and item.get(flag)}
            scheduled[dimension] = {"new": missing, "changed": changed}
            # Persist retry state before any work so interruption cannot lose missing fields.
            for item in posts:
                if str(item["id"]) in missing:
                    item[flag] = True
        if any(scheduled[d]["new"] for d in DIMENSIONS):
            sync.atomic_write(source_root / "state.json", sync.json_bytes(state))

        report = dict(started_at=sync.utc_now().isoformat(),
                      new_candidates=len(scheduled["topics"]["new"]),
                      changed_candidates=len(scheduled["topics"]["changed"]),
                      classified=[], reclassified=[], review_required=[], errors=[],
                      reclassification_approved=None,
                      content_new_candidates=len(scheduled["content_nature"]["new"]),
                      content_changed_candidates=len(scheduled["content_nature"]["changed"]),
                      content_classified=[], content_reclassified=[],
                      content_review_required=[], content_errors=[],
                      incomplete_downloads=sync_report["incomplete_records"])

        def save_report():
            report["pending_article_ids"] = [item["id"] for item in posts
                if item.get("classification_pending") or "topics" not in assignments.get(str(item["id"]), {})]
            report["content_pending_article_ids"] = [item["id"] for item in posts
                if item.get("content_classification_pending") or "content_nature" not in assignments.get(str(item["id"]), {})]
            report["updated_at"] = sync.utc_now().isoformat()
            sync.atomic_write(articles_root / "update-report.json", sync.json_bytes(report))

        def process_article(item, dimensions, *, reclassify):
            if not dimensions:
                return
            aid = str(item["id"])
            title = item["title"]
            phase = "újrabesorolás" if reclassify else "besorolás"
            progress(f"Besorolás ({', '.join(dimensions)}), {phase}: {aid} · {title}")
            def record_issue(dimension, error):
                prefix = REPORT_PREFIXES[dimension]
                issue_kind = "review_required" if isinstance(error, classify.ReviewRequired) else "errors"
                report[prefix + issue_kind].append(dict(id=item["id"], title=title, reason=str(error)))
                progress(f"Teendő · {aid} · {title}: {error}")
                save_report()

            def process_prepared(prepared):
                for dimension in dimensions:
                    prefix = REPORT_PREFIXES[dimension]
                    flag = PENDING_FLAGS[dimension]
                    try:
                        classify.classify_article(aid, root=root, dimension=dimension,
                                                  force=reclassify, prepared=prepared)
                        refreshed = classify.validate_assignments(
                            classify.load_json(root / "classification/assignments.json"), allowed_topics)
                        if aid not in refreshed or dimension not in refreshed[aid]:
                            raise classify.ClassificationError(
                                f"A(z) {dimension} besorolás mentése nem található a nyilvántartásban.")
                        assignments[aid] = refreshed[aid]
                        # Keep the retry flag if persisting its removal fails.
                        had_flag = item.get(flag)
                        item.pop(flag, None)
                        try:
                            sync.atomic_write(source_root / "state.json", sync.json_bytes(state))
                        except Exception:
                            item[flag] = had_flag if had_flag is not None else True
                            raise
                    except CLASSIFICATION_ERRORS as error:
                        record_issue(dimension, error)
                        continue
                    report[prefix + ("reclassified" if reclassify else "classified")].append(item["id"])
                    save_report()

            try:
                entered = False
                with classify.prepare_article(aid, root=root) as prepared:
                    entered = True
                    process_prepared(prepared)
            except CLASSIFICATION_ERRORS as error:
                if entered:
                    # An exception leaving the prepared context indicates a cleanup failure.
                    raise
                for dimension in dimensions:
                    record_issue(dimension, error)

        save_report()
        try:
            # Complete all missing dimensions before asking about bulk reclassification.
            for item in posts:
                aid = str(item["id"])
                work = [d for d in DIMENSIONS if aid in scheduled[d]["new"]]
                process_article(item, work, reclassify=False)

            changed_ids = set().union(*(scheduled[d]["changed"] for d in DIMENSIONS))
            if changed_ids:
                approved = len(changed_ids) <= 5 or (confirm or confirm_reclassification)(len(changed_ids))
                report["reclassification_approved"] = approved
                save_report()
                if approved:
                    for item in posts:
                        aid = str(item["id"])
                        work = [d for d in DIMENSIONS if aid in scheduled[d]["changed"]]
                        process_article(item, work, reclassify=True)
                else:
                    progress(f"Újbóli besorolás elhalasztva: {len(changed_ids)} cikk függőben maradt.")
        finally:
            sync.publish_article_index(articles_root, state)

        report["finished_at"] = sync.utc_now().isoformat()
        save_report()
        progress(f"Kész: {len(report['classified'])} besorolva, {len(report['reclassified'])} újra besorolva; "
                 f"{len(report['pending_article_ids'])} cikk függőben, "
                 f"{report['incomplete_downloads']} hiányos letöltés.")
        progress(f"Tartalmi besorolás: {len(report['content_classified'])} új, "
                 f"{len(report['content_reclassified'])} frissített, "
                 f"{len(report['content_pending_article_ids'])} függőben.")
        progress(f"Részletek: {articles_root / 'update-report.json'}")
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
