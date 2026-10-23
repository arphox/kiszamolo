"""Classify one locally downloaded article and update its canonical assignment."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import unquote, urljoin, urlsplit
from bs4 import BeautifulSoup
ROOT = Path(__file__).resolve().parents[3]
MODEL = "gpt-6.1-sol"
REASONING = "medium"
MAX_REQUEST_BYTES = 200_000
IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
MEDIA = IMAGES | set(".gif .svg .avif .bmp .tif .tiff .pdf .doc .docx .odt .ppt .pptx .xls .xlsx .csv .txt .rtf .mp3 .mp4 .m4a .m4v .wav .ogg .webm .mov .mkv .zip .rar .7z".split())
LINK = r'(<[^>]+>|[^\s)]+)(?:\s+"[^"]*")?'
IMAGE_LINK = re.compile(r'!\[[\s\S]*?\]\(' + LINK + r'\)')
DECORATIVE_IMAGES = {
    "https://kiszamolo.hu/hirdetes2.gif": "",
    "https://kiszamolo.hu/wp-includes/images/smilies/icon_smile.gif": ":)",
}
class ClassificationError(Exception):
    pass
class ReviewRequired(ClassificationError):
    pass
def load_json(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ClassificationError(f"Ismétlődő JSON-kulcs: {key}")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
def local_path(directory, target, boundary=None):
    parts = urlsplit(target.strip("<>"))
    if parts.scheme or parts.netloc:
        raise ReviewRequired(f"A hivatkozott tartalom nincs helyben: {target}")
    path = (directory / unquote(parts.path)).resolve()
    if not path.is_relative_to((boundary or directory).resolve()):
        raise ClassificationError(f"A helyi útvonal kilép a cikkgyűjteményből: {target}")
    return path
def article_row(root, article_id):
    matches = []
    with (root / "blog/content-mirror/articles/index.jsonl").open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            if str(row["id"]) == article_id and row["type"] == "post":
                matches.append(row)
    if len(matches) != 1:
        raise ClassificationError(f"A cikk nincs egyértelműen a helyi cikklistában: {article_id}. Előbb töltsd le.")
    row = matches[0]
    path = local_path(root / "blog/content-mirror/articles", row["path"])
    if not path.is_file():
        raise ClassificationError(f"A cikk nincs letöltve helyben: {article_id}. Előbb töltsd le.")
    return row, path
def clean_markdown(markdown, title):
    text = re.sub(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", "", markdown.lstrip("\ufeff"), count=1, flags=re.S)
    text = re.sub(r"(?m)^#{1,6}\s+" + re.escape(title) + r"\s*$", "", text)
    return text.strip()
def image_sources(image):
    nodes = [image]
    if image.parent.name == "picture":
        nodes += image.parent.find_all("source", recursive=False)
    ranked = []
    for node in nodes:
        for attribute in ("srcset", "data-srcset", "imagesrcset"):
            value = str(node.get(attribute, ""))
            while value.strip(" ,\t\r\n"):
                value = value.lstrip(" ,\t\r\n")
                token = re.match(r"\S+", value).group()
                value = value[len(token):]
                descriptor = ""
                if not token.endswith(","):
                    descriptor, _, value = value.partition(",")
                weight = float(descriptor.strip()[:-1]) if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?[wx]", descriptor.strip()) else 1
                ranked.append((weight, token.rstrip(",")))
    candidates = [url for _, url in sorted(ranked, reverse=True)]
    candidates += [str(image[name]) for name in ("data-src", "src") if image.get(name)]
    parent = image.find_parent("a", href=True)
    if parent and Path(urlsplit(str(parent["href"])).path).suffix.lower() in IMAGES:
        candidates.insert(0, str(parent["href"]))
    return candidates
def image_variants(row, root):
    decorations = dict(DECORATIVE_IMAGES)
    if not row.get("source_record"):
        return {}, decorations
    if row["source_record"] != f"records/posts/{row['id']}":
        raise ClassificationError("A forrásútvonal nem a besorolandó cikkhez tartozik.")
    source_root = root / ".local-cache/blog/content-mirror"
    source = local_path(source_root, row["source_record"]) / "view-source.html"
    soup = BeautifulSoup(source.read_text(encoding="utf-8"), "html.parser")
    assets = load_json(source_root / "state.json")["assets"]

    def cached(target):
        asset = assets.get(urljoin(row["url"], target), {})
        return local_path(root / "blog/content-mirror/articles", asset["path"]) if asset.get("path") else None

    for url, text in DECORATIVE_IMAGES.items():
        if path := cached(url):
            decorations[path] = text

    variants = {}
    for image in soup.find_all("img"):
        candidates = image_sources(image)
        preferred = next((path for url in candidates if (path := cached(url))
                          and path.is_file() and path.suffix.lower() in IMAGES), None)
        if preferred:
            for url in candidates:
                if path := cached(url):
                    variants[path] = preferred
    return variants, decorations
def replace_decorative_images(body, path, articles_root, decorations):
    """Replace only verified decoration URLs and their registered local copies."""
    def replacement(target):
        target = target.strip("<>")
        parts = urlsplit(target)
        key = (urljoin("https://kiszamolo.hu/", target) if parts.scheme or parts.netloc
               else local_path(path.parent, target, articles_root))
        return decorations.get(key)

    def markdown_image(match):
        linked = re.match(r'\]\(' + LINK + r'\)', body[match.end():])
        if linked and Path(urlsplit(linked.group(1).strip("<>")).path).suffix.lower() in IMAGES:
            return match.group(0)
        text = replacement(match.group(1))
        return match.group(0) if text is None else text

    def html_image(match):
        image = html_images.get(match.start())
        if image is None:
            return match.group(0)
        texts = [replacement(target) for target in image_sources(image)]
        if texts and texts[0] is not None and all(text == texts[0] for text in texts):
            return texts[0]
        return match.group(0)

    body = IMAGE_LINK.sub(markdown_image, body)
    # Keep parent sources/original links without reserializing the Markdown body.
    line_starts = [0] + [match.end() for match in re.finditer("\n", body)]
    soup = BeautifulSoup(body, "html.parser")
    html_images = {line_starts[image.sourceline - 1] + image.sourcepos: image
                   for image in soup.find_all("img")}
    return re.sub(r'''<img\b(?:[^>"']|"[^"]*"|'[^']*')*>''', html_image, body, flags=re.I)
def codex_exec(request, executable=None, timeout=600, *, model=None, reasoning=None):
    executable = executable or shutil.which("codex")
    if not executable:
        raise ClassificationError("A codex CLI nem található a PATH-on.")
    model = model or MODEL
    reasoning = reasoning or REASONING
    with tempfile.TemporaryDirectory(prefix="kiszamolo-classify-") as directory:
        workdir = Path(directory)
        if workdir.resolve().is_relative_to(ROOT.resolve()):
            raise ClassificationError("Az ideiglenes munkakönyvtárnak a projekten kívül kell lennie.")
        schema_path, result_path = workdir / "schema.json", workdir / "result.json"
        schema_path.write_text(json.dumps(request["schema"], ensure_ascii=False), encoding="utf-8")
        command = [executable, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                   "--sandbox", "read-only", "--model", model,
                   "-c", f"model_reasoning_effort='{reasoning}'", "-c", "forced_login_method='chatgpt'",
                   "-c", "web_search='disabled'", "--disable", "shell_tool", "--disable", "multi_agent",
                   "--disable", "apps", "--disable", "hooks", "--output-schema", str(schema_path),
                   "--output-last-message", str(result_path), "--json"]
        for number, image in enumerate(request["images"], 1):
            copy = workdir / f"image-{number}{image.suffix.lower()}"
            shutil.copyfile(image, copy)
            command += ["--image", str(copy)]
        command.append("-")
        env = os.environ.copy()
        for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_THREAD_ID"):
            env.pop(name, None)
        try:
            process = subprocess.run(command, input=request["prompt"], cwd=workdir, env=env,
                                     capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ClassificationError("A modellhívás időtúllépés miatt megállt; nincs automatikus újrapróbálkozás.") from None
        if process.returncode:
            details = _codex_failure_details(process.stdout, process.stderr)
            message = f"A Codex hívás sikertelen (kilépési kód: {process.returncode})."
            raise ClassificationError(f"{message} {details}" if details else message)
        for line in process.stdout.splitlines():
            event = json.loads(line)
            kind = event.get("type", "")
            if kind in {"error", "turn.failed"}:
                details = _codex_failure_details(process.stdout, process.stderr)
                message = "A Codex futás végzetes hibát jelzett."
                raise ClassificationError(f"{message} {details}" if details else message)
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            item_type = item.get("type", "")
            if (kind in {"item.started", "item.updated", "item.completed"}
                    and item_type == "error"):
                # Codex's item-level error is a nonfatal diagnostic; the output file
                # remains authoritative unless a top-level fatal event was emitted.
                continue
            if (any(word in kind.lower() for word in ("tool", "mcp", "command"))
                    or kind in {"item.started", "item.updated", "item.completed"}
                    and item_type not in {"reasoning", "agent_message"}):
                detail = f"{kind}/{item_type}"[:120]
                raise ClassificationError(f"A modell tiltott eszközt próbált használni ({detail}).")
        try:
            return load_json(result_path)
        except FileNotFoundError as error:
            details = _codex_failure_details(process.stdout, process.stderr)
            message = "A Codex futás nem készített eredményfájlt."
            raise ClassificationError(f"{message} {details}" if details else message) from error
def _codex_failure_details(stdout, stderr, limit=1000):
    """Extract bounded diagnostics from recognized Codex errors and stderr."""
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        message = event.get("message") if kind == "error" else None
        item = event.get("item")
        if (kind in {"item.started", "item.updated", "item.completed"}
                and isinstance(item, dict) and item.get("type") == "error"):
            message = item.get("message")
        if kind == "turn.failed":
            error = event.get("error")
            message = error.get("message") if isinstance(error, dict) else None
        if isinstance(message, str) and message.strip():
            events.append(" ".join(message.split()))
    event_details = " ".join(events)
    stderr_details = " ".join(stderr.split())
    if not event_details:
        return stderr_details[-limit:]
    if not stderr_details:
        return event_details[-limit:]
    # Reserve half of the bounded diagnostic for each source so stderr cannot hide a quota error.
    event_limit = limit // 2
    stderr_limit = limit - event_limit - 1
    return f"{event_details[-event_limit:]} {stderr_details[-stderr_limit:]}"
@contextmanager
def assignment_lock(path):
    with path.with_name(".assignments.lock").open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
