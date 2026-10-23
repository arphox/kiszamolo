"""Offline tests for image normalization and remote-source safety."""

import io
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image, features

from blog.enrichment.automation.media import (MediaError, fetch_remote_image, normalize_local_image,
                                  normalize_local_video, compact_model_images)


def png_bytes(color=(255, 0, 0), size=(2, 2)):
    image = Image.new("RGB", size, color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size):
        yield self._body

    def close(self):
        self.closed = True


class FakeSession:
    trust_env = True

    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.urls.append((url, kwargs))
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("CLASSIFICATION_TEST_TMP"))
        self.root = Path(self.temp.name)
        self.scratch = self.root / "scratch"

    def tearDown(self):
        self.temp.cleanup()

    def test_gif_frames_preserved_and_identical_outputs_deduplicated(self):
        gif = io.BytesIO()
        frames = [Image.new("RGB", (3, 2), color) for color in ("red", "blue", "red")]
        frames[0].save(gif, format="GIF", save_all=True, append_images=frames[1:], duration=100,
                       loop=0, disposal=2)
        source = self.root / "frames.gif"
        source.write_bytes(gif.getvalue())

        paths = normalize_local_image(source, self.scratch)

        self.assertEqual(len(paths), 3)
        self.assertEqual(paths[0], paths[2])
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual(len(list(self.scratch.glob("*.png"))), 2)
        with Image.open(paths[2]) as normalized:
            self.assertEqual(normalized.size, (3, 2))

    def test_palette_gif_transparency_is_preserved(self):
        frame = Image.new("P", (2, 1))
        frame.putpalette([255, 0, 0, 0, 0, 255] + [0, 0, 0] * 254)
        frame.putdata([0, 1])
        frame.info["transparency"] = 0
        source = self.root / "transparent.gif"
        frame.save(source, format="GIF", transparency=0)

        [normalized_path] = normalize_local_image(source, self.scratch)
        with Image.open(normalized_path) as normalized:
            self.assertEqual(normalized.mode, "RGBA")
            self.assertEqual(normalized.getpixel((0, 0))[3], 0)
            self.assertEqual(normalized.getpixel((1, 0))[3], 255)

    def test_tiff_multipage_and_bmp_normalize(self):
        first, second = Image.new("RGB", (2, 2), "red"), Image.new("RGB", (2, 2), "blue")
        tiff = self.root / "pages.tif"
        first.save(tiff, format="TIFF", save_all=True, append_images=[second])
        bmp = self.root / "single.bmp"
        first.save(bmp, format="BMP")
        self.assertEqual(len(normalize_local_image(tiff, self.scratch)), 2)
        self.assertEqual(len(normalize_local_image(bmp, self.scratch)), 1)

    def test_supported_static_jpeg_passes_through_unchanged(self):
        for extension, image_format in ((".jpg", "JPEG"), (".png", "PNG"), (".webp", "WEBP")):
            with self.subTest(image_format=image_format):
                source = self.root / f"photo{extension}"
                Image.new("RGB", (32, 24), "blue").save(source, format=image_format)
                original = source.read_bytes()

                self.assertEqual(normalize_local_image(source, self.scratch), [source])
                self.assertEqual(source.read_bytes(), original)

    @unittest.skipUnless(features.check("avif"), "Pillow AVIF support is unavailable")
    def test_avif_normalizes_when_codec_is_available(self):
        avif = self.root / "single.avif"
        Image.new("RGB", (2, 2), "green").save(avif, format="AVIF")
        self.assertEqual(len(normalize_local_image(avif, self.scratch)), 1)

    def test_corrupt_svg_and_limits_fail_explicitly(self):
        corrupt = self.root / "broken.gif"
        corrupt.write_bytes(b"not an image")
        with self.assertRaisesRegex(MediaError, "invalid image"):
            normalize_local_image(corrupt, self.scratch)
        svg = self.root / "image.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        with self.assertRaisesRegex(MediaError, "SVG normalization is unavailable"):
            normalize_local_image(svg, self.scratch)
        png = self.root / "too-large.png"
        png.write_bytes(png_bytes(size=(5, 4)))
        with self.assertRaisesRegex(MediaError, "pixel limit"):
            normalize_local_image(png, self.scratch, max_pixels=19)
        with self.assertRaisesRegex(MediaError, "byte limit"):
            normalize_local_image(png, self.scratch, max_bytes=10)

    def test_frame_limit_does_not_truncate_animation(self):
        gif = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(
            gif, format="GIF", save_all=True,
            append_images=[Image.new("RGB", (2, 2), "blue")], duration=100)
        source = self.root / "two.gif"
        source.write_bytes(gif.getvalue())
        with self.assertRaisesRegex(MediaError, "2 frames; limit is 1"):
            normalize_local_image(source, self.scratch, max_frames=1)
        with self.assertRaisesRegex(MediaError, "total limit"):
            normalize_local_image(source, self.scratch, max_total_pixels=7)

    @patch("blog.enrichment.automation.media.socket.getaddrinfo")
    def test_remote_fetch_normalizes_with_bounded_request(self, resolve):
        resolve.return_value = [(None, None, None, None, ("93.184.216.34", 443))]
        response = FakeResponse(body=png_bytes(), headers={"Content-Length": str(len(png_bytes()))})
        session = FakeSession([response])
        paths = fetch_remote_image("https://images.example/pic", self.scratch, session=session)
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].is_file())
        self.assertFalse(session.trust_env)
        self.assertEqual(session.urls[0][1]["allow_redirects"], False)
        self.assertIsNotNone(session.urls[0][1]["timeout"])

    @patch("blog.enrichment.automation.media.socket.getaddrinfo")
    def test_remote_rejects_private_targets_and_redirects(self, resolve):
        resolve.return_value = [(None, None, None, None, ("127.0.0.1", 80))]
        with self.assertRaisesRegex(MediaError, "non-public"):
            fetch_remote_image("http://evil.example/image.png", self.scratch,
                               session=FakeSession([]))

        resolve.return_value = [(None, None, None, None, ("93.184.216.34", 443))]
        session = FakeSession([FakeResponse(302, headers={"Location": "http://127.0.0.1/secret"})])
        with self.assertRaisesRegex(MediaError, "non-public"):
            fetch_remote_image("https://images.example/image.png", self.scratch, session=session)
        self.assertEqual(len(session.urls), 1)

    @patch("blog.enrichment.automation.media.socket.getaddrinfo")
    def test_remote_response_size_limit(self, resolve):
        resolve.return_value = [(None, None, None, None, ("93.184.216.34", 443))]
        session = FakeSession([FakeResponse(body=b"x" * 30)])
        with self.assertRaisesRegex(MediaError, "byte limit"):
            fetch_remote_image("https://images.example/image.png", self.scratch,
                               max_bytes=20, session=session)

    @patch("blog.enrichment.automation.media.socket.getaddrinfo")
    def test_remote_stream_errors_become_optional_media_errors(self, resolve):
        import requests

        resolve.return_value = [(None, None, None, None, ("93.184.216.34", 443))]
        class BrokenResponse(FakeResponse):
            def iter_content(self, chunk_size):
                raise requests.Timeout("stream timed out")
                yield b""

        with self.assertRaisesRegex(MediaError, "stream failed: stream timed out"):
            fetch_remote_image("https://images.example/image.png", self.scratch,
                               session=FakeSession([BrokenResponse()]))

    @patch("blog.enrichment.automation.media.socket.getaddrinfo")
    def test_remote_connects_to_validated_ip_with_original_tls_and_host_names(self, resolve):
        resolve.side_effect = [
            [(None, None, None, None, ("93.184.216.34", 443))],
            [(None, None, None, None, ("127.0.0.1", 443))],
        ]
        response = MagicMock()
        response.status = 200
        response.headers = {"Content-Length": str(len(png_bytes()))}
        response.read1.side_effect = [png_bytes(), b""]
        pool = MagicMock()
        pool.urlopen.return_value = response

        with patch("urllib3.HTTPSConnectionPool", return_value=pool) as create_pool:
            paths = fetch_remote_image("https://images.example:8443/pic?q=1", self.scratch)

        self.assertEqual(len(paths), 1)
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(create_pool.call_args.kwargs["host"], "93.184.216.34")
        self.assertEqual(create_pool.call_args.kwargs["port"], 8443)
        self.assertEqual(create_pool.call_args.kwargs["server_hostname"], "images.example")
        self.assertEqual(create_pool.call_args.kwargs["assert_hostname"], "images.example")
        self.assertEqual(create_pool.call_args.kwargs["cert_reqs"], "CERT_REQUIRED")
        self.assertEqual(pool.urlopen.call_args.args[:2], ("GET", "/pic?q=1"))
        self.assertEqual(pool.urlopen.call_args.kwargs["headers"], {"Host": "images.example:8443"})
        self.assertEqual(response.read1.call_count, 2)
        response.stream.assert_not_called()
        response.close.assert_called_once()
        pool.close.assert_called_once()

    @patch("blog.enrichment.automation.media.socket.getaddrinfo")
    def test_remote_total_deadline_checks_each_available_chunk(self, resolve):
        resolve.return_value = [(None, None, None, None, ("93.184.216.34", 443))]
        response = MagicMock()
        response.status = 200
        response.headers = {}
        chunks = iter([b"part1", b"part2", b"part3"])
        clock = [0.0]

        def read_available(_amount):
            clock[0] += 6.0
            return next(chunks)

        response.read1.side_effect = read_available
        pool = MagicMock()
        pool.urlopen.return_value = response
        with patch("urllib3.HTTPSConnectionPool", return_value=pool), \
                patch("blog.enrichment.automation.media.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(MediaError, "exceeded 15-second limit"):
                fetch_remote_image("https://images.example/pic", self.scratch, max_duration=15)
        self.assertEqual(response.read1.call_count, 3)
        response.stream.assert_not_called()
        response.close.assert_called_once()

    def test_remote_deadline_stops_before_request(self):
        session = FakeSession([])
        with self.assertRaisesRegex(MediaError, "0-second limit"):
            fetch_remote_image("https://images.example/pic", self.scratch,
                               max_duration=0, session=session)
        self.assertEqual(session.urls, [])

    def test_local_video_duration_and_file_type_limits_fail_before_decode(self):
        source = self.root / "long.mp4"
        source.write_bytes(b"local video placeholder")
        probe_result = subprocess.CompletedProcess(
            [], 0, json.dumps({"streams": [{"width": 16, "height": 16, "duration": "121"}]}), "")
        with patch("blog.enrichment.automation.media.shutil.which", side_effect=["ffprobe.exe", "ffmpeg.exe"]), \
                patch("blog.enrichment.automation.media.subprocess.run", return_value=probe_result) as run:
            with self.assertRaisesRegex(MediaError, "duration must be between"):
                normalize_local_video(source, self.scratch)
        run.assert_called_once()  # ffprobe only; ffmpeg is not invoked for an over-limit video.
        probe_args = run.call_args.args[0]
        self.assertEqual(probe_args[probe_args.index("-f") + 1], "mov")
        self.assertIn("-enable_drefs", probe_args)
        self.assertIn("-use_absolute_path", probe_args)

        text_file = self.root / "playlist.m3u8"
        text_file.write_text("#EXTM3U", encoding="utf-8")
        with self.assertRaisesRegex(MediaError, "Unsupported local video type"):
            normalize_local_video(text_file, self.scratch)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "ffmpeg and ffprobe are unavailable")
    def test_local_video_emits_bounded_sampled_png_frames(self):
        source = self.root / "clip.mp4"
        generated = subprocess.run(
            [shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
             "color=c=red:s=16x16:r=2:d=2", "-an", "-c:v", "mpeg4", "-y", str(source)],
            capture_output=True, timeout=10, check=False, shell=False)
        if generated.returncode:
            self.skipTest("ffmpeg lacks a usable local lavfi/video encoder")

        frames = normalize_local_video(source, self.scratch)

        self.assertEqual(len(frames), 2)
        self.assertTrue(all(frame.suffix == ".png" and frame.is_file() for frame in frames))
        for frame in frames:
            with Image.open(frame) as image:
                self.assertEqual(image.size, (16, 16))
        sampled = normalize_local_video(source, self.scratch, sample_fps=0.5)
        self.assertEqual(len(sampled), 1)
        for invalid_fps in (0, -0.1, 1.01):
            with self.assertRaisesRegex(MediaError, "sample_fps"):
                normalize_local_video(source, self.scratch, sample_fps=invalid_fps)

    def test_local_video_total_pixel_limit_uses_actual_frame_dimensions(self):
        source = self.root / "changing-resolution.mp4"
        source.write_bytes(b"mock local video")
        metadata = json.dumps({"streams": [{"width": 4, "height": 4, "duration": "2"}]})

        def fake_run(args, **kwargs):
            if "ffprobe.exe" in args[0]:
                return subprocess.CompletedProcess(args, 0, metadata, "")
            output_pattern = Path(args[-1])
            for frame_index in range(2):
                Image.new("RGB", (8, 8), "red").save(
                    output_pattern.parent / f"frame-{frame_index:04d}.png")
            return subprocess.CompletedProcess(args, 0, b"", b"")

        with patch("blog.enrichment.automation.media.shutil.which", side_effect=["ffprobe.exe", "ffmpeg.exe"]), \
                patch("blog.enrichment.automation.media.subprocess.run", side_effect=fake_run):
            with self.assertRaisesRegex(MediaError, "total-pixel limit"):
                normalize_local_video(source, self.scratch, max_total_pixels=40)

    def test_model_image_compaction_deduplicates_and_keeps_small_inputs_unchanged(self):
        first = self.root / "first.png"
        second = self.root / "duplicate.png"
        first.write_bytes(png_bytes())
        second.write_bytes(first.read_bytes())

        result = compact_model_images([first, second], self.scratch, max_total_bytes=1000)

        self.assertEqual(result, [first])
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_model_image_compaction_reencodes_png_without_resizing(self):
        png = self.root / "noise.png"
        rng = random.Random(7)
        image = Image.frombytes("RGB", (1024, 1024), rng.randbytes(1024 * 1024 * 3))
        image.save(png, format="PNG")
        self.assertGreater(png.stat().st_size, 1_000_000)

        [compact] = compact_model_images([png], self.scratch, max_total_bytes=700_000)

        self.assertEqual(compact.suffix, ".jpg")
        self.assertLess(compact.stat().st_size, 700_000)
        with Image.open(compact) as image:
            self.assertEqual(image.size, (1024, 1024))

    def test_model_image_compaction_composites_png_alpha_on_white(self):
        png = self.root / "transparent-noise.png"
        rng = random.Random(11)
        rgba = bytearray(rng.randbytes(512 * 512 * 3))
        pixels = bytearray()
        for offset in range(0, len(rgba), 3):
            pixels.extend(rgba[offset:offset + 3])
            pixels.append(0)  # fully transparent, despite noisy RGB under the alpha
        Image.frombytes("RGBA", (512, 512), bytes(pixels)).save(png, format="PNG")
        self.assertGreater(png.stat().st_size, 256 * 1024)

        [compact] = compact_model_images([png], self.scratch, max_total_bytes=25_000)

        with Image.open(compact) as image:
            self.assertEqual(image.size, (512, 512))
            self.assertEqual(image.getpixel((20, 20)), (255, 255, 255))

    def test_model_image_compaction_fails_if_budget_requires_dropping_images(self):
        jpeg = self.root / "large.jpg"
        rng = random.Random(13)
        Image.frombytes("RGB", (256, 256), rng.randbytes(256 * 256 * 3)).save(jpeg, format="JPEG")
        with self.assertRaisesRegex(MediaError, "after deduplication and PNG compaction"):
            compact_model_images([jpeg], self.scratch, max_total_bytes=10)

    def test_many_small_png_frames_are_compacted_when_the_combined_payload_is_large(self):
        paths = []
        for index in range(20):
            source = self.root / f"small-frame-{index:02d}.png"
            rng = random.Random(index)
            Image.frombytes("RGB", (128, 128), rng.randbytes(128 * 128 * 3)).save(source, format="PNG")
            self.assertLess(source.stat().st_size, 256 * 1024)
            paths.append(source)
        self.assertGreater(sum(path.stat().st_size for path in paths), 600_000)

        compacted = compact_model_images(paths, self.scratch, max_total_bytes=600_000)

        self.assertEqual(len(compacted), len(paths))
        self.assertTrue(any(path.suffix == ".jpg" for path in compacted))
        self.assertLessEqual(sum(path.stat().st_size for path in compacted), 600_000)


if __name__ == "__main__":
    unittest.main()
