import tempfile
import unittest
from pathlib import Path

from app.services.storage import format_bytes, summarize_export_storage


class StorageTests(unittest.TestCase):
    def test_missing_directory_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"

            self.assertEqual(
                summarize_export_storage(missing).file_count,
                0,
            )
            self.assertEqual(
                summarize_export_storage(missing).total_bytes,
                0,
            )

    def test_only_mp4_files_are_counted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "first.mp4").write_bytes(b"a" * 1024)
            (directory / "second.mp4").write_bytes(b"b" * 512)
            (directory / "ignored.txt").write_bytes(b"c" * 4096)

            summary = summarize_export_storage(directory)

            self.assertEqual(summary.file_count, 2)
            self.assertEqual(summary.total_bytes, 1536)

    def test_format_bytes(self) -> None:
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(1023), "1023 B")
        self.assertEqual(format_bytes(1024), "1.0 KB")
        self.assertEqual(format_bytes(1024 * 1024), "1.0 MB")


if __name__ == "__main__":
    unittest.main()
