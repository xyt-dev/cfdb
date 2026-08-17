from __future__ import annotations

from copy import deepcopy
from collections.abc import Collection
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import tempfile
import zlib
from typing import Callable
from urllib.parse import unquote, urlsplit

from content_codecs import codec_for_kind  # pyright: ignore[reportMissingImports]
from content_model import ContentNode, Diagnostic, SemanticDocument
from content_asset_policy import asset_magic_is_valid  # pyright: ignore[reportMissingImports]


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
    ".bmp": {"image/bmp", "image/x-bmp", "image/x-ms-bmp"},
}
_ALL_RASTER_MEDIA_TYPES = frozenset(
    media_type for media_types in _RASTER_MEDIA_TYPES.values() for media_type in media_types
)
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
    if payload.startswith(b"BM") and asset_magic_is_valid("bmp", payload):
        return ".bmp"
    return None


_MAX_FLATTENED_PNG_BYTES = 128 * 1024 * 1024


def _png_chunk(chunk_type: bytes, body: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + body) & 0xFFFFFFFF
    return (
        len(body).to_bytes(4, "big")
        + chunk_type
        + body
        + checksum.to_bytes(4, "big")
    )


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _decode_png_rows(
    raw: bytes,
    *,
    height: int,
    stride: int,
    bytes_per_pixel: int,
) -> list[bytearray] | None:
    if len(raw) != height * (stride + 1):
        return None
    rows: list[bytearray] = []
    previous = bytearray(stride)
    position = 0
    for _row_index in range(height):
        filter_type = raw[position]
        row = bytearray(raw[position + 1:position + 1 + stride])
        position += stride + 1
        if filter_type == 1:
            for index in range(bytes_per_pixel, stride):
                row[index] = (row[index] + row[index - bytes_per_pixel]) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                row[index] = (row[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                row[index] = (row[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                row[index] = (
                    row[index]
                    + _paeth_predictor(left, previous[index], upper_left)
                ) & 0xFF
        elif filter_type != 0:
            return None
        rows.append(row)
        previous = row
    return rows


def _packed_png_samples(row: bytearray, bit_depth: int, width: int) -> list[int]:
    if bit_depth == 8:
        return list(row[:width])
    samples: list[int] = []
    samples_per_byte = 8 // bit_depth
    mask = (1 << bit_depth) - 1
    for byte in row:
        for shift in range(samples_per_byte - 1, -1, -1):
            samples.append((byte >> (shift * bit_depth)) & mask)
            if len(samples) == width:
                return samples
    return samples


def _valid_png_semantics(
    *,
    color_type: int,
    bit_depth: int,
    palette: bytes | None,
    transparency: bytes | None,
) -> bool:
    palette_entries: int | None = None
    if palette is not None:
        if not palette or len(palette) % 3 or len(palette) > 768:
            return False
        palette_entries = len(palette) // 3
        if color_type in {0, 4}:
            return False
        if color_type == 3 and palette_entries > 1 << bit_depth:
            return False
    if color_type == 3 and palette_entries is None:
        return False
    if transparency is None:
        return True
    max_sample = (1 << bit_depth) - 1
    if color_type == 0:
        return (
            len(transparency) == 2
            and int.from_bytes(transparency, "big") <= max_sample
        )
    if color_type == 2:
        return len(transparency) == 6 and all(
            int.from_bytes(transparency[index:index + 2], "big") <= max_sample
            for index in range(0, 6, 2)
        )
    if color_type == 3:
        return palette_entries is not None and len(transparency) <= palette_entries
    return False


def _composite_over_white(
    output: bytearray,
    offset: int,
    red: int,
    green: int,
    blue: int,
    alpha: int,
    *,
    sample_max: int = 255,
) -> None:
    def scale(sample: int) -> int:
        return (sample * 255 + sample_max // 2) // sample_max

    if alpha >= sample_max:
        output[offset:offset + 3] = bytes(
            (scale(red), scale(green), scale(blue))
        )
        return
    if alpha <= 0:
        output[offset:offset + 3] = b"\xff\xff\xff"
        return
    inverse_alpha = sample_max - alpha
    components = (red, green, blue)
    for component_index, component in enumerate(components):
        composited = (
            component * alpha
            + sample_max * inverse_alpha
            + sample_max // 2
        ) // sample_max
        output[offset + component_index] = scale(composited)


def _flatten_transparent_png(payload: bytes) -> bytes:
    """Composite supported transparent PNG pixels over white using only stdlib."""
    try:
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            return payload
        position = 8
        idat_parts: list[bytes] = []
        palette: bytes | None = None
        transparency: bytes | None = None
        width: int | None = None
        height: int | None = None
        bit_depth: int | None = None
        color_type: int | None = None
        seen_header = False
        seen_palette = False
        seen_transparency = False
        seen_idat = False
        idat_closed = False
        seen_end = False
        known_critical_chunks = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
        while position < len(payload):
            if position + 12 > len(payload):
                return payload
            size = int.from_bytes(payload[position:position + 4], "big")
            chunk_end = position + size + 12
            if chunk_end > len(payload):
                return payload
            chunk_type = payload[position + 4:position + 8]
            if len(chunk_type) != 4 or not all(
                65 <= value <= 90 or 97 <= value <= 122
                for value in chunk_type
            ):
                return payload
            body = payload[position + 8:position + 8 + size]
            stored_checksum = int.from_bytes(
                payload[position + 8 + size:chunk_end],
                "big",
            )
            actual_checksum = zlib.crc32(chunk_type + body) & 0xFFFFFFFF
            if stored_checksum != actual_checksum:
                return payload
            if not seen_header and chunk_type != b"IHDR":
                return payload
            if chunk_type == b"IDAT":
                if idat_closed:
                    return payload
            elif seen_idat:
                idat_closed = True
            if (
                chunk_type not in known_critical_chunks
                and chunk_type[0] & 0x20 == 0
            ):
                return payload

            if chunk_type == b"IHDR":
                if seen_header or position != 8 or len(body) != 13:
                    return payload
                seen_header = True
                width = int.from_bytes(body[0:4], "big")
                height = int.from_bytes(body[4:8], "big")
                bit_depth = body[8]
                color_type = body[9]
                if body[10:13] != b"\x00\x00\x00":
                    return payload
            elif chunk_type == b"PLTE":
                if seen_palette or seen_idat:
                    return payload
                seen_palette = True
                palette = body
            elif chunk_type == b"tRNS":
                if seen_transparency or seen_idat:
                    return payload
                if color_type == 3 and not seen_palette:
                    return payload
                seen_transparency = True
                transparency = body
            elif chunk_type == b"IDAT":
                seen_idat = True
                idat_parts.append(body)
            elif chunk_type == b"IEND":
                if size != 0 or not seen_idat or chunk_end != len(payload):
                    return payload
                seen_end = True
                position = chunk_end
                break
            position = chunk_end
        if not seen_end:
            return payload

        if (
            width is None
            or height is None
            or bit_depth is None
            or color_type is None
            or width <= 0
            or height <= 0
            or not idat_parts
        ):
            return payload
        if color_type not in {0, 2, 3, 4, 6}:
            return payload
        if color_type == 3:
            if bit_depth not in {1, 2, 4, 8}:
                return payload
        elif color_type == 0:
            if bit_depth not in {1, 2, 4, 8, 16}:
                return payload
        elif bit_depth not in {8, 16}:
            return payload
        if not _valid_png_semantics(
            color_type=color_type,
            bit_depth=bit_depth,
            palette=palette,
            transparency=transparency,
        ):
            return payload
        if color_type in {0, 2, 3} and transparency is None:
            return payload

        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
        bytes_per_sample = 2 if bit_depth == 16 else 1
        if color_type in {0, 3} and bit_depth < 8:
            stride = (width * bit_depth + 7) // 8
            bytes_per_pixel = 1
        else:
            stride = width * channels * bytes_per_sample
            bytes_per_pixel = channels * bytes_per_sample
        raw_size = height * (stride + 1)
        output_size = width * height * 3
        if raw_size > _MAX_FLATTENED_PNG_BYTES or output_size > _MAX_FLATTENED_PNG_BYTES:
            return payload

        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(b"".join(idat_parts), raw_size + 1)
        if (
            len(raw) != raw_size
            or not decompressor.eof
            or decompressor.unconsumed_tail
            or decompressor.unused_data
        ):
            return payload
        rows = _decode_png_rows(
            raw,
            height=height,
            stride=stride,
            bytes_per_pixel=bytes_per_pixel,
        )
        if rows is None:
            return payload

        output = bytearray(output_size)
        output_offset = 0
        saw_transparency = False
        if color_type == 6:
            pixel_size = 4 * bytes_per_sample
            for row in rows:
                for pixel_offset in range(0, stride, pixel_size):
                    if bytes_per_sample == 2:
                        red, green, blue, alpha = (
                            int.from_bytes(
                                row[pixel_offset + index:pixel_offset + index + 2],
                                "big",
                            )
                            for index in (0, 2, 4, 6)
                        )
                        sample_max = 65535
                    else:
                        red, green, blue, alpha = row[pixel_offset:pixel_offset + 4]
                        sample_max = 255
                    saw_transparency = saw_transparency or alpha < sample_max
                    _composite_over_white(
                        output,
                        output_offset,
                        red,
                        green,
                        blue,
                        alpha,
                        sample_max=sample_max,
                    )
                    output_offset += 3
        elif color_type == 4:
            pixel_size = 2 * bytes_per_sample
            for row in rows:
                for pixel_offset in range(0, stride, pixel_size):
                    if bytes_per_sample == 2:
                        gray, alpha = (
                            int.from_bytes(
                                row[pixel_offset + index:pixel_offset + index + 2],
                                "big",
                            )
                            for index in (0, 2)
                        )
                        sample_max = 65535
                    else:
                        gray, alpha = row[pixel_offset], row[pixel_offset + 1]
                        sample_max = 255
                    saw_transparency = saw_transparency or alpha < sample_max
                    _composite_over_white(
                        output,
                        output_offset,
                        gray,
                        gray,
                        gray,
                        alpha,
                        sample_max=sample_max,
                    )
                    output_offset += 3
        elif color_type == 2:
            if transparency is None:
                return payload
            transparent_color = tuple(
                int.from_bytes(transparency[index:index + 2], "big")
                for index in range(0, 6, 2)
            )
            pixel_size = 3 * bytes_per_sample
            for row in rows:
                for pixel_offset in range(0, stride, pixel_size):
                    if bytes_per_sample == 2:
                        samples = tuple(
                            int.from_bytes(
                                row[pixel_offset + index:pixel_offset + index + 2],
                                "big",
                            )
                            for index in (0, 2, 4)
                        )
                        sample_max = 65535
                    else:
                        samples = tuple(row[pixel_offset:pixel_offset + 3])
                        sample_max = 255
                    red, green, blue = samples
                    alpha = 0 if samples == transparent_color else sample_max
                    saw_transparency = saw_transparency or alpha == 0
                    _composite_over_white(
                        output,
                        output_offset,
                        red,
                        green,
                        blue,
                        alpha,
                        sample_max=sample_max,
                    )
                    output_offset += 3
        elif color_type == 0:
            if transparency is None:
                return payload
            transparent_gray = int.from_bytes(transparency, "big")
            for row in rows:
                if bit_depth < 8:
                    max_sample = (1 << bit_depth) - 1
                    gray_samples = _packed_png_samples(row, bit_depth, width)
                    if len(gray_samples) != width:
                        return payload
                    for gray_sample in gray_samples:
                        gray = gray_sample * 255 // max_sample
                        alpha = 0 if gray_sample == transparent_gray else 255
                        saw_transparency = saw_transparency or alpha == 0
                        _composite_over_white(
                            output,
                            output_offset,
                            gray,
                            gray,
                            gray,
                            alpha,
                        )
                        output_offset += 3
                else:
                    for pixel_offset in range(0, stride, bytes_per_sample):
                        if bytes_per_sample == 2:
                            gray_sample = int.from_bytes(
                                row[pixel_offset:pixel_offset + 2],
                                "big",
                            )
                            sample_max = 65535
                        else:
                            gray_sample = row[pixel_offset]
                            sample_max = 255
                        alpha = 0 if gray_sample == transparent_gray else sample_max
                        saw_transparency = saw_transparency or alpha == 0
                        _composite_over_white(
                            output,
                            output_offset,
                            gray_sample,
                            gray_sample,
                            gray_sample,
                            alpha,
                            sample_max=sample_max,
                        )
                        output_offset += 3
        else:
            if palette is None or len(palette) % 3:
                return payload
            palette_colors = [
                tuple(palette[index:index + 3])
                for index in range(0, len(palette), 3)
            ]
            alphas = [255] * len(palette_colors)
            if transparency is not None:
                for index, alpha in enumerate(transparency[:len(alphas)]):
                    alphas[index] = alpha
            for row in rows:
                palette_indices = _packed_png_samples(row, bit_depth, width)
                if len(palette_indices) != width:
                    return payload
                for palette_index in palette_indices:
                    if palette_index >= len(palette_colors):
                        return payload
                    red, green, blue = palette_colors[palette_index]
                    alpha = alphas[palette_index]
                    saw_transparency = saw_transparency or alpha < 255
                    _composite_over_white(
                        output,
                        output_offset,
                        red,
                        green,
                        blue,
                        alpha,
                    )
                    output_offset += 3

        if not saw_transparency or output_offset != output_size:
            return payload
        scanlines = bytearray()
        for row_index in range(height):
            scanlines.append(0)
            row_start = row_index * width * 3
            scanlines.extend(output[row_start:row_start + width * 3])
        header = (
            width.to_bytes(4, "big")
            + height.to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00"
        )
        return (
            b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", header)
            + _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), 6))
            + _png_chunk(b"IEND", b"")
        )
    except (IndexError, MemoryError, OverflowError, ValueError, zlib.error):
        return payload


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
    if media_type not in _ALL_RASTER_MEDIA_TYPES:
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


def _image_is_known_unsupported(
    node: ContentNode,
    known_missing_image_sources: Collection[str],
) -> bool:
    value = node.attrs.get("src")
    if not isinstance(value, str):
        return True
    if value in known_missing_image_sources:
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
    known_missing_image_sources: Collection[str] = (),
) -> SemanticDocument:
    expected_prefix = _ROUTE_PREFIXES.get(document.content_kind)
    if route_prefix != expected_prefix:
        raise AssetError("asset-route-prefix-mismatch")
    asset_directory = Path(generation_asset_dir)
    localized = deepcopy(document)
    localized_assets = list(localized.assets)

    def transform(node: ContentNode, path: str) -> ContentNode:
        if node.kind in {"image", "attachment"}:
            if node.kind == "image" and _image_is_known_unsupported(
                node,
                known_missing_image_sources,
            ):
                return _unsupported_image(node, localized, path)
            source = _remote_resource_url(node)
            try:
                fetched = fetcher(source)
            except AssetError:
                raise
            except Exception as error:
                raise AssetError("asset-fetch-failed") from error
            extension, payload = _validate_fetched_asset(node, fetched, policy)
            if extension == ".png":
                payload = _flatten_transparent_png(payload)
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
