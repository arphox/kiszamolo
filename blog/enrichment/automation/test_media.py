"""Offline tests for image normalization and remote-source safety."""

import io
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image, features

from blog.enrichment.automation.media import (MediaError, _decode_bytes, fetch_remote_image,
                                  compact_model_images, DEFAULT_MAX_BYTES, DEFAULT_MAX_FRAMES,
                                  DEFAULT_MAX_PIXELS, DEFAULT_MAX_TOTAL_PIXELS)


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

    def decode(self, data, **limits):
        return _decode_bytes(data, self.scratch,
                             max_frames=limits.get("max_frames", DEFAULT_MAX_FRAMES),
                             max_pixels=limits.get("max_pixels", DEFAULT_MAX_PIXELS),
                             max_total_pixels=limits.get("max_total_pixels", DEFAULT_MAX_TOTAL_PIXELS),
                             max_bytes=limits.get("max_bytes", DEFAULT_MAX_BYTES))

    def test_gif_frames_preserved_and_identical_outputs_deduplicated(self):
        gif = io.BytesIO()
        frames = [Image.new("RGB", (3, 2), color) for color in ("red", "blue", "red")]
        frames[0].save(gif, format="GIF", save_all=True, append_images=frames[1:], duration=100,
                       loop=0, disposal=2)
        paths = self.decode(gif.getvalue())

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
        gif = io.BytesIO()
        frame.save(gif, format="GIF", transparency=0)

        [normalized_path] = self.decode(gif.getvalue())
        with Image.open(normalized_path) as normalized:
            self.assertEqual(normalized.mode, "RGBA")
            self.assertEqual(normalized.getpixel((0, 0))[3], 0)
            self.assertEqual(normalized.getpixel((1, 0))[3], 255)

    def test_tiff_multipage_and_bmp_normalize(self):
        first, second = Image.new("RGB", (2, 2), "red"), Image.new("RGB", (2, 2), "blue")
        tiff, bmp = io.BytesIO(), io.BytesIO()
        first.save(tiff, format="TIFF", save_all=True, append_images=[second])
        first.save(bmp, format="BMP")
        self.assertEqual(len(self.decode(tiff.getvalue())), 2)
        self.assertEqual(len(self.decode(bmp.getvalue())), 1)

    def test_static_remote_image_bytes_are_normalized_to_png(self):
        for image_format in ("JPEG", "PNG", "WEBP"):
            with self.subTest(image_format=image_format):
                payload = io.BytesIO()
                Image.new("RGB", (32, 24), "blue").save(payload, format=image_format)
                [normalized] = self.decode(payload.getvalue())
                self.assertEqual(normalized.suffix, ".png")
                with Image.open(normalized) as image:
                    self.assertEqual(image.size, (32, 24))

    @unittest.skipUnless(features.check("avif"), "Pillow AVIF support is unavailable")
    def test_avif_normalizes_when_codec_is_available(self):
        avif = io.BytesIO()
        Image.new("RGB", (2, 2), "green").save(avif, format="AVIF")
        self.assertEqual(len(self.decode(avif.getvalue())), 1)

    def test_corrupt_svg_and_limits_fail_explicitly(self):
        with self.assertRaisesRegex(MediaError, "invalid image"):
            self.decode(b"not an image")
        with self.assertRaisesRegex(MediaError, "invalid image"):
            self.decode(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        png = png_bytes(size=(5, 4))
        with self.assertRaisesRegex(MediaError, "pixel limit"):
            self.decode(png, max_pixels=19)
        with self.assertRaisesRegex(MediaError, "byte limit"):
            self.decode(png, max_bytes=10)

    def test_frame_limit_does_not_truncate_animation(self):
        gif = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(
            gif, format="GIF", save_all=True,
            append_images=[Image.new("RGB", (2, 2), "blue")], duration=100)
        with self.assertRaisesRegex(MediaError, "2 frames; limit is 1"):
            self.decode(gif.getvalue(), max_frames=1)
        with self.assertRaisesRegex(MediaError, "total limit"):
            self.decode(gif.getvalue(), max_total_pixels=7)

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
