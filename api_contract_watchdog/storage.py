"""File writes shared by the baseline and run-state stores."""

from pathlib import Path
from tempfile import NamedTemporaryFile


def write_text_atomic(path: Path, text: str) -> None:
    """Replace a file only after its complete new contents are written.

    Raises OSError; callers convert it into their own storage error.
    """
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.stem}-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        # Replace only after the complete JSON file is closed (also on Windows).
        temporary.replace(path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Cleanup must not hide the write error or stop other checks.
                pass
