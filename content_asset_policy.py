from __future__ import annotations

from dataclasses import dataclass
import re


ASSET_CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
}
_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp"})
_ASSET_NAME_RE = re.compile(
    r"(?P<digest>[0-9a-f]{64})\.(?P<extension>png|jpg|jpeg|gif|webp|pdf)"
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


def asset_magic_is_valid(extension: str, payload: bytes) -> bool:
    if extension == "png":
        return payload.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {"jpg", "jpeg"}:
        return payload.startswith(b"\xff\xd8\xff")
    if extension == "gif":
        return payload.startswith((b"GIF87a", b"GIF89a"))
    if extension == "webp":
        return len(payload) >= 12 and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP"
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
