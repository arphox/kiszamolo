"""Refresh the local article collection and classify new or changed articles."""

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "automation/content-mirror"))

import sync
from topics import classify
from classification import classify as content_classifier


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

    # Hold the mirror's own locks while reading article inputs and acknowledging work.
    # A standalone sync cannot change those inputs during a model call or approval.
    with sync.exclusive_run(source_root), sync.exclusive_run(articles_root):
        state = sync.load_state(source_root)
        vocabulary = classify.load_json(root / "topics/topics.json")
        assignments = classify.validate_assignments(
            classify.load_json(root / "topics/assignments.json"),
            {topic["id"] for topic in vocabulary["topics"]})
        content_assignments = content_classifier.validate_assignments(
            classify.load_json(root / "classification/assignments.json"))
        posts = sorted((item for item in state["records"].values() if item["type"] == "post"),
                       key=lambda item: int(item["id"]))
        new = [item for item in posts if str(item["id"]) not in assignments]
        changed = [item for item in posts if str(item["id"]) in assignments and item.get("classification_pending")]
        new_ids = {str(item["id"]) for item in new}
        content_new = [item for item in posts if str(item["id"]) not in content_assignments and (
            item.get("content_classification_pending") or item.get("classification_pending")
            or str(item["id"]) in new_ids)]
        content_changed = [item for item in posts if str(item["id"]) in content_assignments
                           and item.get("content_classification_pending")]
        for item in content_new:
            item["content_classification_pending"] = True
        if content_new:
            sync.atomic_write(source_root / "state.json", sync.json_bytes(state))
        report = dict(started_at=sync.utc_now().isoformat(), new_candidates=len(new),
                      changed_candidates=len(changed), classified=[], reclassified=[],
                      review_required=[], errors=[], reclassification_approved=None,
                      content_new_candidates=len(content_new), content_changed_candidates=len(content_changed),
                      content_classified=[], content_reclassified=[], content_review_required=[], content_errors=[],
                      incomplete_downloads=sync_report["incomplete_records"])

        def save_report():
            report["pending_article_ids"] = [item["id"] for item in posts
                if item.get("classification_pending") or str(item["id"]) not in assignments]
            report["content_pending_article_ids"] = [item["id"] for item in posts
                if item.get("content_classification_pending")]
            report["updated_at"] = sync.utc_now().isoformat()
            sync.atomic_write(articles_root / "update-report.json", sync.json_bytes(report))

        def process(items, *, force, content=False):
            classifier = content_classifier if content else classify
            prefix = "content_" if content else ""
            pending_flag = "content_classification_pending" if content else "classification_pending"
            for number, item in enumerate(items, 1):
                aid = str(item["id"])
                kind = "Tartalmi" if content else "Témakörök szerinti"
                progress(f"{kind} {'újrabesorolás' if force else 'besorolás'}: {number}/{len(items)} · {aid} · {item['title']}")
                try:
                    result = classifier.classify_article(aid, root=root, force=force)
                except classify.ReviewRequired as error:
                    report[prefix + "review_required"].append(dict(id=item["id"], title=item["title"], reason=str(error)))
                except (classify.ClassificationError, OSError, ValueError, subprocess.TimeoutExpired) as error:
                    report[prefix + "errors"].append(dict(id=item["id"], title=item["title"], reason=str(error)))
                else:
                    # Each classifier acknowledges only its own registry flag.
                    item.pop(pending_flag, None)
                    sync.atomic_write(source_root / "state.json", sync.json_bytes(state))
                    if content:
                        content_assignments[aid] = {"content_nature": result["content_nature"]}
                    else:
                        assignments[aid] = {"topics": result["topics"]}
                    report[prefix + ("reclassified" if force else "classified")].append(item["id"])
                save_report()

        save_report()
        try:
            process(new, force=False)
            process(content_new, force=False, content=True)
            changed_ids = {item["id"] for item in changed + content_changed}
            if changed_ids:
                approved = len(changed_ids) <= 5 or (confirm or confirm_reclassification)(len(changed_ids))
                report["reclassification_approved"] = approved
                save_report()
                if approved:
                    process(changed, force=True)
                    process(content_changed, force=True, content=True)
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
        for kind in ("review_required", "errors", "content_review_required", "content_errors"):
            for issue in report[kind]:
                progress(f"Teendő · {issue['id']} · {issue['title']}: {issue['reason']}")
        progress(f"Részletek: {articles_root / 'update-report.json'}")
        return report


def main(argv=None, *, root=ROOT):
    parser = argparse.ArgumentParser(description="A helyi cikkgyűjtemény frissítése, témakörök és tartalmi jelleg besorolása.")
    parser.parse_args(argv)
    try:
        report = run_update(root)
        return 2 if (report["pending_article_ids"] or report["incomplete_downloads"]
                     or report["review_required"] or report["errors"] or report["content_pending_article_ids"]
                     or report["content_review_required"] or report["content_errors"]) else 0
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
