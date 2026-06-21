"""OPDS-PSE image processing: descrambling and resizing.

DLsite Play serves some images in a scrambled (tiled + shuffled) form.
This module replicates the descrambling algorithm from dlsite-async so
images can be processed entirely in memory without temp files.
"""

import io
import math
import threading
from random import Random

from cykooz.resizer import FilterType, ResizeAlg, ResizeOptions, Resizer
from PIL import Image

from dlsite_async.play.models import PlayFile

_thread_local = threading.local()

MAX_CRYPT_ATTEMPTS = 3


class CryptImageError(ValueError):
    """Crypt image validation or descramble failed."""


def is_auth_failure(status: int) -> bool:
    """Return True when the CDN token or session likely expired."""
    return status in (401, 403)


def should_retry(attempt: int, max_attempts: int = MAX_CRYPT_ATTEMPTS) -> bool:
    """Return True if another fetch attempt is allowed."""
    return attempt + 1 < max_attempts


def _get_resizer() -> Resizer:
    """Return a per-thread ``Resizer`` instance.

    ``Resizer.resize`` takes ``&mut self`` in Rust and uses internal
    scratch buffers, so a single instance must never be called from
    multiple threads concurrently.
    """
    r = getattr(_thread_local, "resizer", None)
    if r is None:
        r = Resizer()
        _thread_local.resizer = r
    return r


# ---------------------------------------------------------------------------
# Mersenne Twister tile-shuffle (replicated from dlsite-async)
# ---------------------------------------------------------------------------

class _MTRandom(Random):
    """MT19937 with the raw Knuth-style ``init_genrand`` seed step used by
    DLsite Play's image viewer."""

    _N = 624

    def seed(self, a: int = 0, **kwargs: object) -> None:  # type: ignore[override]
        mt = [a & 0xFFFFFFFF] * self._N
        for i in range(1, self._N):
            mt[i] = 1812433253 * (mt[i - 1] ^ (mt[i - 1] >> 30)) + i
            mt[i] &= 0xFFFFFFFF
        state = tuple(mt) + (self._N,)
        self.setstate((self.VERSION, state, None))


def _mt_tiles(seed: int, length: int) -> list[int]:
    rs = _MTRandom(seed)
    a = list(range(length))
    pos = 0
    for n in range(length - 1, -1, -1):
        e = math.floor(rs.random() * (n + 1))
        a[n], a[e] = a[e], a[n]
        pos += 1
        version, state, next_gauss = rs.getstate()
        state = state[:-1] + (pos,)
        rs.setstate((version, state, next_gauss))
    return a


def _crypt_seed(optimized_name: str) -> int:
    if len(optimized_name) < 12:
        raise CryptImageError(
            f"optimized_name too short for seed extraction: {optimized_name!r}"
        )
    seed_str = optimized_name[5:12]
    try:
        return int(seed_str, 16)
    except ValueError as exc:
        raise CryptImageError(
            f"Invalid hex seed in optimized_name: {seed_str!r}"
        ) from exc


def _is_crypt_playfile(playfile: PlayFile) -> bool:
    return bool(playfile.files.get("optimized", {}).get("crypt", False))


# ---------------------------------------------------------------------------
# Crypt image validation
# ---------------------------------------------------------------------------

def validate_crypt_image(
    image_bytes: bytes,
    playfile: PlayFile,
    *,
    http_status: int = 200,
    content_length: int | None = None,
) -> None:
    """Validate a downloaded crypt image before descrambling."""
    if not _is_crypt_playfile(playfile):
        return

    opt_name = playfile.optimized_name
    optimized = playfile.files["optimized"]
    width: int = optimized["width"]
    height: int = optimized["height"]

    if not (200 <= http_status < 300):
        raise CryptImageError(
            f"Crypt image download failed with HTTP {http_status} for {opt_name}"
        )

    im = Image.open(io.BytesIO(image_bytes))
    img_w, img_h = im.size
    if img_w == 0 or img_h == 0:
        raise CryptImageError(f"Crypt image has zero dimensions for {opt_name}")
    if img_w != width or img_h != height:
        raise CryptImageError(
            f"Crypt image dimension mismatch for {opt_name}: "
            f"got {img_w}x{img_h}, expected {width}x{height}"
        )

    expected_len = optimized.get("length")
    if expected_len and content_length is not None and content_length < expected_len:
        raise CryptImageError(
            f"Crypt image incomplete for {opt_name}: "
            f"Content-Length {content_length} < expected {expected_len}"
        )


# ---------------------------------------------------------------------------
# In-memory descramble
# ---------------------------------------------------------------------------

def descramble_image(im: Image.Image, playfile: PlayFile) -> Image.Image:
    """Descramble a DLsite Play encrypted image in memory."""
    tile_w = 128
    optimized = playfile.files["optimized"]
    width: int = optimized["width"]
    height: int = optimized["height"]

    if im.size != (width, height):
        raise CryptImageError(
            f"Image dimensions {im.width}x{im.height} do not match "
            f"expected {width}x{height}"
        )

    tiles_w = math.ceil(width / tile_w)
    tiles_h = math.ceil(height / tile_w)

    tiles = [
        im.crop((x * tile_w, y * tile_w, (x + 1) * tile_w, (y + 1) * tile_w))
        for y in range(tiles_h)
        for x in range(tiles_w)
    ]

    new_im = Image.new(im.mode, im.size)
    seed = _crypt_seed(playfile.optimized_name)
    tile_order = _mt_tiles(seed, len(tiles))
    shuffle = {k: v for v, k in enumerate(tile_order)}

    for i in range(len(tiles)):
        tile = tiles[shuffle[i]]
        x = i % tiles_w
        y = i // tiles_w
        new_im.paste(tile, (x * tile_w, y * tile_w))

    return new_im.crop((0, 0, width, height))


# ---------------------------------------------------------------------------
# Page-image pipeline
# ---------------------------------------------------------------------------

def prepare_source_image_with_validation(
    image_bytes: bytes,
    playfile: PlayFile,
    *,
    http_status: int = 200,
    content_length: int | None = None,
) -> Image.Image:
    """Decode and descramble a crypt page after validating download integrity."""
    validate_crypt_image(
        image_bytes,
        playfile,
        http_status=http_status,
        content_length=content_length,
    )
    return prepare_source_image(image_bytes, playfile)


def prepare_source_image(
    image_bytes: bytes,
    playfile: PlayFile,
) -> Image.Image:
    """Decode, descramble (if encrypted), and normalise to RGB/L."""
    im = Image.open(io.BytesIO(image_bytes))

    if _is_crypt_playfile(playfile):
        im = descramble_image(im, playfile)

    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")

    im.load()
    return im


_RESIZE_OPTIONS = ResizeOptions(
    resize_alg=ResizeAlg.convolution(FilterType.catmull_rom),
)


def resize_and_encode(
    im: Image.Image,
    max_width: int | None = None,
) -> bytes:
    """Optionally resize a PIL Image and encode it as JPEG bytes.

    Uses cykooz.resizer (Rust ``fast_image_resize`` with SIMD) for the
    heavy resize step instead of Pillow's pure-C implementation.
    """
    if max_width and max_width > 0 and im.width > max_width:
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        ratio = max_width / im.width
        new_height = int(im.height * ratio)
        dst = Image.new(im.mode, (max_width, new_height))
        _get_resizer().resize_pil(im, dst, _RESIZE_OPTIONS)
        im = dst

    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def process_page_image(
    image_bytes: bytes,
    playfile: PlayFile,
    max_width: int | None = None,
) -> bytes:
    """Descramble (if encrypted), optionally resize, and convert to JPEG."""
    im = prepare_source_image(image_bytes, playfile)
    return resize_and_encode(im, max_width)
