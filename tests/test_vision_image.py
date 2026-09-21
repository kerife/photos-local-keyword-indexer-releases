"""Real ImageIO contract tests; no Photos library or model is involved."""
from __future__ import annotations

import hashlib
import importlib
import os
import struct
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest


def prepare(path):
    # Keep the initial missing implementation a failing assertion, not collection error.
    from photos_indexer import adapters
    assert hasattr(adapters, "prepare_vision_image"), "shared raster preparation is missing"
    return adapters.prepare_vision_image(path)


@pytest.fixture
def quartz():
    if sys.platform != "darwin":
        pytest.skip("requires macOS ImageIO")
    return pytest.importorskip("Quartz")


def raster(q, width=80, height=40, *, orientation=1, alpha=False, metadata=False,
           color_space=None, random_pixels=False, kind="public.png", frames=1):
    from Foundation import NSMutableData
    pixels = bytearray(os.urandom(width * height * 4)) if random_pixels else None
    space = q.CGColorSpaceCreateWithName(color_space or q.kCGColorSpaceSRGB)
    context = q.CGBitmapContextCreate(
        pixels, width, height, 8, width * 4, space,
        q.kCGImageAlphaPremultipliedLast if alpha else q.kCGImageAlphaNoneSkipLast,
    )
    assert context is not None
    if not random_pixels:
        q.CGContextSetRGBFillColor(context, 1, 0, 0, 0.5 if alpha else 1)
        q.CGContextFillRect(context, ((0, 0), (width, height)))
        if not alpha:
            q.CGContextSetRGBFillColor(context, 0, 0, 1, 1)
            q.CGContextFillRect(context, ((0, 0), (width / 4, height)))
    data = NSMutableData.data()
    destination = q.CGImageDestinationCreateWithData(data, kind, frames, None)
    properties = {q.kCGImagePropertyOrientation: orientation}
    if metadata:
        properties.update({q.kCGImagePropertyGPSDictionary: {q.kCGImagePropertyGPSLatitude: 12.3,
                           q.kCGImagePropertyGPSLatitudeRef: "N", q.kCGImagePropertyGPSLongitude: 45.6,
                           q.kCGImagePropertyGPSLongitudeRef: "E"},
                           q.kCGImagePropertyExifDictionary: {q.kCGImagePropertyExifUserComment: "private comment"},
                           q.kCGImagePropertyIPTCDictionary: {q.kCGImagePropertyIPTCCaptionAbstract: "private caption"}})
    for _ in range(frames):
        q.CGImageDestinationAddImage(destination, q.CGBitmapContextCreateImage(context), properties)
    assert q.CGImageDestinationFinalize(destination)
    return data.getBytes_length_(None, len(data))


def decode(q, data):
    source = q.CGImageSourceCreateWithData(data, None)
    assert source is not None
    image = q.CGImageSourceCreateImageAtIndex(source, 0, None)
    assert image is not None
    return image, dict(q.CGImageSourceCopyPropertiesAtIndex(source, 0, None))


def pixel(q, image, x, y):
    width, height = q.CGImageGetWidth(image), q.CGImageGetHeight(image)
    context = q.CGBitmapContextCreate(None, width, height, 8, width * 4,
                                    q.CGColorSpaceCreateWithName(q.kCGColorSpaceSRGB),
                                    q.kCGImageAlphaPremultipliedLast)
    q.CGContextDrawImage(context, ((0, 0), (width, height)), image)
    data = q.CGBitmapContextGetData(context).as_buffer(width * height * 4)
    offset = (y * width + x) * 4
    return tuple(data[offset:offset + 3])


@pytest.fixture(scope="module")
def large_png(tmp_path_factory):
    if sys.platform != "darwin":
        pytest.skip("requires macOS ImageIO")
    q = pytest.importorskip("Quartz")
    encoded = raster(q, 4096, 4096, random_pixels=True)
    # Quartz creates the raster and PNG; losslessly store its scanlines uncompressed.
    chunks, compressed, offset = [], bytearray(), 8
    while offset < len(encoded):
        length = struct.unpack(">I", encoded[offset:offset + 4])[0]
        kind = encoded[offset + 4:offset + 8]
        payload = encoded[offset + 8:offset + 8 + length]
        if kind == b"IDAT":
            compressed.extend(payload)
        else:
            chunks.append((kind, payload))
        offset += length + 12
    chunks.insert(-1, (b"IDAT", zlib.compress(zlib.decompress(compressed), level=0)))
    path = tmp_path_factory.mktemp("vision") / "large.png"
    with path.open("wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        for kind, payload in chunks:
            handle.write(struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload)))
    assert path.stat().st_size > 32 * 1024 * 1024
    return path


def test_large_raster_is_normalized_without_touching_source(quartz, large_png):
    before = hashlib.sha256(large_png.read_bytes()).digest()
    output = prepare(large_png)
    assert output.startswith(b"\xff\xd8\xff")
    assert len(output) <= 8 * 1024 * 1024
    image, _ = decode(quartz, output)
    assert (quartz.CGImageGetWidth(image), quartz.CGImageGetHeight(image)) == (2048, 2048)
    assert hashlib.sha256(large_png.read_bytes()).digest() == before


@pytest.mark.parametrize("width,height,expected", [(80, 40, (80, 40)), (8192, 128, (2048, 32))])
def test_preserves_aspect_and_does_not_enlarge(quartz, tmp_path, width, height, expected):
    path = tmp_path / "input.png"
    path.write_bytes(raster(quartz, width, height))
    image, _ = decode(quartz, prepare(path))
    assert (quartz.CGImageGetWidth(image), quartz.CGImageGetHeight(image)) == expected


@pytest.mark.parametrize("orientation,blue_y", [(6, 5), (8, 75)])
def test_physically_applies_orientation(quartz, tmp_path, orientation, blue_y):
    path = tmp_path / "rotated.jpg"
    path.write_bytes(raster(quartz, orientation=orientation, kind="public.jpeg"))
    image, properties = decode(quartz, prepare(path))
    assert (quartz.CGImageGetWidth(image), quartz.CGImageGetHeight(image)) == (40, 80)
    assert pixel(quartz, image, 20, blue_y)[2] > 200
    assert pixel(quartz, image, 20, 79 - blue_y)[0] > 200
    assert properties.get(quartz.kCGImagePropertyOrientation, 1) == 1


def test_alpha_is_composited_over_white(quartz, tmp_path):
    path = tmp_path / "alpha.png"
    path.write_bytes(raster(quartz, alpha=True))
    image, _ = decode(quartz, prepare(path))
    assert pixel(quartz, image, 20, 20) == pytest.approx((255, 127, 127), abs=6)


def test_converts_to_srgb_and_discards_source_metadata(quartz, tmp_path):
    path = tmp_path / "metadata.jpg"
    original = raster(quartz, color_space=quartz.kCGColorSpaceDisplayP3, metadata=True, kind="public.jpeg")
    _, original_properties = decode(quartz, original)
    assert quartz.kCGImagePropertyGPSDictionary in original_properties
    assert quartz.kCGImagePropertyIPTCDictionary in original_properties
    xmp = b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>private xmp</x:xmpmeta>"
    path.write_bytes(original[:2] + b"\xff\xe1" + struct.pack(">H", len(xmp) + 2) + xmp + original[2:])
    output = prepare(path)
    image, properties = decode(quartz, output)
    assert quartz.CGColorSpaceCopyName(quartz.CGImageGetColorSpace(image)) == quartz.kCGColorSpaceSRGB
    for name in (quartz.kCGImagePropertyGPSDictionary, quartz.kCGImagePropertyIPTCDictionary):
        assert name not in properties
    assert b"private comment" not in output and b"private caption" not in output
    assert b"http://ns.adobe.com/xap" not in output
    assert not properties.get(quartz.kCGImagePropertyExifDictionary, {}).get(quartz.kCGImagePropertyExifUserComment)


@pytest.mark.parametrize("kind", ["public.jpeg", "public.png"])
@pytest.mark.parametrize("removed", [100, "ten_percent"])
def test_rejects_tail_truncation_even_if_imageio_decodes(quartz, tmp_path, kind, removed):
    from photos_indexer.vision_image import VisionImageError
    data = raster(quartz, 1024, 1024, random_pixels=True, kind=kind)
    remove_count = len(data) // 10 if removed == "ten_percent" else removed
    path = tmp_path / "truncated"
    path.write_bytes(data[:-remove_count])
    with pytest.raises(VisionImageError):
        prepare(path)


def test_rejects_png_chunk_corruption(quartz, tmp_path):
    from photos_indexer.vision_image import VisionImageError
    data = bytearray(raster(quartz))
    index = data.index(b"IDAT") + 8
    data[index] ^= 1
    path = tmp_path / "corrupt.png"
    path.write_bytes(data)
    with pytest.raises(VisionImageError):
        prepare(path)


@pytest.mark.parametrize("case", ["oversize", "corrupt", "truncated", "multiframe", "symlink", "hardlink", "directory", "fifo", "dimension", "pixels"])
def test_rejects_unsafe_or_invalid_input(quartz, tmp_path, case):
    path = tmp_path / "input.png"
    path.write_bytes(raster(quartz))
    if case == "oversize":
        with path.open("r+b") as handle:
            handle.truncate(128 * 1024 * 1024 + 1)
    elif case == "corrupt":
        path.write_bytes(b"not a raster")
    elif case == "truncated":
        path.write_bytes(path.read_bytes()[:50])
    elif case == "multiframe":
        path.write_bytes(raster(quartz, kind="public.tiff", frames=2))
    elif case in ("symlink", "hardlink"):
        original = tmp_path / "original.png"
        path.rename(original)
        path.symlink_to(original) if case == "symlink" else os.link(original, path)
    elif case in ("directory", "fifo"):
        path.unlink()
        path.mkdir() if case == "directory" else os.mkfifo(path)
    else:
        # Valid PNG container with hostile dimensions: reject properties before decoding.
        data = bytearray(path.read_bytes())
        width, height = (65536, 1) if case == "dimension" else (10001, 10000)
        data[16:24] = struct.pack(">II", width, height)
        data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
        path.write_bytes(data)
    from photos_indexer import adapters
    assert hasattr(adapters, "prepare_vision_image"), "shared raster preparation is missing"
    module = importlib.import_module("photos_indexer.vision_image")
    with pytest.raises(module.VisionImageError, match="^Image preparation failed$"):
        prepare(path)


def test_short_reads_are_collected_and_descriptor_closed(quartz, tmp_path, monkeypatch):
    path = tmp_path / "input.png"
    path.write_bytes(raster(quartz))
    from photos_indexer import adapters
    assert hasattr(adapters, "prepare_vision_image"), "shared raster preparation is missing"
    module = importlib.import_module("photos_indexer.vision_image")
    original_read, original_close = os.read, os.close
    closed = []
    monkeypatch.setattr(module.os, "read", lambda fd, count: original_read(fd, min(count, 7)))
    def close(fd):
        closed.append(fd)
        original_close(fd)
    monkeypatch.setattr(module.os, "close", close)
    assert prepare(path).startswith(b"\xff\xd8\xff")
    assert len(closed) == 1


def test_growth_during_read_is_rejected_and_descriptor_closed(quartz, tmp_path, monkeypatch):
    path = tmp_path / "input.png"
    path.write_bytes(raster(quartz))
    from photos_indexer import adapters
    assert hasattr(adapters, "prepare_vision_image"), "shared raster preparation is missing"
    module = importlib.import_module("photos_indexer.vision_image")
    original_read, original_close = os.read, os.close
    closed = []
    def growing_read(fd, count):
        with path.open("ab") as handle:
            handle.write(b"growth")
        return original_read(fd, count)
    def close(fd):
        closed.append(fd)
        original_close(fd)
    monkeypatch.setattr(module.os, "read", growing_read)
    monkeypatch.setattr(module.os, "close", close)
    with pytest.raises(module.VisionImageError):
        prepare(path)
    assert len(closed) == 1


def test_decoder_failure_closes_descriptor_and_releases_slot(quartz, tmp_path, monkeypatch):
    from photos_indexer import vision_image
    original_close = os.close
    closed = []
    def close(fd):
        closed.append(fd)
        original_close(fd)
    monkeypatch.setattr(vision_image.os, "close", close)
    path = tmp_path / "input.png"
    path.write_bytes(b"corrupt")
    with pytest.raises(vision_image.VisionImageError):
        prepare(path)
    path.write_bytes(raster(quartz))
    assert prepare(path).startswith(b"\xff\xd8\xff")
    assert len(closed) == 2


def test_preparation_serializes_real_conversions(quartz, tmp_path, monkeypatch):
    from photos_indexer import vision_image
    path = tmp_path / "input.png"
    path.write_bytes(raster(quartz, 512, 512))
    original_normalize = vision_image._normalize
    guard = threading.Lock()
    active, peak = 0, 0
    def normalize(path):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            return original_normalize(path)
        finally:
            with guard:
                active -= 1
    monkeypatch.setattr(vision_image, "_normalize", normalize)
    with ThreadPoolExecutor(max_workers=4) as executor:
        outputs = list(executor.map(prepare, [path] * 4))
    assert all(output.startswith(b"\xff\xd8\xff") for output in outputs)
    assert peak == 1 and active == 0


def test_output_limit_uses_three_bounded_attempts_then_fails(quartz, tmp_path, monkeypatch):
    from photos_indexer import vision_image
    path = tmp_path / "input.png"
    path.write_bytes(raster(quartz, 2200, 1100))
    monkeypatch.setattr(vision_image, "_MAX_OUTPUT_BYTES", 1)
    add_image = quartz.CGImageDestinationAddImage
    attempts = []
    def record(destination, image, properties):
        attempts.append((quartz.CGImageGetWidth(image), quartz.CGImageGetHeight(image),
                         properties[quartz.kCGImageDestinationLossyCompressionQuality]))
        return add_image(destination, image, properties)
    monkeypatch.setattr(quartz, "CGImageDestinationAddImage", record)
    with pytest.raises(vision_image.VisionImageError):
        prepare(path)
    assert attempts == [(2048, 1024, 0.85), (2048, 1024, 0.75), (1536, 768, 0.75)]


def test_analyze_sends_prepared_jpeg_to_http(quartz, large_png):
    import base64
    from photos_indexer.adapters import OllamaVisionClient
    from tests.test_adapters import FakeHttpClient, FakeHttpResponse
    client = FakeHttpClient([
        FakeHttpResponse(200, {"version": "0.12.7"}),
        FakeHttpResponse(200, {"models": [{"name": "vision:latest"}]}),
        FakeHttpResponse(200, {"capabilities": ["vision"]}),
        FakeHttpResponse(200, {"message": {"content": '{"keywords":["colores"],"caption":"","contains_people":false,"contains_text":false,"confidence":0.9}'}}),
    ])
    result = OllamaVisionClient(client_factory=lambda: client).analyze("vision:latest", large_png)
    assert result.keywords == ("colores",)
    assert client.calls[-1][1].endswith("/chat")
    output = base64.b64decode(client.calls[-1][2]["messages"][0]["images"][0])
    assert output.startswith(b"\xff\xd8\xff") and len(output) <= 8 * 1024 * 1024
    image, _ = decode(quartz, output)
    assert (quartz.CGImageGetWidth(image), quartz.CGImageGetHeight(image)) == (2048, 2048)


def test_preparation_failure_does_not_send_chat_and_is_closed(quartz, tmp_path):
    from photos_indexer.adapters import AdapterError, OllamaVisionClient
    from tests.test_adapters import FakeHttpClient, FakeHttpResponse
    client = FakeHttpClient([
        FakeHttpResponse(200, {"version": "0.12.7"}),
        FakeHttpResponse(200, {"models": [{"name": "vision:latest"}]}),
        FakeHttpResponse(200, {"capabilities": ["vision"]}),
    ])
    path = tmp_path / "private-image.png"
    path.write_bytes(b"corrupt")
    with pytest.raises(AdapterError, match="^Image preparation failed$"):
        OllamaVisionClient(client_factory=lambda: client).analyze("vision:latest", path)
    assert not any(call[1].endswith("/chat") for call in client.calls)
