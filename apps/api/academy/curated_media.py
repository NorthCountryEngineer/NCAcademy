"""Bounded student photos and reviewed, source-linked lesson diagrams.

This module never fetches media or interprets image text as instructions. Pillow
decodes a small raster and a fresh JPEG discards the original file and metadata.
The scientific diagrams are original schematic summaries of reviewed facts from:
https://science.nasa.gov/earth/earth-observatory/the-carbon-cycle/
https://www.noaa.gov/education/resource-collections/freshwater/water-cycle
https://oceanservice.noaa.gov/education/tutorial_corals/coral07_importance.html
https://www.epa.gov/recycle/recycling-basics-and-benefits
No source artwork, logos or model-produced markup is included.
"""
import base64
import binascii
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_IMAGE_INPUT_BYTES = 128 * 1024
MAX_IMAGE_OUTPUT_BYTES = 96 * 1024
MAX_IMAGE_EDGE = 1280
MAX_IMAGE_PIXELS = 1638400
IMAGE_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def _raster_bounds(image):
    width, height = image.size
    if min(width, height) < 1 or max(width, height) > MAX_IMAGE_EDGE or width * height > MAX_IMAGE_PIXELS:
        raise ValueError("Assignment photo dimensions exceed the 1280-pixel limit")
    if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
        raise ValueError("Assignment photos must contain one still image")


def _container_boundaries(content, declared_format):
    # Pillow may accept bytes after a valid image. Do not retain a polyglot file;
    # require the expected container boundaries before even opening its decoder.
    if declared_format == "JPEG":
        valid = content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9")
    elif declared_format == "PNG":
        valid = content.startswith(b"\x89PNG\r\n\x1a\n") and content.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
    else:
        valid = (len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
                 and int.from_bytes(content[4:8], "little") + 8 == len(content))
    if not valid:
        raise ValueError("Assignment photo does not match its declared image format")


def normalize_assignment_image(data_url):
    """Return metadata-free RGB JPEG bytes as base64, never the uploaded container.

    Caller supplies the independent authorization/consent, storage encryption and
    request limits. Errors intentionally contain no uploaded bytes or metadata.
    """
    if not isinstance(data_url, str) or len(data_url) > 64 + ((MAX_IMAGE_INPUT_BYTES + 2) // 3) * 4:
        raise ValueError("Assignment photo exceeds the 128 KiB upload limit")
    prefix, separator, encoded = data_url.partition(",")
    if not separator or prefix not in {f"data:{mime};base64" for mime in IMAGE_FORMATS}:
        raise ValueError("Use a JPEG, PNG or WebP assignment photo")
    mime = prefix[5:-7]
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Assignment photo contains invalid image encoding") from None
    if not content or len(content) > MAX_IMAGE_INPUT_BYTES:
        raise ValueError("Assignment photo exceeds the 128 KiB upload limit")
    expected = IMAGE_FORMATS[mime]
    _container_boundaries(content, expected)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content), formats=(expected,)) as candidate:
                if candidate.format != expected:
                    raise ValueError("Assignment photo does not match its declared image format")
                _raster_bounds(candidate)
                candidate.verify()
            # verify() does not decode every format. Reopen and fully load before
            # copying pixels, with dimensions checked before allocation/decode.
            with Image.open(BytesIO(content), formats=(expected,)) as candidate:
                _raster_bounds(candidate)
                candidate.load()
                oriented = ImageOps.exif_transpose(candidate)
                rgba = oriented.convert("RGBA")
                fresh = Image.new("RGB", rgba.size, "white")
                fresh.paste(rgba, mask=rgba.getchannel("A"))
                # A newly allocated image receives no EXIF, ICC, comments, text,
                # paths or other input metadata, including orientation/GPS.
                for quality in (85, 75, 65, 55, 45, 35):
                    output = BytesIO()
                    fresh.save(output, format="JPEG", quality=quality, optimize=True, progressive=False)
                    normalized = output.getvalue()
                    if len(normalized) <= MAX_IMAGE_OUTPUT_BYTES:
                        return {"data_base64": base64.b64encode(normalized).decode("ascii"),
                                "sha256": hashlib.sha256(normalized).hexdigest(),
                                "width": fresh.width, "height": fresh.height,
                                "media_type": "image/jpeg"}
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, EOFError):
        raise ValueError("Assignment photo is invalid or cannot be safely decoded") from None
    raise ValueError("Assignment photo cannot fit the 96 KiB normalized limit; crop it and try again")


def _node(identifier, label, detail):
    return {"id": identifier, "label": label, "detail": detail}


def _edge(source, target, label):
    return {"from": source, "to": target, "label": label}


# Exact source identity and reviewed-byte hashes prevent a lookalike URL, teacher
# supplement or unrelated subject from acquiring a misleading scientific diagram.
_REVIEWED_VISUALS = (
    {"document_id": "reference:carbon-reservoirs-and-climate", "source_id": "nasa-carbon-cycle",
     "source_url": "https://science.nasa.gov/earth/earth-observatory/the-carbon-cycle/",
     "source_content_sha256": "5da825f90f69194476004a776ae3461aaced80bb15f66067aed0d37a55a725f5",
     "visual": {"id": "carbon-reservoirs-v1", "version": "2026-10-18.1", "title": "Carbon reservoirs and transfers",
                "caption": "A simplified reservoir model. Arrows show selected transfers, not their amounts or timescales. Explore each store of carbon.",
                "nodes": [_node("air", "Atmosphere", "Atmospheric carbon dioxide participates in the fast carbon cycle and influences Earth's energy balance."),
                          _node("life", "Living organisms", "Photosynthesis takes up carbon dioxide. Carbon becomes part of living organic matter and food webs."),
                          _node("soil", "Soils", "Soils store carbon. Decomposition transfers some organic carbon back to the atmosphere."),
                          _node("ocean", "Ocean water", "Ocean water exchanges carbon dioxide with the atmosphere. Additional dissolved carbon changes ocean chemistry."),
                          _node("rock", "Rocks and fossil fuels", "Geological stores participate over long timescales. Burning fossil fuels transfers stored carbon into the active cycle.")],
                "edges": [_edge("air", "life", "Photosynthesis"), _edge("life", "air", "Respiration"),
                          _edge("life", "soil", "Organic matter"), _edge("soil", "air", "Decomposition"),
                          _edge("air", "ocean", "Dissolution"), _edge("ocean", "air", "Exchange"),
                          _edge("rock", "air", "Combustion")]}},
    {"document_id": "reference:water-pools-fluxes-and-people", "source_id": "noaa-water-cycle",
     "source_url": "https://www.noaa.gov/education/resource-collections/freshwater/water-cycle",
     "source_content_sha256": "16a42ea7274cd06aa52a09311291dfad98cbe35928779b35517c2ae6b692daa5",
     "visual": {"id": "water-pathways-v1", "version": "2026-10-18.1", "title": "Water stores and pathways",
                "caption": "Water moves through connected stores. Energy supports evaporation; gravity drives precipitation and downhill flow. These are selected pathways.",
                "nodes": [_node("surface", "Surface water", "Oceans, lakes and rivers store water. Liquid water can evaporate into the atmosphere."),
                          _node("air", "Atmosphere", "Water vapor can condense into droplets. Precipitation transfers water from the atmosphere to the surface."),
                          _node("land", "Land and soil", "Precipitation reaches land. Water may run across the surface or enter the soil."),
                          _node("ground", "Groundwater", "Infiltration and percolation move water into soil and underground stores."),
                          _node("plants", "Plants", "Plants release water to the atmosphere through transpiration.")],
                "edges": [_edge("surface", "air", "Evaporation"), _edge("air", "land", "Precipitation"),
                          _edge("land", "surface", "Runoff"), _edge("land", "ground", "Infiltration"),
                          _edge("plants", "air", "Transpiration")]}},
    {"document_id": "reference:reef-biodiversity-and-ecosystem-services", "source_id": "noaa-reef-ecosystems",
     "source_url": "https://oceanservice.noaa.gov/education/tutorial_corals/coral07_importance.html",
     "source_content_sha256": "b3fb0febe20aafa50743de4d608c7a6f8b4f0fae7aaf448bd1306df6624c9c94",
     "visual": {"id": "reef-connections-v1", "version": "2026-10-18.1", "title": "Reefs connect ecosystems and communities",
                "caption": "This concept map shows selected ecosystem services. Its arrows represent connections, not a complete food web or a prediction of their size.",
                "nodes": [_node("reef", "Reef structure", "Coral reefs provide habitat for diverse marine organisms and reduce wave energy reaching coastlines."),
                          _node("life", "Marine organisms", "Reef habitats support interacting species, food webs and fisheries."),
                          _node("coast", "Shoreline", "Reef structure can help limit erosion and storm damage by reducing wave energy."),
                          _node("people", "Coastal communities", "Reef-associated systems support food, livelihoods and recreation. Conservation considers ecological condition and community needs.")],
                "edges": [_edge("reef", "life", "Habitat"), _edge("reef", "coast", "Wave protection"),
                          _edge("life", "people", "Food and livelihoods"), _edge("coast", "people", "Protection")]}},
    {"document_id": "reference:materials-conservation-and-recycling", "source_id": "epa-recycling-resources",
     "source_url": "https://www.epa.gov/recycle/recycling-basics-and-benefits",
     "source_content_sha256": "1748ba442c859afd5b3e277859858d1cbba486d5edfbe916b758b1457806d42d",
     "visual": {"id": "recycling-system-v1", "version": "2026-10-18.1", "title": "Following the recycling loop",
                "caption": "Avoiding waste and reusing materials come before recycling. Recycling also depends on local collection rules, processing capability and markets.",
                "nodes": [_node("reduce", "Reduce and reuse", "Avoid unnecessary consumption and keep useful materials in service before they become waste."),
                          _node("collect", "Collect", "Local collection programs accept different materials. Check the program's rules."),
                          _node("process", "Sort and process", "Collected materials must be sorted and processed into inputs for manufacturing."),
                          _node("make", "Manufacture", "Recovered materials become inputs for making new products, reducing some demand for extracted resources."),
                          _node("buy", "Use recycled products", "Purchasing recycled-content products is part of the loop. Recyclable and recycled-content do not mean the same thing.")],
                "edges": [_edge("reduce", "collect", "When material becomes waste"), _edge("collect", "process", "Accepted materials"),
                          _edge("process", "make", "Recovered inputs"), _edge("make", "buy", "New products"),
                          _edge("buy", "collect", "After use, if accepted")]}}
)


def document_visuals(document):
    """Admit only an exact reviewed public source reference; return detached data."""
    if not isinstance(document, dict) or document.get("kind") != "source_reference" or document.get("audience") != "all":
        return []
    for reviewed in _REVIEWED_VISUALS:
        if all(document.get(key) == reviewed[key] for key in ("source_id", "source_url", "source_content_sha256")) and document.get("id") == reviewed["document_id"]:
            return [deepcopy(reviewed["visual"])]
    return []


def media_for_document(document, visual_id):
    """Resolve a selector ID against the admitted visuals for this exact document."""
    return next((visual for visual in document_visuals(document) if visual["id"] == visual_id), None)


def visual_sha256(visual):
    """Stable fingerprint covers every rendered fact, node, edge and version."""
    return hashlib.sha256(json.dumps(visual, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
