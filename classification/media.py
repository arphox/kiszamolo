"""Bounded preparation and compaction of article image inputs."""

from __future__ import annotations

import hashlib
import ipaddress
import io
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import warnings
from urllib.parse import urljoin, urlsplit

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:  # Keep the normal classification pipeline importable without Pillow.
    Image = None
    UnidentifiedImageError = OSError


class MediaError(ValueError):
    """An image could not be safely made available to the classifier."""


DEFAULT_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_FRAMES = 100
DEFAULT_MAX_PIXELS = 40_000_000
DEFAULT_MAX_TOTAL_PIXELS = 80_000_000
DEFAULT_TIMEOUT = (5, 15)
DEFAULT_MAX_REDIRECTS = 3
DEFAULT_MAX_DURATION = 30
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mov", ".mkv"}
DEFAULT_MAX_VIDEO_DURATION = 120
DEFAULT_MAX_VIDEO_FRAMES = 120


def _output_frames(image: Image.Image, scratch_dir: Path, *, max_frames: int,
                   max_pixels: int, max_total_pixels: int, max_bytes: int) -> list[Path]:
    frame_count = int(getattr(image, "n_frames", 1))
    if frame_count > max_frames:
        raise MediaError(f"Image has {frame_count} frames; limit is {max_frames}.")
    outputs: list[Path] = []
    by_digest: dict[str, Path] = {}
    total_pixels = 0
    for frame_no in range(frame_count):
        try:
            image.seek(frame_no)
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise MediaError(
                    f"Frame {frame_no} dimensions {width}x{height} exceed the {max_pixels}-pixel limit."
                )
            total_pixels += width * height
            if total_pixels > max_total_pixels:
                raise MediaError(
                    f"Image frames exceed the {max_total_pixels}-pixel total limit."
                )
            # RGBA also preserves alpha stored in palette-indexed PNG/GIF images.
            frame = image.convert("RGBA")
            pixels = frame.tobytes()
        except MediaError:
            raise
        except Exception as error:
            raise MediaError(f"Could not decode frame {frame_no}: {error}") from error
        digest = hashlib.sha256(
            width.to_bytes(4, "big") + height.to_bytes(4, "big") + frame.mode.encode("ascii") + pixels
        ).hexdigest()
        output = by_digest.get(digest)
        if output is None:
            output = scratch_dir / f"{digest}.png"
            if output.is_symlink() or (output.exists() and not output.is_file()):
                raise MediaError("Scratch output path is not a regular file.")
            if not output.exists():
                fd, temporary_name = tempfile.mkstemp(prefix=f".{digest}.", suffix=".tmp", dir=scratch_dir)
                os.close(fd)
                temporary = Path(temporary_name)
                try:
                    frame.save(temporary, format="PNG", optimize=False, compress_level=9)
                    if temporary.stat().st_size > max_bytes:
                        raise MediaError(f"Normalized frame exceeds the {max_bytes}-byte limit.")
                    os.replace(temporary, output)
                finally:
                    if temporary.exists():
                        temporary.unlink()
            by_digest[digest] = output
        outputs.append(output)
    return outputs


def _decode_bytes(data: bytes, scratch_dir: Path, *, max_frames: int,
                  max_pixels: int, max_total_pixels: int, max_bytes: int) -> list[Path]:
    if Image is None:
        raise MediaError("Image normalization is unavailable: Pillow is not installed.")
    if not data:
        raise MediaError("Image response is empty.")
    if len(data) > max_bytes:
        raise MediaError(f"Image input exceeds the {max_bytes}-byte limit.")
    scratch_dir.mkdir(parents=True, exist_ok=True)
    if not scratch_dir.is_dir():
        raise MediaError("Scratch path is not a directory.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data))
            try:
                return _output_frames(image, scratch_dir, max_frames=max_frames,
                                      max_pixels=max_pixels, max_total_pixels=max_total_pixels,
                                      max_bytes=max_bytes)
            finally:
                image.close()
    except MediaError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, OSError, ValueError) as error:
        raise MediaError(f"Unsupported or invalid image data: {error}") from error


def normalize_local_image(source: str | Path, scratch_dir: str | Path, *,
                          max_frames: int = DEFAULT_MAX_FRAMES,
                          max_pixels: int = DEFAULT_MAX_PIXELS,
                          max_total_pixels: int = DEFAULT_MAX_TOTAL_PIXELS,
                          max_bytes: int = DEFAULT_MAX_BYTES) -> list[Path]:
    """Validate static supported images in place; convert other inputs to PNG frames.

    The caller must resolve article-reference and article-root policy before calling.
    SVG is rejected unless a safe renderer is added; Pillow does not decode it.
    """
    path = Path(source)
    try:
        if path.is_symlink() or not path.is_file():
            raise MediaError("Local image must be a regular, non-symlink file.")
        if path.stat().st_size > max_bytes:
            raise MediaError(f"Local image exceeds the {max_bytes}-byte limit.")
        data = path.read_bytes()
    except MediaError:
        raise
    except OSError as error:
        raise MediaError(f"Cannot read local image {path}: {error}") from error
    if path.suffix.lower() == ".svg":
        raise MediaError("SVG normalization is unavailable: a safe SVG renderer is not installed.")
    passthrough = _supported_static_image(path, data, max_pixels=max_pixels)
    if passthrough:
        return [path]
    return _decode_bytes(data, Path(scratch_dir), max_frames=max_frames,
                         max_pixels=max_pixels, max_total_pixels=max_total_pixels,
                         max_bytes=max_bytes)


def _supported_static_image(path: Path, data: bytes, *, max_pixels: int) -> bool:
    if Image is None:
        raise MediaError("Image normalization is unavailable: Pillow is not installed.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data))
            try:
                if image.format not in {"JPEG", "PNG", "WEBP"} or getattr(image, "n_frames", 1) != 1:
                    return False
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > max_pixels:
                    raise MediaError(
                        f"Image dimensions {width}x{height} exceed the {max_pixels}-pixel limit."
                    )
                image.load()
                return True
            finally:
                image.close()
    except MediaError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, OSError, ValueError) as error:
        raise MediaError(f"Unsupported or invalid image data: {error}") from error


def compact_model_images(paths, scratch_dir: str | Path, *,
                         max_total_bytes: int = 8 * 1024 * 1024) -> list[Path]:
    """Deduplicate image payloads and JPEG-compact large PNGs if needed.

    Images remain full resolution and ordered. PNG alpha is composited on white for
    JPEG output. If the payload cannot fit without dropping images, fail explicitly.
    """
    if max_total_bytes <= 0:
        raise MediaError("Image payload byte limit must be positive.")
    unique: list[tuple[Path, bytes]] = []
    seen: set[str] = set()
    for path in paths:
        path = Path(path)
        try:
            data = path.read_bytes()
        except OSError as error:
            raise MediaError(f"Cannot read model image {path}: {error}") from error
        digest = hashlib.sha256(data).hexdigest()
        if digest not in seen:
            seen.add(digest)
            unique.append((path, data))
    total = sum(len(data) for _, data in unique)
    if total <= max_total_bytes:
        return [path for path, _ in unique]

    scratch = Path(scratch_dir)
    scratch.mkdir(parents=True, exist_ok=True)
    candidates = sorted((index for index, (path, data) in enumerate(unique)
                         if path.suffix.lower() == ".png"),
                        key=lambda index: len(unique[index][1]), reverse=True)
    for index in candidates:
        path, original = unique[index]
        try:
            with Image.open(io.BytesIO(original)) as source:
                source.load()
                rgba = source.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, "white")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
                best = None
                for quality in (90, 85, 80):
                    encoded = io.BytesIO()
                    rgb.save(encoded, format="JPEG", quality=quality, optimize=True)
                    candidate = encoded.getvalue()
                    if len(candidate) < len(original):
                        best = candidate
                    if best is not None and total - len(original) + len(best) <= max_total_bytes:
                        break
        except Exception as error:
            raise MediaError(f"Could not compact PNG image {path}: {error}") from error
        if best is None:
            continue
        digest = hashlib.sha256(best).hexdigest()
        output = scratch / f"{digest}.jpg"
        if not output.exists():
            temporary = scratch / f".{digest}.tmp"
            try:
                temporary.write_bytes(best)
                os.replace(temporary, output)
            finally:
                if temporary.exists():
                    temporary.unlink()
        unique[index] = (output, best)
        total = sum(len(data) for _, data in unique)
        if total <= max_total_bytes:
            break
    deduped: list[tuple[Path, bytes]] = []
    seen.clear()
    for candidate_path, candidate_data in unique:
        candidate_hash = hashlib.sha256(candidate_data).hexdigest()
        if candidate_hash not in seen:
            seen.add(candidate_hash)
            deduped.append((candidate_path, candidate_data))
    unique = deduped
    total = sum(len(data) for _, data in unique)
    if total > max_total_bytes:
        raise MediaError(
            f"Image payload is {total} bytes after deduplication and PNG compaction; "
            f"limit is {max_total_bytes} bytes."
        )
    return [path for path, _ in unique]


def normalize_local_video(source: str | Path, scratch_dir: str | Path, *,
                          sample_fps: float = 1,
                          max_duration: float = DEFAULT_MAX_VIDEO_DURATION,
                          max_frames: int = DEFAULT_MAX_VIDEO_FRAMES,
                          max_pixels: int = DEFAULT_MAX_PIXELS,
                          max_total_pixels: int = DEFAULT_MAX_TOTAL_PIXELS,
                          max_bytes: int = DEFAULT_MAX_BYTES,
                          probe_timeout: float = 10,
                          decode_timeout: float = 30) -> list[Path]:
    """Extract bounded visual samples from one caller-approved local video.

    The caller must enforce same-article path policy. No audio is decoded or transcribed.
    """
    path = Path(source)
    try:
        sample_fps = float(sample_fps)
    except (TypeError, ValueError) as error:
        raise MediaError("Video sample_fps must be greater than 0 and at most 1.") from error
    if not math.isfinite(sample_fps) or sample_fps <= 0 or sample_fps > 1:
        raise MediaError("Video sample_fps must be greater than 0 and at most 1.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise MediaError(f"Unsupported local video type: {path.suffix or '(no extension)'}.")
    try:
        if path.is_symlink() or not path.is_file():
            raise MediaError("Local video must be a regular, non-symlink file.")
        if path.stat().st_size > max_bytes:
            raise MediaError(f"Local video exceeds the {max_bytes}-byte limit.")
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    except MediaError:
        raise
    except OSError as error:
        raise MediaError(f"Cannot read local video {path}: {error}") from error

    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        raise MediaError("Local video sampling is unavailable: ffmpeg/ffprobe is not installed.")
    container = "mov" if path.suffix.lower() in {".mp4", ".m4v", ".mov"} else "matroska"
    probe_args = [ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe", "-f", container]
    if container == "mov":
        probe_args += ["-enable_drefs", "0", "-use_absolute_path", "0"]
    probe_args += ["-select_streams", "v:0", "-show_entries",
                  "stream=width,height,duration:format=duration", "-of", "json", str(path)]
    try:
        probe = subprocess.run(probe_args, capture_output=True, text=True, timeout=probe_timeout,
                               check=False, shell=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise MediaError(f"Could not inspect local video: {error}") from error
    if probe.returncode != 0:
        reason = (probe.stderr or "ffprobe failed").strip()[:500]
        raise MediaError(f"Could not inspect local video: {reason}")
    try:
        metadata = json.loads(probe.stdout)
        stream = metadata["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        duration_value = stream.get("duration") or metadata.get("format", {}).get("duration")
        duration = float(duration_value)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise MediaError(f"Local video has incomplete or invalid metadata: {error}") from error
    if not math.isfinite(duration) or duration <= 0 or duration > max_duration:
        raise MediaError(f"Local video duration must be between 0 and {max_duration} seconds.")
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise MediaError(f"Local video dimensions {width}x{height} exceed the pixel limit.")
    expected_frames = max(1, math.ceil(duration * sample_fps))
    if expected_frames > max_frames or expected_frames * width * height > max_total_pixels:
        raise MediaError("Local video samples exceed the frame or total-pixel limit.")

    scratch = Path(scratch_dir)
    scratch.mkdir(parents=True, exist_ok=True)
    output_dir = Path(tempfile.mkdtemp(prefix=f"video-{source_hash[:12]}-", dir=scratch))
    output_pattern = output_dir / "frame-%04d.png"
    decode_args = [ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
                   "-f", container]
    if container == "mov":
        decode_args += ["-enable_drefs", "0", "-use_absolute_path", "0"]
    decode_args += ["-i", str(path), "-map", "0:v:0", "-vf", f"fps={sample_fps:g}",
                    "-frames:v", str(max_frames + 1), "-start_number", "0", str(output_pattern)]
    try:
        try:
            decoded = subprocess.run(decode_args, capture_output=True, timeout=decode_timeout,
                                     check=False, shell=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise MediaError(f"Could not sample local video: {error}") from error
        if decoded.returncode != 0:
            reason = (decoded.stderr or b"ffmpeg failed").decode("utf-8", "replace").strip()[:500]
            raise MediaError(f"Could not sample local video: {reason}")
        frames = sorted(output_dir.glob("frame-*.png"))
        if not frames or len(frames) > max_frames:
            raise MediaError(f"Local video yielded {len(frames)} samples; limit is {max_frames}.")
        actual_total_pixels = 0
        for frame in frames:
            try:
                with Image.open(frame) as image:
                    frame_pixels = image.width * image.height
                    if frame_pixels > max_pixels:
                        raise MediaError(f"Decoded video frame {frame.name} exceeds the pixel limit.")
                    actual_total_pixels += frame_pixels
                    if actual_total_pixels > max_total_pixels:
                        raise MediaError("Decoded local video samples exceed the total-pixel limit.")
                    image.verify()
            except MediaError:
                raise
            except Exception as error:
                raise MediaError(f"Decoded video frame {frame.name} is invalid: {error}") from error
        return frames
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise


def _validate_public_url(url: str):
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise MediaError("Remote image URL must use HTTP or HTTPS and include a host.")
        if parsed.username is not None or parsed.password is not None:
            raise MediaError("Remote image URL must not contain credentials.")
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
            raise MediaError("Remote image URL targets a local host.")
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                addresses = [ipaddress.ip_address(item[4][0])
                             for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                                            type=socket.SOCK_STREAM)]
            except OSError as error:
                raise MediaError(f"Could not resolve remote image host {host}: {error}") from error
        if not addresses or any(not address.is_global for address in addresses):
            raise MediaError("Remote image URL resolves to a non-public address.")
        return parsed, addresses
    except MediaError:
        raise
    except (ValueError, UnicodeError) as error:
        raise MediaError(f"Invalid remote image URL: {error}") from error


def fetch_remote_image(url: str, scratch_dir: str | Path, *,
                       max_frames: int = DEFAULT_MAX_FRAMES,
                       max_pixels: int = DEFAULT_MAX_PIXELS,
                       max_total_pixels: int = DEFAULT_MAX_TOTAL_PIXELS,
                       max_bytes: int = DEFAULT_MAX_BYTES,
                       timeout: tuple[float, float] = DEFAULT_TIMEOUT,
                       max_duration: float = DEFAULT_MAX_DURATION,
                       max_redirects: int = DEFAULT_MAX_REDIRECTS,
                       session=None) -> list[Path]:
    """Fetch an explicitly referenced public image, pinning each connection to its DNS result.

    Redirects are followed manually and revalidated. The optional `session` is a
    requests-compatible test seam; production uses urllib3 directly so DNS cannot change
    between public-address validation and connection establishment.
    """
    try:
        import requests
        import urllib3
    except ImportError as error:
        raise MediaError("Remote image fetching is unavailable: requests/urllib3 is not installed.") from error

    current = url
    started = time.monotonic()
    for redirect_no in range(max_redirects + 1):
        remaining = max_duration - (time.monotonic() - started)
        if remaining <= 0:
            raise MediaError(f"Remote image operation exceeded {max_duration}-second limit.")
        parsed, addresses = _validate_public_url(current)
        request_timeout = (min(timeout[0], remaining), min(timeout[1], remaining))
        pool = None
        try:
            if session is not None:
                if hasattr(session, "trust_env"):
                    session.trust_env = False
                response = session.get(current, stream=True, allow_redirects=False,
                                       timeout=request_timeout)
                stream = response.iter_content(chunk_size=64 * 1024)
            else:
                address = str(addresses[0])
                port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
                authority = parsed.hostname
                if ":" in authority:
                    authority = f"[{authority}]"
                if parsed.port is not None:
                    authority += f":{parsed.port}"
                timeout_obj = urllib3.Timeout(connect=request_timeout[0], read=request_timeout[1])
                pool_args = dict(host=address, port=port, timeout=timeout_obj, retries=False, maxsize=1)
                if parsed.scheme.lower() == "https":
                    pool = urllib3.HTTPSConnectionPool(
                        **pool_args, server_hostname=parsed.hostname, assert_hostname=parsed.hostname,
                        cert_reqs="CERT_REQUIRED", ca_certs=requests.certs.where())
                else:
                    pool = urllib3.HTTPConnectionPool(**pool_args)
                target = parsed.path or "/"
                if parsed.query:
                    target += "?" + parsed.query
                response = pool.urlopen(
                    "GET", target, headers={"Host": authority}, redirect=False, retries=False,
                    timeout=urllib3.Timeout(connect=request_timeout[0], read=request_timeout[1]),
                    preload_content=False)
                def production_chunks():
                    while True:
                        chunk = response.read1(64 * 1024)
                        if not chunk:
                            return
                        yield chunk
                stream = production_chunks()
        except (requests.RequestException, urllib3.exceptions.HTTPError, OSError) as error:
            if pool is not None:
                pool.close()
            raise MediaError(f"Remote image request failed: {error}") from error

        try:
            status = getattr(response, "status", getattr(response, "status_code", None))
            headers = response.headers
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("Location")
                if not location:
                    raise MediaError("Remote image redirect has no Location header.")
                if redirect_no >= max_redirects:
                    raise MediaError(f"Remote image exceeded {max_redirects} redirects.")
                current = urljoin(current, location)
                continue
            if status != 200:
                raise MediaError(f"Remote image returned HTTP {status}.")
            declared = headers.get("Content-Length")
            if declared:
                try:
                    if int(declared) > max_bytes:
                        raise MediaError(f"Remote image exceeds the {max_bytes}-byte limit.")
                except ValueError as error:
                    raise MediaError("Remote image has an invalid Content-Length header.") from error
            chunks = bytearray()
            try:
                for chunk in stream:
                    if time.monotonic() - started >= max_duration:
                        raise MediaError(f"Remote image operation exceeded {max_duration}-second limit.")
                    if chunk:
                        chunks.extend(chunk)
                        if len(chunks) > max_bytes:
                            raise MediaError(f"Remote image exceeds the {max_bytes}-byte limit.")
            except MediaError:
                raise
            except (requests.RequestException, urllib3.exceptions.HTTPError, OSError) as error:
                raise MediaError(f"Remote image stream failed: {error}") from error
            return _decode_bytes(bytes(chunks), Path(scratch_dir), max_frames=max_frames,
                                 max_pixels=max_pixels, max_total_pixels=max_total_pixels,
                                 max_bytes=max_bytes)
        finally:
            response.close()
            if pool is not None:
                pool.close()
    raise MediaError("Remote image redirect handling failed.")
