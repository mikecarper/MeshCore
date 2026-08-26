#!/usr/bin/env python3
"""Build the Indicator's native-resolution text and color emoji font."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont


SOURCES = {
    "NotoColorEmoji.ttf": (
        "https://raw.githubusercontent.com/googlefonts/noto-emoji/"
        "8998f5dd683424a73e2314a8c1f1e359c19e8742/fonts/"
        "NotoColorEmoji.ttf",
        "72a635cb3d2f3524c51620cdde406b217204e8a6a06c6a096ff8ed4b5fd6e27b",
    ),
    "NotoSansMono-wdth-wght.ttf": (
        "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansmono/"
        "NotoSansMono%5Bwdth%2Cwght%5D.ttf",
        "2cb2adb378a8f574213e23df697050b83c54c27df465a2015552740b2769a081",
    ),
    "emoji-test-17.0.txt": (
        "https://www.unicode.org/Public/17.0.0/emoji/emoji-test.txt",
        "1d8a944f88d7952f7ef7c5167fef3c67995bcae24543949710231b03a201acda",
    ),
}

TEXT_CELL_WIDTH = 18
EMOJI_CELL_WIDTH = 12
CELL_HEIGHT = 24
EMOJI_CELL_HEIGHT = 12
BASELINE = 18
TEXT_NATIVE_SCALE = 3
FIRST_EMOJI_GLYPH = 0xE000
LAST_EMOJI_GLYPH = 0xF8FF
FOOTER_MAGIC = b"MCEMAP2\0"
ATLAS_MAGIC = b"CE01"
TRANSPARENT_RGB332 = 0xE3
PIXEL_OFF = 0
PIXEL_ON = 255
TEXT_THRESHOLD = 96
EMOJI_ALPHA_THRESHOLD = 48
EMOJI_REGRESSION_MIN_ON_PIXELS = {
    "\U0001f44b": 60,  # waving hand
    "\U0001f642": 70,  # slightly smiling face
    "\U0001f60a": 70,  # smiling face with smiling eyes
    "\U0001f44d": 60,  # thumbs up
}


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def fetch_sources(cache_dir: Path) -> dict[str, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, (url, expected_sha) in SOURCES.items():
        path = cache_dir / name
        if not path.exists() or digest(path) != expected_sha:
            print(f"Downloading {name}")
            with urllib.request.urlopen(url, timeout=60) as response:
                contents = response.read()
            if hashlib.sha256(contents).hexdigest() != expected_sha:
                raise RuntimeError(f"source checksum mismatch: {name}")
            path.write_bytes(contents)
        paths[name] = path
    return paths


def parse_emoji_test(path: Path) -> tuple[list[tuple[str, int, str]], dict[bytes, int]]:
    glyphs: list[tuple[str, int, str]] = []
    aliases: dict[bytes, int] = {}
    last_output = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        data, comment = line.split("#", 1)
        codepoints, status = data.split(";", 1)
        sequence = "".join(chr(int(value, 16)) for value in codepoints.split())
        status = status.strip()
        description_fields = comment.strip().split(" ", 2)
        description = description_fields[2] if len(description_fields) == 3 else sequence
        encoded = sequence.encode("utf-8")
        if status in ("fully-qualified", "component"):
            output = FIRST_EMOJI_GLYPH + len(glyphs)
            if output > LAST_EMOJI_GLYPH:
                raise RuntimeError("emoji glyphs exceed the BMP private-use range")
            glyphs.append((sequence, output, description))
            aliases[encoded] = output
            last_output = output
        elif status in ("minimally-qualified", "unqualified"):
            if last_output == 0:
                raise RuntimeError("emoji qualification alias has no canonical glyph")
            aliases[encoded] = last_output

    # Accept clients that omit the emoji presentation selector even when the
    # qualification file does not list that spelling separately.
    for sequence, output, _ in glyphs:
        without_vs16 = sequence.replace("\ufe0f", "").encode("utf-8")
        aliases.setdefault(without_vs16, output)
    return glyphs, aliases


def text_codepoints(font_path: Path) -> list[int]:
    font = TTFont(font_path, lazy=True)
    supported: set[int] = set()
    for table in font["cmap"].tables:
        if table.isUnicode():
            supported.update(table.cmap)
    font.close()

    ranges = (
        (0x21, 0x7E),       # ASCII (space is encoded as NBSP at runtime)
        (0x00A0, 0x024F),   # Latin and phonetic extensions
        (0x0370, 0x052F),   # Greek and Cyrillic
        (0x2000, 0x206F),   # punctuation and format marks
        (0x20A0, 0x20CF),   # currency
        (0x2100, 0x214F),   # letter-like symbols
        (0x2190, 0x21FF),   # arrows
        (0x2500, 0x259F),   # box drawing, blocks, and geometric fill
    )
    return [
        codepoint
        for first, last in ranges
        for codepoint in range(first, last + 1)
        if codepoint in supported and not 0xE000 <= codepoint <= 0xF8FF
    ]


def make_binary(image: Image.Image, threshold: int) -> Image.Image:
    return image.point(
        lambda value: PIXEL_ON if value >= threshold else PIXEL_OFF,
        mode="L",
    )


def render_text_glyph(font: ImageFont.FreeTypeFont, codepoint: int) -> bytes:
    image = Image.new("L", (TEXT_CELL_WIDTH, CELL_HEIGHT), 0)
    draw = ImageDraw.Draw(image)
    draw.text(
        (TEXT_CELL_WIDTH // 2, BASELINE - 1),
        chr(codepoint),
        font=font,
        fill=255,
        anchor="ms",
    )
    return make_binary(image, TEXT_THRESHOLD).tobytes()


def render_empty_emoji_fallback(sequence: str) -> Image.Image:
    image = Image.new(
        "RGBA", (EMOJI_CELL_WIDTH, EMOJI_CELL_HEIGHT), (0, 0, 0, 0)
    )
    if sequence == "\u2796":
        ImageDraw.Draw(image).line(
            (
                1,
                EMOJI_CELL_HEIGHT // 2,
                EMOJI_CELL_WIDTH - 2,
                EMOJI_CELL_HEIGHT // 2,
            ),
            fill=(50, 120, 220, 255),
            width=2,
        )
        return image
    raise RuntimeError(f"emoji rendered blank: {sequence!r}")


def render_emoji_image(font: ImageFont.FreeTypeFont, sequence: str) -> Image.Image:
    bbox = font.getbbox(sequence, anchor="lt")
    if bbox is None or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return render_empty_emoji_fallback(sequence)
    source = Image.new(
        "RGBA", (bbox[2] - bbox[0], bbox[3] - bbox[1]), (0, 0, 0, 0)
    )
    ImageDraw.Draw(source).text(
        (-bbox[0], -bbox[1]),
        sequence,
        font=font,
        embedded_color=True,
    )
    cropped_bbox = source.getchannel("A").getbbox()
    if cropped_bbox is None:
        return render_empty_emoji_fallback(sequence)
    source = source.crop(cropped_bbox)
    scale = min(
        EMOJI_CELL_WIDTH / source.width,
        EMOJI_CELL_HEIGHT / source.height,
    )
    width = max(1, round(source.width * scale))
    height = max(1, round(source.height * scale))
    source = source.resize((width, height), Image.Resampling.LANCZOS)
    image = Image.new(
        "RGBA", (EMOJI_CELL_WIDTH, EMOJI_CELL_HEIGHT), (0, 0, 0, 0)
    )
    image.paste(
        source,
        (
            (EMOJI_CELL_WIDTH - width) // 2,
            (EMOJI_CELL_HEIGHT - height) // 2,
        ),
        source,
    )
    if image.getchannel("A").getbbox() is None:
        return render_empty_emoji_fallback(sequence)
    return image


def pack_rgb332(image: Image.Image) -> bytes:
    packed = bytearray()
    for red, green, blue, alpha in image.getdata():
        if alpha < EMOJI_ALPHA_THRESHOLD:
            packed.append(TRANSPARENT_RGB332)
            continue
        value = (red & 0xE0) | ((green >> 3) & 0x1C) | (blue >> 6)
        if value == TRANSPARENT_RGB332:
            value ^= 1
        packed.append(value)
    return bytes(packed)


@dataclass
class TrieNode:
    children: dict[int, "TrieNode"] = field(default_factory=dict)
    output: int = 0
    index: int = 0


def build_map(aliases: dict[bytes, int]) -> bytes:
    root = TrieNode()
    for sequence, output in sorted(aliases.items()):
        node = root
        for value in sequence:
            node = node.children.setdefault(value, TrieNode())
        if node.output not in (0, output):
            raise RuntimeError("conflicting emoji aliases")
        node.output = output

    nodes: list[TrieNode] = [root]
    for node in nodes:
        for _, child in sorted(node.children.items()):
            if child.index == 0 and child is not root:
                child.index = len(nodes)
                nodes.append(child)
    if len(nodes) > 0xFFFF:
        raise RuntimeError("emoji trie has too many nodes")

    node_records = bytearray()
    edge_records = bytearray()
    edge_index = 0
    for node in nodes:
        children = sorted(node.children.items())
        node_records += struct.pack("<HHH", edge_index, len(children), node.output)
        for value, child in children:
            edge_records += struct.pack("<BH", value, child.index)
        edge_index += len(children)
    if edge_index > 0xFFFF:
        raise RuntimeError("emoji trie has too many edges")

    return (
        b"EM01"
        + struct.pack("<HHI", len(nodes), edge_index, 0)
        + node_records
        + edge_records
    )


def glyph_record(codepoint: int, width: int) -> bytes:
    return struct.pack(
        ">7I",
        codepoint,
        CELL_HEIGHT,
        width,
        width,
        BASELINE,
        0,
        0,
    )


def rgb332_to_rgba(value: int) -> tuple[int, int, int, int]:
    if value == TRANSPARENT_RGB332:
        return (0, 0, 0, 0)
    red = (value >> 5) & 7
    green = (value >> 2) & 7
    blue = value & 3
    return (
        (red * 255 + 3) // 7,
        (green * 255 + 3) // 7,
        (blue * 255 + 1) // 3,
        255,
    )


def make_preview(path: Path, emoji_bitmaps: list[tuple[str, bytes]]) -> None:
    scale = 6
    columns = 16
    rows = (len(emoji_bitmaps) + columns - 1) // columns
    preview = Image.new(
        "RGBA",
        (
            columns * EMOJI_CELL_WIDTH * scale,
            rows * EMOJI_CELL_HEIGHT * scale,
        ),
        (28, 28, 28, 255),
    )
    for index, (_, bitmap) in enumerate(emoji_bitmaps):
        glyph = Image.new("RGBA", (EMOJI_CELL_WIDTH, EMOJI_CELL_HEIGHT))
        glyph.putdata([rgb332_to_rgba(value) for value in bitmap])
        glyph = glyph.resize(
            (EMOJI_CELL_WIDTH * scale, EMOJI_CELL_HEIGHT * scale),
            Image.Resampling.NEAREST,
        )
        x = (index % columns) * EMOJI_CELL_WIDTH * scale
        y = (index // columns) * EMOJI_CELL_HEIGHT * scale
        preview.alpha_composite(glyph, (x, y))
    preview.save(path)


def build_font(output: Path, cache_dir: Path, preview: Path | None) -> None:
    sources = fetch_sources(cache_dir)
    emoji_glyphs, aliases = parse_emoji_test(sources["emoji-test-17.0.txt"])
    text_points = text_codepoints(sources["NotoSansMono-wdth-wght.ttf"])

    text_font = ImageFont.truetype(
        str(sources["NotoSansMono-wdth-wght.ttf"]), 20
    )
    text_font.set_variation_by_name("Bold")
    emoji_font = ImageFont.truetype(str(sources["NotoColorEmoji.ttf"]), 109)

    glyphs: list[tuple[int, int, bytes]] = [
        (
            codepoint,
            TEXT_CELL_WIDTH,
            render_text_glyph(text_font, codepoint),
        )
        for codepoint in text_points
    ]
    rendered_emoji: list[tuple[str, bytes]] = []
    color_emoji: list[bytes] = []
    for index, (sequence, _, _) in enumerate(emoji_glyphs, start=1):
        bitmap = pack_rgb332(render_emoji_image(emoji_font, sequence))
        minimum_pixels = EMOJI_REGRESSION_MIN_ON_PIXELS.get(sequence)
        if minimum_pixels is not None:
            on_pixels = sum(pixel != TRANSPARENT_RGB332 for pixel in bitmap)
            if on_pixels < minimum_pixels:
                raise RuntimeError(
                    f"emoji lost identifying pixels: {sequence!r} "
                    f"has {on_pixels}, expected at least {minimum_pixels}"
                )
        color_emoji.append(bitmap)
        if preview is not None and (index <= 128 or index % 97 == 0):
            rendered_emoji.append((sequence, bitmap))
        if index % 500 == 0:
            print(f"Rendered {index}/{len(emoji_glyphs)} emoji")

    if any(
        pixel not in (PIXEL_OFF, PIXEL_ON)
        for _, _, bitmap in glyphs
        for pixel in bitmap
    ):
        raise RuntimeError("font contains antialiased pixels")

    glyphs.sort(key=lambda item: item[0])
    header = struct.pack(">6I", len(glyphs), 11, CELL_HEIGHT, 0, BASELINE, 1)
    records = b"".join(
        glyph_record(codepoint, width) for codepoint, width, _ in glyphs
    )
    bitmaps = b"".join(bitmap for _, _, bitmap in glyphs)
    mapping = build_map(aliases)
    map_offset = len(header) + len(records) + len(bitmaps)
    atlas_header = ATLAS_MAGIC + struct.pack(
        "<HBBBBH",
        len(color_emoji),
        EMOJI_CELL_WIDTH,
        EMOJI_CELL_HEIGHT,
        TRANSPARENT_RGB332,
        TEXT_NATIVE_SCALE,
        EMOJI_CELL_WIDTH * EMOJI_CELL_HEIGHT,
    )
    atlas = atlas_header + b"".join(color_emoji)
    atlas_offset = map_offset + len(mapping)
    footer = FOOTER_MAGIC + struct.pack(
        "<IIIIIIII",
        map_offset,
        len(mapping),
        zlib.crc32(mapping),
        atlas_offset,
        len(atlas),
        zlib.crc32(atlas),
        0,
        0,
    )
    contents = header + records + bitmaps + mapping + atlas + footer

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(contents)
    manifest = {
        "format": "MeshCore SD UI font 2",
        "text_cell": [TEXT_CELL_WIDTH, CELL_HEIGHT],
        "emoji_cell": [EMOJI_CELL_WIDTH, EMOJI_CELL_HEIGHT],
        "pixel_font": True,
        "antialiasing": False,
        "text_pixel_values": [PIXEL_OFF, PIXEL_ON],
        "emoji_encoding": {
            "color": "RGB332",
            "transparent_key": TRANSPARENT_RGB332,
            "alpha_threshold": EMOJI_ALPHA_THRESHOLD,
        },
        "text_source": "Noto Sans Mono Bold, binary-thresholded",
        "text_native_scale": TEXT_NATIVE_SCALE,
        "unicode_emoji_version": "17.0",
        "text_glyphs": len(text_points),
        "emoji_glyphs": len(emoji_glyphs),
        "emoji_aliases": len(aliases),
        "trie_bytes": len(mapping),
        "color_atlas_bytes": len(atlas),
        "font_bytes": len(contents),
        "sha256": hashlib.sha256(contents).hexdigest(),
        "sources": {name: expected for name, (_, expected) in SOURCES.items()},
    }
    output.with_suffix(output.suffix + ".json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    if preview is not None:
        make_preview(preview, rendered_emoji)
    print(json.dumps(manifest, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".cache" / "meshcore-indicator-font",
    )
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    build_font(args.output, args.cache_dir, args.preview)


if __name__ == "__main__":
    main()
