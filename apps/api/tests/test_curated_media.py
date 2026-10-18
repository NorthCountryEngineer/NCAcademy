import base64
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import zlib

from PIL import Image, PngImagePlugin
import pytest

from academy.curated_media import document_visuals, media_for_document, normalize_assignment_image, visual_sha256


def encoded_photo(image_format="PNG", image=None, **save_options):
    image = image or Image.new("RGB", (64, 48), (55, 120, 180))
    output = BytesIO()
    image.save(output, format=image_format, **save_options)
    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}[image_format]
    return "data:" + mime + ";base64," + base64.b64encode(output.getvalue()).decode()


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_accepts_bounded_raster_and_emits_only_hashed_rgb_jpeg(image_format):
    result = normalize_assignment_image(encoded_photo(image_format))
    raw = base64.b64decode(result["data_base64"], validate=True)
    assert set(result) == {"data_base64", "sha256", "width", "height", "media_type"}
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(raw) <= 96 * 1024 and result["media_type"] == "image/jpeg"
    with Image.open(BytesIO(raw)) as normalized:
        normalized.load()
        assert normalized.format == "JPEG" and normalized.mode == "RGB"
        assert normalized.size == (result["width"], result["height"]) == (64, 48)
        assert not normalized.getexif() and "icc_profile" not in normalized.info


def test_applies_exif_orientation_and_strips_metadata_before_storage():
    exif = Image.Exif()
    exif[270] = "PRIVATE_ASSIGNMENT_LOCATION"
    exif[274] = 6
    original = encoded_photo("JPEG", Image.new("RGB", (18, 30), "green"), exif=exif, icc_profile=b"PRIVATE_COLOR_PROFILE")
    result = normalize_assignment_image(original)
    raw = base64.b64decode(result["data_base64"])
    assert (result["width"], result["height"]) == (30, 18)
    assert b"PRIVATE_ASSIGNMENT_LOCATION" not in raw and b"PRIVATE_COLOR_PROFILE" not in raw
    with Image.open(BytesIO(raw)) as normalized:
        assert not normalized.getexif() and "icc_profile" not in normalized.info


def test_strips_png_text_and_composites_transparency_on_white():
    text = PngImagePlugin.PngInfo()
    text.add_text("Comment", "PRIVATE_GPS_OR_STUDENT_METADATA")
    result = normalize_assignment_image(encoded_photo("PNG", Image.new("RGBA", (30, 20), (200, 20, 10, 0)), pnginfo=text))
    raw = base64.b64decode(result["data_base64"])
    assert b"PRIVATE_GPS_OR_STUDENT_METADATA" not in raw
    with Image.open(BytesIO(raw)) as normalized:
        assert all(channel >= 250 for channel in normalized.getpixel((15, 10)))


@pytest.mark.parametrize("data_url", [None, True, "", "https://example.test/assignment.png", "data:image/svg+xml;base64,PHN2Zy8+", "data:image/gif;base64,R0lGODlh", "data:image/png;charset=utf-8;base64,AAAA", "data:image/png;base64,NOT VALID!!!", "data:image/png;base64,"])
def test_rejects_unsupported_urls_containers_and_invalid_encoding(data_url):
    with pytest.raises(ValueError):
        normalize_assignment_image(data_url)


def test_rejects_declared_mime_mismatch_and_trailing_polyglot_data():
    valid = encoded_photo()
    with pytest.raises(ValueError, match="declared image format"):
        normalize_assignment_image(valid.replace("image/png", "image/jpeg"))
    raw = base64.b64decode(valid.split(",")[1]) + b"<script>not an image</script>"
    with pytest.raises(ValueError, match="declared image format"):
        normalize_assignment_image("data:image/png;base64," + base64.b64encode(raw).decode())


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_rejects_truncated_containers(image_format):
    source = encoded_photo(image_format)
    prefix, encoded = source.split(",")
    raw = base64.b64decode(encoded)
    with pytest.raises(ValueError):
        normalize_assignment_image(prefix + "," + base64.b64encode(raw[:-12]).decode())


def test_rejects_png_corruption_even_when_file_boundary_is_intact():
    source = encoded_photo()
    raw = bytearray(base64.b64decode(source.split(",")[1]))
    raw[42] ^= 1
    with pytest.raises(ValueError):
        normalize_assignment_image("data:image/png;base64," + base64.b64encode(raw).decode())


@pytest.mark.parametrize("image_format", ["PNG", "WEBP"])
def test_rejects_animated_images(image_format):
    original = encoded_photo(image_format, Image.new("RGB", (32, 32), "red"), save_all=True,
                             append_images=[Image.new("RGB", (32, 32), "blue")], duration=100, loop=0)
    with pytest.raises(ValueError, match="one still image"):
        normalize_assignment_image(original)


def test_rejects_oversized_encoded_payload_and_small_file_with_large_dimensions():
    with pytest.raises(ValueError, match="128 KiB"):
        normalize_assignment_image("data:image/png;base64," + base64.b64encode(b"x" * (128 * 1024 + 1)).decode())
    with pytest.raises(ValueError, match="1280-pixel"):
        normalize_assignment_image(encoded_photo("PNG", Image.new("RGB", (1281, 1), "white")))


def test_rejects_decompression_bomb_header_without_loading_its_pixels():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    content = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", 50000, 50000, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + chunk(b"IEND", b"")
    with pytest.raises(ValueError, match="safely decoded"):
        normalize_assignment_image("data:image/png;base64," + base64.b64encode(content).decode())


def reviewed_documents():
    catalog = json.loads((Path(__file__).parents[1] / "academy" / "standards_data" / "ny_environmental_science_2026.json").read_text())
    sources = {item["id"]: item for item in catalog["sources"]}
    return [{"id": "reference:" + item["id"], "kind": "source_reference", "audience": "all",
             "source_id": item["source_id"], "source_url": sources[item["source_id"]]["url"],
             "source_content_sha256": sources[item["source_id"]]["content_sha256"]} for item in catalog["teaching_documents"]]


def test_each_starter_source_has_exact_trusted_interactive_visual_and_stable_hash():
    visuals = []
    for document in reviewed_documents():
        choices = document_visuals(document)
        assert len(choices) == 1
        visual = choices[0]
        assert set(visual) == {"id", "version", "title", "caption", "nodes", "edges"}
        assert 3 <= len(visual["nodes"]) <= 8
        node_ids = {node["id"] for node in visual["nodes"]}
        assert len(node_ids) == len(visual["nodes"])
        assert all(set(node) == {"id", "label", "detail"} for node in visual["nodes"])
        assert all(set(edge) == {"from", "to", "label"} and {edge["from"], edge["to"]} <= node_ids for edge in visual["edges"])
        assert media_for_document(document, visual["id"]) == visual
        assert media_for_document(document, "unknown") is None
        assert visual_sha256(visual) == visual_sha256(deepcopy(visual))
        changed = deepcopy(visual)
        changed["nodes"][0]["detail"] += " changed"
        assert visual_sha256(changed) != visual_sha256(visual)
        visuals.append(visual["id"])
    assert len(set(visuals)) == 4


@pytest.mark.parametrize("changes", [
    {"kind": "instructional_supplement"}, {"kind": "standard"}, {"audience": "teachers"}, {"audience": None},
    {"id": "reference:unreviewed-topic"}, {"source_id": "unreviewed-source"}, {"source_content_sha256": "0" * 64},
    {"source_url": "https://science.nasa.gov.evil.test/earth/earth-observatory/the-carbon-cycle/"},
    {"source_url": "https://science.nasa.gov/earth/earth-observatory/the-carbon-cycle/#different"},
])
def test_unknown_private_or_lookalike_documents_cannot_acquire_diagrams(changes):
    assert document_visuals({**reviewed_documents()[0], **changes}) == []


def test_returned_descriptor_mutation_cannot_change_later_snapshots():
    document = reviewed_documents()[0]
    expected = document_visuals(document)
    mutated = document_visuals(document)
    mutated[0]["nodes"][0]["detail"] = "Injected facts"
    assert document_visuals(document) == expected
