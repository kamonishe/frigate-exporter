from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExportStorageSummary:
    file_count: int
    total_bytes: int


def summarize_export_storage(directory: Path) -> ExportStorageSummary:
    if not directory.exists():
        return ExportStorageSummary(file_count=0, total_bytes=0)

    file_count = 0
    total_bytes = 0

    for file in directory.glob("*.mp4"):
        try:
            total_bytes += file.stat().st_size
            file_count += 1
        except FileNotFoundError:
            continue

    return ExportStorageSummary(
        file_count=file_count,
        total_bytes=total_bytes,
    )


def format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"

            return f"{size:.1f} {unit}"

        size /= 1024

    return "0 B"
