import tempfile
import unittest
from pathlib import Path

from hostspark.telegram.media import detect_output_media, is_ssrf_safe_url


class MediaResolverTests(unittest.TestCase):
    def test_ssrf_checks(self) -> None:
        # Loopback / private
        self.assertFalse(is_ssrf_safe_url("http://127.0.0.1/image.png"))
        self.assertFalse(is_ssrf_safe_url("http://localhost/image.png"))
        self.assertFalse(is_ssrf_safe_url("http://10.0.0.1/image.png"))
        self.assertFalse(is_ssrf_safe_url("http://192.168.1.1/image.png"))
        self.assertFalse(is_ssrf_safe_url("http://169.254.169.254/latest/meta-data/"))
        self.assertFalse(is_ssrf_safe_url("http://[::1]/image.png"))
        self.assertFalse(is_ssrf_safe_url("ftp://example.com/image.png"))

        # Public IP
        self.assertTrue(is_ssrf_safe_url("https://8.8.8.8/test.png"))

    def test_detect_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as workdir:
            wd = Path(workdir)
            img_file = wd / "chart.png"
            img_file.write_text("fake image content")

            other_file = wd / "test.txt"
            other_file.write_text("test")

            text = f"Report generated at {img_file} and see /etc/shadow"
            paths, urls = detect_output_media(text, allowed_dirs=[wd])

            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0], img_file)
            self.assertNotIn(Path("/etc/shadow"), paths)

    def test_detect_urls_with_markdown_and_brackets(self) -> None:
        text = "Check this [https://example.com/app.png](https://example.com/app.png) or [broken](https://[invalid-ipv6/test.png)"
        paths, urls = detect_output_media(text, allowed_dirs=[])
        # Should not throw ValueError: Invalid IPv6 URL
        self.assertIsInstance(urls, list)


if __name__ == "__main__":
    unittest.main()
