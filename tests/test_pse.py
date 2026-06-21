"""Tests for PSE image processing (descrambling + resize)."""

import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from dlsite_opds.services.pse import (
    CryptImageError,
    descramble_image,
    prepare_source_image,
    prepare_source_image_with_validation,
    process_page_image,
    resize_and_encode,
    should_retry,
    validate_crypt_image,
)

from .conftest import FakePlayFile, make_jpeg


class TestProcessPageImage:
    def test_passthrough_no_crypt_no_resize(self) -> None:
        src = make_jpeg(800, 600)
        pf = FakePlayFile(crypt=False, width=800, height=600)
        result = process_page_image(src, pf)  # type: ignore[arg-type]
        im = Image.open(io.BytesIO(result))
        assert im.format == "JPEG"
        assert im.width == 800
        assert im.height == 600

    def test_resize_when_width_specified(self) -> None:
        src = make_jpeg(1200, 1600)
        pf = FakePlayFile(crypt=False, width=1200, height=1600)
        result = process_page_image(src, pf, max_width=600)  # type: ignore[arg-type]
        im = Image.open(io.BytesIO(result))
        assert im.width == 600
        assert im.height == 800  # aspect ratio preserved

    def test_no_resize_when_image_smaller_than_max(self) -> None:
        src = make_jpeg(400, 300)
        pf = FakePlayFile(crypt=False, width=400, height=300)
        result = process_page_image(src, pf, max_width=800)  # type: ignore[arg-type]
        im = Image.open(io.BytesIO(result))
        assert im.width == 400

    def test_png_converted_to_jpeg(self) -> None:
        im_src = Image.new("RGBA", (200, 200), color=(0, 255, 0, 128))
        buf = io.BytesIO()
        im_src.save(buf, format="PNG")
        src = buf.getvalue()

        pf = FakePlayFile(crypt=False, width=200, height=200)
        result = process_page_image(src, pf)  # type: ignore[arg-type]
        im = Image.open(io.BytesIO(result))
        assert im.format == "JPEG"
        assert im.mode == "RGB"


class TestPrepareSourceImage:
    def test_returns_pil_image(self) -> None:
        src = make_jpeg(800, 600)
        pf = FakePlayFile(crypt=False, width=800, height=600)
        im = prepare_source_image(src, pf)  # type: ignore[arg-type]
        assert isinstance(im, Image.Image)
        assert im.width == 800
        assert im.height == 600

    def test_converts_rgba_to_rgb(self) -> None:
        im_src = Image.new("RGBA", (200, 200), color=(0, 255, 0, 128))
        buf = io.BytesIO()
        im_src.save(buf, format="PNG")
        src = buf.getvalue()

        pf = FakePlayFile(crypt=False, width=200, height=200)
        im = prepare_source_image(src, pf)  # type: ignore[arg-type]
        assert im.mode == "RGB"


class TestResizeAndEncode:
    def test_no_resize_without_max_width(self) -> None:
        im = Image.new("RGB", (800, 600))
        result = resize_and_encode(im)
        out = Image.open(io.BytesIO(result))
        assert out.format == "JPEG"
        assert out.width == 800

    def test_resize_preserves_aspect_ratio(self) -> None:
        im = Image.new("RGB", (1200, 1600))
        result = resize_and_encode(im, max_width=600)
        out = Image.open(io.BytesIO(result))
        assert out.width == 600
        assert out.height == 800

    def test_no_resize_when_smaller_than_max(self) -> None:
        im = Image.new("RGB", (400, 300))
        result = resize_and_encode(im, max_width=800)
        out = Image.open(io.BytesIO(result))
        assert out.width == 400

    def test_large_downscale_correct(self) -> None:
        im = Image.new("RGB", (4000, 6000))
        result = resize_and_encode(im, max_width=800)
        out = Image.open(io.BytesIO(result))
        assert out.width == 800
        assert out.height == 1200

    def test_moderate_downscale_correct(self) -> None:
        im = Image.new("RGB", (1000, 1500))
        result = resize_and_encode(im, max_width=600)
        out = Image.open(io.BytesIO(result))
        assert out.width == 600
        assert out.height == 900


class TestCryptValidation:
    def test_validate_accepts_matching_crypt_image(self) -> None:
        src = make_jpeg(800, 600)
        pf = FakePlayFile(crypt=True, width=800, height=600)
        validate_crypt_image(src, pf, http_status=200, content_length=1000)  # type: ignore[arg-type]

    def test_validate_rejects_http_error(self) -> None:
        src = make_jpeg(800, 600)
        pf = FakePlayFile(crypt=True, width=800, height=600)
        with pytest.raises(CryptImageError, match="HTTP 403"):
            validate_crypt_image(src, pf, http_status=403)  # type: ignore[arg-type]

    def test_validate_rejects_dimension_mismatch(self) -> None:
        src = make_jpeg(400, 300)
        pf = FakePlayFile(crypt=True, width=800, height=600)
        with pytest.raises(CryptImageError, match="dimension mismatch"):
            validate_crypt_image(src, pf)  # type: ignore[arg-type]

    def test_validate_rejects_zero_dimensions(self) -> None:
        pf = FakePlayFile(crypt=True, width=800, height=600)
        fake_im = MagicMock()
        fake_im.size = (0, 0)
        with patch("dlsite_opds.services.pse.Image.open", return_value=fake_im):
            with pytest.raises(CryptImageError, match="zero dimensions"):
                validate_crypt_image(b"dummy", pf)  # type: ignore[arg-type]

    def test_validate_rejects_short_content_length(self) -> None:
        src = make_jpeg(800, 600)
        pf = FakePlayFile(crypt=True, width=800, height=600)
        with pytest.raises(CryptImageError, match="Content-Length"):
            validate_crypt_image(src, pf, content_length=50)  # type: ignore[arg-type]

    def test_validate_skips_non_crypt(self) -> None:
        src = make_jpeg(10, 10)
        pf = FakePlayFile(crypt=False, width=800, height=600)
        validate_crypt_image(src, pf, http_status=500)  # type: ignore[arg-type]

    def test_descramble_rejects_wrong_input_dimensions(self) -> None:
        im = Image.new("RGB", (400, 300))
        pf = FakePlayFile(crypt=True, width=800, height=600)
        with pytest.raises(CryptImageError, match="do not match expected"):
            descramble_image(im, pf)  # type: ignore[arg-type]

    def test_descramble_rejects_short_optimized_name(self) -> None:
        im = Image.new("RGB", (800, 600))
        pf = FakePlayFile(crypt=True, width=800, height=600)
        pf.optimized_name = "abc"
        with pytest.raises(CryptImageError, match="too short"):
            descramble_image(im, pf)  # type: ignore[arg-type]

    def test_prepare_with_validation_runs_descramble(self) -> None:
        src = make_jpeg(800, 600)
        pf = FakePlayFile(crypt=False, width=800, height=600)
        im = prepare_source_image_with_validation(src, pf)  # type: ignore[arg-type]
        assert im.width == 800


class TestShouldRetry:
    def test_allows_retries_before_max(self) -> None:
        assert should_retry(0, 3) is True
        assert should_retry(1, 3) is True

    def test_stops_at_max_attempts(self) -> None:
        assert should_retry(2, 3) is False
