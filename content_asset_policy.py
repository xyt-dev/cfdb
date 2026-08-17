from __future__ import annotations

from dataclasses import dataclass
import re


ASSET_CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "pdf": "application/pdf",
}
_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp"})
_ASSET_NAME_RE = re.compile(
    r"(?P<digest>[0-9a-f]{64})\.(?P<extension>png|jpg|jpeg|gif|webp|bmp|pdf)"
)
_ROUTE_PREFIXES = {
    "editorial": "/editorial-assets",
    "statement": "/statement-assets",
}


@dataclass(frozen=True, slots=True)
class AssetIdentity:
    name: str
    digest: str
    extension: str


def parse_asset_name(
    name: str,
    *,
    content_kind: str | None = None,
    resource_kind: str | None = None,
) -> AssetIdentity | None:
    if content_kind is not None and content_kind not in _ROUTE_PREFIXES:
        return None
    if resource_kind not in {None, "image", "attachment"}:
        return None
    if content_kind is None and resource_kind is not None:
        return None
    match = _ASSET_NAME_RE.fullmatch(name)
    if match is None:
        return None
    extension = match.group("extension")
    if content_kind == "editorial" and extension == "pdf":
        return None
    if resource_kind == "image" and extension not in _IMAGE_EXTENSIONS:
        return None
    if resource_kind == "attachment" and not (
        content_kind == "statement" and extension == "pdf"
    ):
        return None
    return AssetIdentity(name, match.group("digest"), extension)


def asset_identity_from_route(
    route: str,
    *,
    content_kind: str,
    resource_kind: str,
) -> AssetIdentity | None:
    prefix = _ROUTE_PREFIXES.get(content_kind)
    if prefix is None or not route.startswith(prefix + "/"):
        return None
    name = route[len(prefix) + 1:]
    identity = parse_asset_name(
        name,
        content_kind=content_kind,
        resource_kind=resource_kind,
    )
    if identity is None or route != f"{prefix}/{identity.name}":
        return None
    return identity


def _valid_bmp_payload(payload: bytes) -> bool:
    if len(payload) < 26 or not payload.startswith(b"BM"):
        return False
    declared_size = int.from_bytes(payload[2:6], "little")
    if declared_size not in {len(payload), len(payload) + 1}:
        return False
    pixel_offset = int.from_bytes(payload[10:14], "little")
    dib_size = int.from_bytes(payload[14:18], "little")
    compression = 0
    colors_used = 0
    palette_entry_bytes = 4
    if dib_size == 12:
        width = int.from_bytes(payload[18:20], "little")
        height = int.from_bytes(payload[20:22], "little")
        planes = int.from_bytes(payload[22:24], "little")
        bits_per_pixel = int.from_bytes(payload[24:26], "little")
        palette_entry_bytes = 3
    else:
        if dib_size < 40 or len(payload) < 14 + dib_size:
            return False
        width = int.from_bytes(payload[18:22], "little", signed=True)
        height = int.from_bytes(payload[22:26], "little", signed=True)
        planes = int.from_bytes(payload[26:28], "little")
        bits_per_pixel = int.from_bytes(payload[28:30], "little")
        compression = int.from_bytes(payload[30:34], "little")
        colors_used = int.from_bytes(payload[46:50], "little")
    if (
        width <= 0
        or height == 0
        or planes != 1
        or bits_per_pixel not in {1, 4, 8, 16, 24, 32}
        or compression != 0
    ):
        return False
    palette_entries = colors_used
    if bits_per_pixel <= 8:
        maximum_colors = 1 << bits_per_pixel
        if colors_used > maximum_colors:
            return False
        palette_entries = colors_used or maximum_colors
    minimum_pixel_offset = 14 + dib_size + palette_entries * palette_entry_bytes
    if pixel_offset < minimum_pixel_offset:
        return False
    row_bytes = ((width * bits_per_pixel + 31) // 32) * 4
    return pixel_offset + row_bytes * abs(height) <= len(payload)


def asset_magic_is_valid(extension: str, payload: bytes) -> bool:
    if extension == "png":
        return payload.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {"jpg", "jpeg"}:
        return payload.startswith(b"\xff\xd8\xff")
    if extension == "gif":
        return payload.startswith((b"GIF87a", b"GIF89a"))
    if extension == "webp":
        return len(payload) >= 12 and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP"
    if extension == "bmp":
        return _valid_bmp_payload(payload)
    if extension == "pdf":
        return payload.startswith(b"%PDF-")
    return False


__all__ = [
    "ASSET_CONTENT_TYPES",
    "AssetIdentity",
    "asset_identity_from_route",
    "asset_magic_is_valid",
    "parse_asset_name",
]
