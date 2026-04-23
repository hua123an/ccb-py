"""Image and file upload utilities.

Supports:
- Detecting image file paths in pasted/dragged text
- Reading images from file paths → base64
- Reading images from macOS clipboard
- Auto-resizing large images to stay under API limits (5 MB)
- Extracting non-image file paths for inline content inclusion
"""
from __future__ import annotations

import base64
import mimetypes
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Supported image extensions (matching original claude-code)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg"}

# API size limit: Anthropic max is ~5 MB base64 (~3.75 MB raw)
MAX_IMAGE_RAW_BYTES = 3_750_000  # 3.75 MB raw ≈ 5 MB base64

# Max dimensions for API
MAX_IMAGE_WIDTH = 7680
MAX_IMAGE_HEIGHT = 4320

# Pattern for image file extensions
IMAGE_EXTENSION_REGEX = re.compile(r"\.(png|jpe?g|gif|webp|bmp|tiff|svg)$", re.IGNORECASE)

# Pattern for common file extensions (non-image, for file upload)
TEXT_FILE_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".xml", ".html", ".css", ".scss", ".less",
    ".sh", ".bash", ".zsh", ".fish", ".rs", ".go", ".java", ".c", ".cpp", ".h",
    ".hpp", ".rb", ".php", ".swift", ".kt", ".scala", ".r", ".sql", ".csv",
    ".log", ".env", ".dockerfile", ".makefile", ".gitignore",
}


@dataclass
class ImageContent:
    """An image attachment ready to be sent with a message."""
    base64_data: str
    media_type: str  # "image/png", "image/jpeg", etc.
    filename: str = ""
    source_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "base64_data": self.base64_data,
            "media_type": self.media_type,
            "filename": self.filename,
            "source_path": self.source_path,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ImageContent:
        return cls(
            base64_data=d["base64_data"],
            media_type=d.get("media_type", "image/png"),
            filename=d.get("filename", ""),
            source_path=d.get("source_path", ""),
        )


@dataclass
class FileContent:
    """A non-image file attachment (text content inlined)."""
    filename: str
    source_path: str
    content: str
    mime_type: str = "text/plain"

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "source_path": self.source_path,
            "content": self.content,
            "mime_type": self.mime_type,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileContent:
        return cls(
            filename=d["filename"],
            source_path=d.get("source_path", ""),
            content=d.get("content", ""),
            mime_type=d.get("mime_type", "text/plain"),
        )


def _strip_quotes(text: str) -> str:
    """Remove outer quotes from a path string."""
    text = text.strip()
    if (text.startswith('"') and text.endswith('"')) or \
       (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    return text


def _strip_backslash_escapes(path: str) -> str:
    """Remove shell escape backslashes (macOS/Linux)."""
    import platform
    if platform.system() == "Windows":
        return path
    # Preserve actual double-backslash as single backslash
    placeholder = "__DBL_BSLASH__"
    result = path.replace("\\\\", placeholder)
    result = re.sub(r"\\(.)", r"\1", result)
    return result.replace(placeholder, "\\")


def is_image_path(text: str) -> bool:
    """Check if text represents an image file path."""
    cleaned = _strip_quotes(text.strip())
    cleaned = _strip_backslash_escapes(cleaned)
    return bool(IMAGE_EXTENSION_REGEX.search(cleaned))


def is_text_file_path(text: str) -> bool:
    """Check if text represents a readable text file path."""
    cleaned = _strip_quotes(text.strip())
    cleaned = _strip_backslash_escapes(cleaned)
    ext = Path(cleaned).suffix.lower()
    return ext in TEXT_FILE_EXTENSIONS


def normalize_path(text: str) -> str:
    """Clean a pasted path: strip quotes, backslash escapes, whitespace."""
    cleaned = _strip_quotes(text.strip())
    return _strip_backslash_escapes(cleaned)


def detect_media_type(path: str) -> str:
    """Detect MIME type from file extension or magic bytes."""
    ext = Path(path).suffix.lower()
    ext_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".svg": "image/svg+xml",
    }
    return ext_map.get(ext, "image/png")


def detect_media_type_from_bytes(data: bytes) -> str:
    """Detect image format from magic bytes."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    return "image/png"  # fallback


def _try_resize_image(data: bytes, media_type: str) -> tuple[bytes, str]:
    """Attempt to resize image if it exceeds API limits.

    Uses Pillow if available; falls back to returning original data.
    """
    if len(data) <= MAX_IMAGE_RAW_BYTES:
        return data, media_type

    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(data))

        # Downscale if dimensions too large
        w, h = img.size
        if w > MAX_IMAGE_WIDTH or h > MAX_IMAGE_HEIGHT:
            ratio = min(MAX_IMAGE_WIDTH / w, MAX_IMAGE_HEIGHT / h)
            new_w = int(w * ratio)
            new_h = int(h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        # Try saving as JPEG with decreasing quality until under limit
        for quality in (90, 80, 70, 50, 30):
            buf = io.BytesIO()
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=quality)
            result = buf.getvalue()
            if len(result) <= MAX_IMAGE_RAW_BYTES:
                return result, "image/jpeg"

        # Last resort: heavily downscale
        while w > 800 or h > 800:
            w //= 2
            h //= 2
        img = img.resize((max(1, w), max(1, h)), Image.LANCZOS)
        buf = io.BytesIO()
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=60)
        return buf.getvalue(), "image/jpeg"

    except ImportError:
        # Pillow not available — return original
        return data, media_type


def read_image_from_path(path: str) -> ImageContent | None:
    """Read an image file and return as ImageContent with base64 data.

    Automatically resizes if the file exceeds API limits.
    Returns None if the file can't be read or is empty.
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return None

    try:
        data = p.read_bytes()
    except (OSError, PermissionError):
        return None

    if not data:
        return None

    media_type = detect_media_type_from_bytes(data)

    # Convert BMP to PNG if possible
    if media_type == "image/bmp":
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(data))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = buf.getvalue()
            media_type = "image/png"
        except ImportError:
            pass

    # Resize if needed
    data, media_type = _try_resize_image(data, media_type)

    b64 = base64.b64encode(data).decode("ascii")
    return ImageContent(
        base64_data=b64,
        media_type=media_type,
        filename=p.name,
        source_path=str(p),
    )


def read_file_as_text(path: str, max_bytes: int = 500_000) -> FileContent | None:
    """Read a text file and return its content for inline inclusion.

    Returns None if the file can't be read or is binary.
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return None

    try:
        raw = p.read_bytes()[:max_bytes]
        # Detect binary
        if b"\x00" in raw[:8192]:
            return None
        text = raw.decode("utf-8", errors="replace")
    except (OSError, PermissionError):
        return None

    mime = mimetypes.guess_type(str(p))[0] or "text/plain"
    return FileContent(
        filename=p.name,
        source_path=str(p),
        content=text,
        mime_type=mime,
    )


def get_clipboard_image_macos() -> ImageContent | None:
    """Read image from macOS clipboard using osascript.

    Returns None if clipboard doesn't contain an image.
    """
    import tempfile
    import platform

    if platform.system() != "Darwin":
        return None

    tmp_path = os.path.join(tempfile.gettempdir(), "ccb_clipboard_image.png")

    try:
        # Check if clipboard has image data
        result = subprocess.run(
            ["osascript", "-e", "the clipboard as «class PNGf»"],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            return None

        # Save image to temp file
        save_script = (
            f'set png_data to (the clipboard as «class PNGf»)\n'
            f'set fp to open for access POSIX file "{tmp_path}" with write permission\n'
            f'write png_data to fp\n'
            f'close access fp'
        )
        result = subprocess.run(
            ["osascript", "-e", save_script],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            return None

        img = read_image_from_path(tmp_path)
        if img:
            img.filename = "clipboard_image.png"
            img.source_path = ""
        return img

    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def extract_paths_from_input(text: str) -> tuple[str, list[str], list[str]]:
    """Parse user input for file/image paths mixed with regular text.

    When a user drags files into the terminal or pastes paths, this function
    separates image paths, text file paths, and remaining text.

    Returns:
        (remaining_text, image_paths, file_paths)
    """
    # Split on spaces preceding absolute paths (handles multi-file drag)
    # Unix: space + /    Windows: space + C:\
    parts = re.split(r" (?=/|[A-Za-z]:\\)", text)
    # Also split on newlines
    lines: list[str] = []
    for part in parts:
        lines.extend(part.split("\n"))

    image_paths: list[str] = []
    file_paths: list[str] = []
    remaining: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        normalized = normalize_path(stripped)

        if is_image_path(normalized) and (
            os.path.isabs(normalized) or os.path.exists(normalized)
        ):
            image_paths.append(normalized)
        elif is_text_file_path(normalized) and os.path.isabs(normalized):
            file_paths.append(normalized)
        else:
            remaining.append(stripped)

    remaining_text = " ".join(remaining).strip()
    return remaining_text, image_paths, file_paths


def process_input_attachments(
    text: str,
) -> tuple[str, list[ImageContent], list[FileContent]]:
    """High-level function: parse input, read files, return attachments.

    Takes raw user input and returns:
      - Cleaned text (paths removed)
      - List of successfully loaded images
      - List of successfully loaded text files
    """
    remaining, image_paths, file_paths = extract_paths_from_input(text)

    images: list[ImageContent] = []
    files: list[FileContent] = []

    for ip in image_paths:
        img = read_image_from_path(ip)
        if img:
            images.append(img)

    for fp in file_paths:
        fc = read_file_as_text(fp)
        if fc:
            files.append(fc)

    return remaining, images, files
