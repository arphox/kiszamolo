"""Compare the local index with public WordPress posts; write nothing."""

import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "articles"
API = "https://kiszamolo.hu/wp-json/wp/v2/posts"
PAGE_SIZE = 100


@dataclass(frozen=True)
class Article:
    title: str
    published_at: str
    url: str
    path: Path | None = None


@dataclass(frozen=True)
class Report:
    web_count: int
    local_count: int
    common_count: int
    common_files_present: int
    web_only: tuple[Article, ...]
    missing_files: tuple[Article, ...]
    local_only: tuple[Article, ...]


def normalize_url(value):
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise ValueError(f"Invalid article URL: {value!r}")
    parts = urlsplit(value)
    if (parts.scheme != "https" or parts.hostname is None
            or parts.hostname.lower() != "kiszamolo.hu" or parts.netloc.lower() != "kiszamolo.hu"
            or parts.query or parts.fragment):
        raise ValueError(f"Unexpected article URL: {value!r}")
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/") + "/", "", ""))


def validate_date(value):
    if not isinstance(value, str):
        raise ValueError("Article date must be a string")
    date = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    if date.isoformat() != value:
        raise ValueError(f"Invalid publication date: {value!r}")
    return value


def load_local(archive=ARCHIVE):
    articles = {}
    with (archive / "index.jsonl").open(encoding="utf-8") as index:
        for line_number, line in enumerate(index, 1):
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or set(record) != {"title", "published_at", "url", "path"}:
                    raise ValueError("Unexpected index schema")
                if not isinstance(record["title"], str) or not record["title"].strip():
                    raise ValueError("Empty or invalid title")
                relative = record["path"]
                if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
                    raise ValueError("Invalid relative article path")
                parts = relative.split("/")
                if any(part in {"", ".", ".."} for part in parts):
                    raise ValueError("Unsafe relative article path")
                target = archive.joinpath(*parts)
                if not target.resolve().is_relative_to(archive.resolve()):
                    raise ValueError("Article path escapes archive")
                key = normalize_url(record["url"])
                if key in articles:
                    raise ValueError("Duplicate article URL")
                articles[key] = Article(record["title"], validate_date(record["published_at"]), record["url"], target)
            except (ValueError, TypeError, KeyError) as error:
                raise ValueError(f"Index line {line_number}: {error}") from error
    if not articles:
        raise ValueError("Local index is empty")
    return articles


class TitleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_title(rendered):
    parser = TitleParser()
    parser.feed(rendered)
    return "".join(parser.parts).strip()


def fetch_page(page, page_size):
    query = urlencode(dict(status="publish", per_page=page_size, page=page,
                           order="asc", orderby="id", _fields="id,date,link,title,status,type"))
    request = Request(f"{API}?{query}", headers={"User-Agent": "KiszamoloLocalInventoryCheck/1.0", "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                total = int(response.headers["X-WP-Total"])
                pages = int(response.headers["X-WP-TotalPages"])
                posts = json.load(response)
            return posts, total, pages
        except HTTPError as error:
            if error.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(attempt + 1)
    raise RuntimeError("Unreachable retry state")


def load_web(fetch=fetch_page, progress=lambda message: None):
    articles, ids = {}, set()
    expected_total = expected_pages = None
    previous_id = 0
    for_page = 1
    while True:
        posts, total, pages = fetch(for_page, PAGE_SIZE)
        if total < 0 or pages != math.ceil(total / PAGE_SIZE):
            raise ValueError("Invalid API pagination headers")
        if expected_total is None:
            expected_total, expected_pages = total, pages
        elif (total, pages) != (expected_total, expected_pages):
            raise ValueError("Web inventory count changed during pagination; retry the complete check")
        expected_length = min(PAGE_SIZE, max(0, total - (for_page - 1) * PAGE_SIZE))
        if not isinstance(posts, list) or len(posts) != expected_length:
            raise ValueError(f"Incomplete API page {for_page}")
        for post in posts:
            if not isinstance(post, dict) or post.get("status") != "publish" or post.get("type") != "post":
                raise ValueError("Unexpected API post schema/status/type")
            post_id = post.get("id")
            if type(post_id) is not int or post_id <= previous_id or post_id in ids:
                raise ValueError("Duplicate or unordered API post ID")
            previous_id = post_id
            ids.add(post_id)
            key = normalize_url(post.get("link"))
            if key in articles:
                raise ValueError("Duplicate API article URL")
            title = post.get("title")
            if not isinstance(title, dict) or not isinstance(title.get("rendered"), str):
                raise ValueError("Invalid API title")
            articles[key] = Article(plain_title(title["rendered"]), validate_date(post.get("date")), post["link"])
        progress(f"API: {for_page}/{max(1, pages)} pages; {len(articles)}/{total} articles")
        if for_page >= pages:
            break
        for_page += 1
        time.sleep(0.1)
    # Recheck total after fetching the last page, without downloading article bodies.
    _, final_total, final_pages = fetch(1, 1)
    if final_total != expected_total or final_pages != final_total:
        raise ValueError("Web inventory count changed before completion; retry the complete check")
    if len(articles) != expected_total:
        raise ValueError("Incomplete web inventory")
    return articles


def compare(local, web):
    common = local.keys() & web.keys()
    missing = {key for key, article in local.items() if not article.path.is_file()}
    ordered = lambda articles: tuple(sorted(articles, key=lambda article: (article.published_at, article.url)))
    return Report(len(web), len(local), len(common), len(common - missing),
                  ordered(web[key] for key in web.keys() - local.keys()),
                  ordered(local[key] for key in missing),
                  ordered(local[key] for key in local.keys() - web.keys()))


def terminal_text(value):
    # Remote titles and local data must not inject terminal control sequences.
    return "".join(c if c.isprintable() else " " for c in value)


def render_report(report, stream=sys.stdout):
    colored = stream.isatty() and "NO_COLOR" not in os.environ
    def color(text, code):
        return f"\033[{code}m{text}\033[0m" if colored else text
    def write(text=""):
        print(text, file=stream)
    write(color("KISZÁMOLÓ | Cikkleltár ellenőrzése", "1;36"))
    write(f"Ellenőrzés: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    write(f"Webes publikált cikkek: {report.web_count} | Helyi index: {report.local_count}")
    write(f"Közös URL-ek: {report.common_count} | Ezekből helyi fájllal: {report.common_files_present}")
    ratio = report.common_files_present / report.web_count if report.web_count else 0
    filled = round(ratio * 30)
    write(color(f"[{('#' * filled).ljust(30, '.')}] {ratio:.2%} webes cikkhez van indexelt helyi fájl", "32" if ratio == 1 else "33"))
    write("A publikálási dátumok a forrás szerintiek; nem feltételezünk időzónát.")
    write("A csoportok átfedhetnek: csak helyi cikk fájlja is hiányozhat.")
    for label, articles, code in [
        ("CSAK WEBEN – hiányzik a helyi indexből", report.web_only, "33"),
        ("HIÁNYZÓ HELYI FÁJL – az indexelt útvonal nem fájl", report.missing_files, "31"),
        ("CSAK HELYBEN – hiányzik az API-leltárból", report.local_only, "35"),
    ]:
        write()
        write(color(f"{label}: {len(articles)}", f"1;{code}"))
        for article in articles:
            write(f"  {article.published_at} | {terminal_text(article.title)}")
            write(f"    {terminal_text(article.url)}")
            if label.startswith("HIÁNYZÓ"):
                write(f"    {terminal_text(str(article.path))}")
    write()
    write("Hatókör: a WordPress API nyilvános publikált cikkei; tartalmi összehasonlítás nélkül.")


def main():
    # Windows redirected streams may otherwise use a legacy code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        local = load_local()
        progress = (lambda message: print(message, file=sys.stderr)) if sys.stderr.isatty() else (lambda message: None)
        web = load_web(progress=progress)
        render_report(compare(local, web))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"ERROR: {terminal_text(str(error))}\nNo completeness report generated.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
