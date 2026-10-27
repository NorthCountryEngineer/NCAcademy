"""Decode a bounded still raster and discard its original container/metadata."""
import base64
import binascii
import hashlib
from io import BytesIO
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from .curated_media import _container_boundaries

MAX_BYTES = 200000
MAX_EDGE = 1024
MAX_NORMALIZED_BYTES = 96 * 1024
FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def normalize_avatar(image):
    try:
        raw = base64.b64decode(image["data_base64"], validate=True)
        expected = FORMATS[image["mime_type"]]
        if not raw or len(raw) > MAX_BYTES:
            raise ValueError()
        _container_boundaries(raw, expected)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            def bounds(candidate):
                if candidate.format != expected or min(candidate.size) < 1 or max(candidate.size) > MAX_EDGE or getattr(candidate, "n_frames", 1) != 1 or getattr(candidate, "is_animated", False):
                    raise ValueError()
            with Image.open(BytesIO(raw), formats=(expected,)) as candidate:
                bounds(candidate)
                candidate.verify()
            with Image.open(BytesIO(raw), formats=(expected,)) as candidate:
                bounds(candidate)
                candidate.load()
                rgba = ImageOps.exif_transpose(candidate).convert("RGBA")
                rgba.thumbnail((512, 512), Image.Resampling.LANCZOS)
                clean = Image.new("RGB", rgba.size, "white")
                clean.paste(rgba, mask=rgba.getchannel("A"))
                for quality in (85, 75, 65, 55, 45, 35):
                    output = BytesIO()
                    clean.save(output, "JPEG", quality=quality, optimize=True)
                    normalized = output.getvalue()
                    if len(normalized) <= MAX_NORMALIZED_BYTES:
                        return {"mime_type": "image/jpeg", "data_base64": base64.b64encode(normalized).decode("ascii"), "sha256": hashlib.sha256(normalized).hexdigest(), "width": clean.width, "height": clean.height, "byte_length": len(normalized)}
    except (ValueError, TypeError, KeyError, EOFError, binascii.Error, OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        pass
    raise ValueError("Use one JPEG, PNG or WebP image up to 200,000 bytes and 1024 pixels per side")
