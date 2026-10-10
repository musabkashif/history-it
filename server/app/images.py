"""Prepare inspection images without modifying Drive files.

High-detail vision requests are scaled to fit inside 2048 x 2048 before the
model sees them. Pixels beyond that edge do not reach the inspection, and the
previous PNG copies of those pixels made a 21-image batch about 127 MB.
Files that are already within that edge and a modest byte size are sent
unchanged. Larger files keep their aspect ratio, use Lanczos resampling, and
are encoded as JPEG quality 95 with 4:4:4 chroma so text stays sharp.
"""
from __future__ import annotations

from io import BytesIO

ORIGINAL_MAX_BYTES = 4_000_000
ORIGINAL_MAX_EDGE = 2048
RESIZE_MAX_EDGE = 2048
JPEG_QUALITY = 95

_MIME_BY_FORMAT = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'WEBP': 'image/webp',
    'GIF': 'image/gif',
    'TIFF': 'image/tiff',
}


def prepare_model_image(
    data: bytes,
    mime_type: str = 'image/jpeg',
    *,
    original_max_bytes: int = ORIGINAL_MAX_BYTES,
    original_max_edge: int = ORIGINAL_MAX_EDGE,
    resize_max_edge: int = RESIZE_MAX_EDGE,
    jpeg_quality: int = JPEG_QUALITY,
):
    """Return (bytes, mime, mode) for the inspection request.

    mode is original or resized-high-quality.
    """
    if not data:
        raise ValueError('Source image is empty')
    if jpeg_quality < 90:
        raise ValueError('Inspection JPEG quality must stay high enough to preserve text')
    from PIL import Image, ImageOps

    with Image.open(BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source)
        width, height = image.size
        longest = max(width, height)
        detected = _MIME_BY_FORMAT.get(image.format or '', mime_type or 'image/jpeg')
        mime = mime_type or detected
        needs_conversion = mime in {'image/tiff', 'image/tif'} or detected == 'image/tiff'
        if not needs_conversion and len(data) <= original_max_bytes and longest <= original_max_edge:
            return data, mime, 'original'
        if longest > resize_max_edge:
            image.thumbnail((resize_max_edge, resize_max_edge), Image.Resampling.LANCZOS)
        encoded = _jpeg_bytes(image, jpeg_quality)
        return encoded, 'image/jpeg', 'resized-high-quality'


def _jpeg_bytes(image, quality: int) -> bytes:
    if image.mode not in ('RGB', 'L'):
        image = image.convert('RGB')
    out = BytesIO()
    save_kwargs = {'format': 'JPEG', 'quality': quality, 'optimize': True}
    if image.mode == 'RGB':
        save_kwargs['subsampling'] = 0
    image.save(out, **save_kwargs)
    return out.getvalue()
