from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Callable
from urllib.parse import unquote, urlsplit

from content_codecs import codec_for_kind  # pyright: ignore[reportMissingImports]
from content_model import ContentNode, Diagnostic, SemanticDocument


class AssetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AssetPolicy:
    allow_raster: bool
    allow_pdf_attachment: bool
    max_bytes: int

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise ValueError("asset max_bytes must be positive")


@dataclass(frozen=True, slots=True)
class AssetFetchResult:
    payload: bytes
    media_type: str


_RASTER_MEDIA_TYPES = {
    ".png": {"image/png"},
    ".jpg": {"image/jpeg", "image/jpg"},
    ".gif": {"image/gif"},
    ".webp": {"image/webp"},
}
_ROUTE_PREFIXES = {
    "editorial": "/editorial-assets",
    "statement": "/statement-assets",
}


def _normalized_media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _raster_extension(payload: bytes) -> str | None:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(payload) >= 12 and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return ".webp"
    return None


def _validate_fetched_asset(
    node: ContentNode,
    fetched: AssetFetchResult,
    policy: AssetPolicy,
) -> tuple[str, bytes]:
    if not isinstance(fetched, AssetFetchResult):
        raise AssetError("invalid-asset-fetch-result")
    payload = fetched.payload
    if not isinstance(payload, bytes) or not payload:
        raise AssetError("empty-asset-payload")
    if len(payload) > policy.max_bytes:
        raise AssetError("asset-too-large")
    media_type = _normalized_media_type(fetched.media_type)

    if node.kind == "attachment":
        if not policy.allow_pdf_attachment:
            raise AssetError("pdf-attachments-disabled")
        if node.attrs.get("mediaType") != "application/pdf":
            raise AssetError("invalid-attachment-media-type")
        if media_type != "application/pdf":
            raise AssetError("invalid-pdf-media-type")
        if not payload.startswith(b"%PDF-"):
            raise AssetError("invalid-pdf-magic")
        return ".pdf", payload

    if node.kind != "image" or not policy.allow_raster:
        raise AssetError("raster-assets-disabled")
    extension = _raster_extension(payload)
    if extension is None:
        raise AssetError("invalid-raster-magic")
    if media_type not in _RASTER_MEDIA_TYPES[extension]:
        raise AssetError("invalid-raster-media-type")
    return extension, payload


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(directory, flags)
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EINVAL, errno.ENOTSUP}:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno not in {errno.EBADF, errno.EINVAL, errno.ENOTSUP}:
                raise
    finally:
        os.close(descriptor)


def _atomic_write_asset(target: Path, payload: bytes) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = target.read_bytes()
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if target.is_symlink() or not target.is_file() or existing != payload:
                raise AssetError("existing-asset-mismatch")
            return
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            _fsync_directory(target.parent)
        except BaseException:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise
    except AssetError:
        raise
    except OSError as error:
        raise AssetError("asset-write-failed") from error


def _remote_resource_url(node: ContentNode) -> str:
    attribute = "src" if node.kind == "image" else "href"
    value = node.attrs.get(attribute)
    if not isinstance(value, str) or not value:
        raise AssetError("missing-resource-url")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise AssetError("invalid-resource-url") from error
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise AssetError("invalid-resource-url")
    return value


def _unsupported_image(node: ContentNode, document: SemanticDocument, path: str) -> ContentNode:
    document.diagnostics.append(
        Diagnostic(
            "warning",
            "editorial-asset-unsupported" if document.content_kind == "editorial" else "content-asset-unsupported",
            "Replaced unsupported image source",
            path,
        )
    )
    return ContentNode(kind="missing_asset", attrs={"alt": str(node.attrs.get("alt", ""))})


def _image_is_known_unsupported(node: ContentNode) -> bool:
    value = node.attrs.get("src")
    if not isinstance(value, str):
        return True
    try:
        path = unquote(urlsplit(value).path)
    except ValueError:
        return True
    return path.lower().endswith(".svg")


def localize_content_assets(
    document: SemanticDocument,
    *,
    generation_asset_dir: str | os.PathLike[str],
    route_prefix: str,
    fetcher: Callable[[str], AssetFetchResult],
    policy: AssetPolicy,
) -> SemanticDocument:
    expected_prefix = _ROUTE_PREFIXES.get(document.content_kind)
    if route_prefix != expected_prefix:
        raise AssetError("asset-route-prefix-mismatch")
    asset_directory = Path(generation_asset_dir)
    localized = deepcopy(document)
    localized_assets = list(localized.assets)

    def transform(node: ContentNode, path: str) -> ContentNode:
        if node.kind in {"image", "attachment"}:
            if node.kind == "image" and _image_is_known_unsupported(node):
                return _unsupported_image(node, localized, path)
            source = _remote_resource_url(node)
            try:
                fetched = fetcher(source)
            except AssetError:
                raise
            except Exception as error:
                raise AssetError("asset-fetch-failed") from error
            extension, payload = _validate_fetched_asset(node, fetched, policy)
            digest = hashlib.sha256(payload).hexdigest()
            name = f"{digest}{extension}"
            target = asset_directory / name
            _atomic_write_asset(target, payload)
            route = f"{route_prefix}/{name}"
            if node.kind == "image":
                node.attrs["src"] = route
            else:
                node.attrs["href"] = route
            if route not in localized_assets:
                localized_assets.append(route)
        if node.kind == "spoiler":
            title = node.attrs.get("title")
            if isinstance(title, list):
                localized_title = []
                for index, value in enumerate(title):
                    if isinstance(value, dict):
                        title_node = ContentNode.from_dict(value)
                        localized_title.append(
                            transform(title_node, f"{path}/title/{index}").to_dict()
                        )
                    else:
                        localized_title.append(value)
                node.attrs["title"] = localized_title
        node.children = [
            transform(child, f"{path}/{index}")
            for index, child in enumerate(node.children)
        ]
        return node

    localized.root = transform(localized.root, "document")
    localized.assets = localized_assets
    codec = codec_for_kind(localized.content_kind)
    codec.validate_document(localized, ready=True)
    return localized


__all__ = [
    "AssetError",
    "AssetFetchResult",
    "AssetPolicy",
    "localize_content_assets",
]
