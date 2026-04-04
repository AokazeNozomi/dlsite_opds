"""Shared test fixtures and helpers."""

import io

from PIL import Image


def make_jpeg(width: int = 800, height: int = 600) -> bytes:
    """Create a minimal JPEG image of the given dimensions."""
    im = Image.new("RGB", (width, height), color=(128, 64, 200))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


class FakePlayFile:
    """Minimal PlayFile stand-in for testing image processing and prefetch."""

    def __init__(
        self,
        type: str = "image",
        crypt: bool = False,
        width: int = 800,
        height: int = 600,
    ) -> None:
        self.type = type
        opt: dict = {
            "name": "abc1234567890abcdef.jpg",
            "width": width,
            "height": height,
            "length": 1000,
        }
        if crypt:
            opt["crypt"] = True
        self.files = {"optimized": opt}
        self.optimized_name = opt["name"]
