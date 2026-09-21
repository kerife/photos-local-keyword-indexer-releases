"""Bounded, metadata-free model rasters using local macOS ImageIO."""
from __future__ import annotations

import os
import stat
import threading
import zlib
from pathlib import Path


class VisionImageError(ValueError):
    """Closed diagnostic: never disclose source paths or image properties."""


_PREPARATION_LOCK = threading.Lock()
_MAX_INPUT_BYTES = 128 * 1024 * 1024
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024


def _validate_container(content: bytearray) -> None:
    """ImageIO can salvage truncated pixels; require a complete supported container."""
    size = len(content)
    view = memoryview(content)
    if content.startswith(b"\xff\xd8\xff"):
        offset, in_scan, saw_scan = 2, False, False
        while offset < size:
            if in_scan:
                offset = content.find(b"\xff", offset)
                if offset < 0:
                    break
            if content[offset] != 0xFF:
                break
            while offset < size and content[offset] == 0xFF:
                offset += 1
            if offset == size:
                break
            marker = content[offset]
            offset += 1
            if in_scan and (marker == 0 or 0xD0 <= marker <= 0xD7):
                continue
            in_scan = False
            if marker == 0xD9:
                if saw_scan and offset == size:
                    return
                break
            if marker == 1:  # standalone TEM marker
                continue
            if marker in (0, 0xD8) or 0xD0 <= marker <= 0xD7 or offset + 2 > size:
                break
            length = int.from_bytes(view[offset:offset + 2], "big")
            if length < 2 or offset + length > size:
                break
            if marker == 0xDA:
                saw_scan = in_scan = True
            offset += length
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        offset, saw_data = 8, False
        while offset + 12 <= size:
            length = int.from_bytes(view[offset:offset + 4], "big")
            end = offset + 12 + length
            if end > size:
                break
            kind = content[offset + 4:offset + 8]
            if offset == 8 and (kind != b"IHDR" or length != 13):
                break
            expected = int.from_bytes(view[end - 4:end], "big")
            if zlib.crc32(view[offset + 4:end - 4]) != expected:
                break
            if kind == b"IDAT":
                saw_data = True
            if kind == b"IEND":
                if length == 0 and saw_data and end == size:
                    return
                break
            offset = end
    elif (content.startswith(b"RIFF") and content[8:12] == b"WEBP"
          and int.from_bytes(view[4:8], "little") + 8 == size):
        offset, saw_image = 12, False
        while offset + 8 <= size:
            length = int.from_bytes(view[offset + 4:offset + 8], "little")
            kind = content[offset:offset + 4]
            offset += 8 + length + (length & 1)
            if offset > size:
                break
            saw_image = saw_image or kind in (b"VP8 ", b"VP8L", b"ANMF")
        if offset == size and saw_image:
            return
    raise VisionImageError("Image preparation failed")


def _read_input(path: Path) -> bytearray:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        initial = os.fstat(descriptor)
        if (not stat.S_ISREG(initial.st_mode) or initial.st_uid != os.getuid()
                or initial.st_nlink != 1 or not 0 < initial.st_size <= _MAX_INPUT_BYTES):
            raise VisionImageError("Image preparation failed")
        content = bytearray(initial.st_size)
        total = 0
        while total < initial.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, initial.st_size - total))
            if not chunk:
                break
            content[total:total + len(chunk)] = chunk
            total += len(chunk)
        extra = os.read(descriptor, 1)
        final = os.fstat(descriptor)
        if (total != initial.st_size or extra or final.st_size != initial.st_size
                or final.st_mtime_ns != initial.st_mtime_ns
                or final.st_ctime_ns != initial.st_ctime_ns or final.st_nlink != 1):
            raise VisionImageError("Image preparation failed")
        return content
    finally:
        os.close(descriptor)


def _normalize(path: Path) -> bytes:
    import Quartz as q  # lazy: non-image paths do not require platform bindings
    from Foundation import NSData, NSMutableData

    content = _read_input(path)
    _validate_container(content)
    data = NSData.dataWithBytes_length_(content, len(content))
    del content
    source = q.CGImageSourceCreateWithData(data, {q.kCGImageSourceShouldCache: False})
    if (source is None or q.CGImageSourceGetCount(source) != 1
            or q.CGImageSourceGetStatus(source) != q.kCGImageStatusComplete
            or q.CGImageSourceGetStatusAtIndex(source, 0) != q.kCGImageStatusComplete):
        raise VisionImageError("Image preparation failed")
    properties = q.CGImageSourceCopyPropertiesAtIndex(source, 0, None)
    if properties is None:
        raise VisionImageError("Image preparation failed")
    width = int(properties.get(q.kCGImagePropertyPixelWidth, 0))
    height = int(properties.get(q.kCGImagePropertyPixelHeight, 0))
    if (not 0 < width <= 65535 or not 0 < height <= 65535
            or width * height > 100_000_000):
        raise VisionImageError("Image preparation failed")
    for maximum, quality in ((2048, 0.85), (2048, 0.75), (1536, 0.75)):
        image = q.CGImageSourceCreateThumbnailAtIndex(source, 0, {
            q.kCGImageSourceCreateThumbnailFromImageAlways: True,
            q.kCGImageSourceCreateThumbnailWithTransform: True,
            q.kCGImageSourceThumbnailMaxPixelSize: min(maximum, max(width, height)),
            q.kCGImageSourceShouldCacheImmediately: True,
        })
        if image is None or q.CGImageSourceGetStatusAtIndex(source, 0) != q.kCGImageStatusComplete:
            raise VisionImageError("Image preparation failed")
        output_width, output_height = q.CGImageGetWidth(image), q.CGImageGetHeight(image)
        if not (0 < output_width <= maximum and 0 < output_height <= maximum):
            raise VisionImageError("Image preparation failed")
        context = q.CGBitmapContextCreate(
            None, output_width, output_height, 8, output_width * 4,
            q.CGColorSpaceCreateWithName(q.kCGColorSpaceSRGB), q.kCGImageAlphaNoneSkipLast,
        )
        if context is None:
            raise VisionImageError("Image preparation failed")
        bounds = ((0, 0), (output_width, output_height))
        q.CGContextSetRGBFillColor(context, 1, 1, 1, 1)
        q.CGContextFillRect(context, bounds)
        q.CGContextDrawImage(context, bounds, image)
        # A fresh bitmap carries no source image metadata.
        flattened = q.CGBitmapContextCreateImage(context)
        encoded = NSMutableData.data()
        destination = q.CGImageDestinationCreateWithData(encoded, "public.jpeg", 1, None)
        if flattened is None or destination is None:
            raise VisionImageError("Image preparation failed")
        q.CGImageDestinationAddImage(destination, flattened, {q.kCGImageDestinationLossyCompressionQuality: quality})
        if not q.CGImageDestinationFinalize(destination):
            raise VisionImageError("Image preparation failed")
        if len(encoded) <= _MAX_OUTPUT_BYTES:
            # NSData.__bytes__ exposes a borrowed buffer that retains storage on
            # current PyObjC. Request an owned, bounded copy instead.
            result = encoded.getBytes_length_(None, len(encoded))
            validation = q.CGImageSourceCreateWithData(result, {q.kCGImageSourceShouldCache: False})
            if (not result.startswith(b"\xff\xd8\xff") or validation is None
                    or q.CGImageSourceGetType(validation) != "public.jpeg"
                    or q.CGImageSourceGetCount(validation) != 1
                    or q.CGImageSourceGetStatus(validation) != q.kCGImageStatusComplete):
                raise VisionImageError("Image preparation failed")
            checked = q.CGImageSourceCopyPropertiesAtIndex(validation, 0, None)
            if (checked is None or checked.get(q.kCGImagePropertyPixelWidth) != output_width
                    or checked.get(q.kCGImagePropertyPixelHeight) != output_height):
                raise VisionImageError("Image preparation failed")
            return result
        del destination, encoded, flattened, context, image
    raise VisionImageError("Image preparation failed")


def prepare_vision_image(image_path: Path) -> bytes:
    """Return an oriented sRGB JPEG, without source metadata, at most 8 MiB."""
    try:
        with _PREPARATION_LOCK:
            import objc
            # Drain ImageIO/Foundation temporaries before another request can enter.
            with objc.autorelease_pool():
                return _normalize(Path(image_path))
    except Exception as error:
        raise VisionImageError("Image preparation failed") from error
