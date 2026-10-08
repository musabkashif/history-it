"""Prepare inspection images without modifying Drive files.

Original bytes are sent when they are within the API size and edge limits.
A required resize keeps the aspect ratio and uses a high-quality encoding.
"""
from __future__ import annotations

from io import BytesIO

ORIGINAL_MAX_BYTES = 15_000_000
ORIGINAL_MAX_EDGE = 8000
RESIZE_MAX_EDGE = 4096
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

    mode is original, converted-png-high-fidelity, or resized-high-quality.
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
        if needs_conversion and longest <= resize_max_edge:
            mode = 'converted-png-high-fidelity'
        else:
            mode = 'resized-high-quality'
        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')
        png = BytesIO()
        image.save(png, format='PNG')
        png_bytes = png.getvalue()
        if len(png_bytes) <= original_max_bytes:
            return png_bytes, 'image/png', mode
        jpeg = BytesIO()
        image.save(jpeg, format='JPEG', quality=jpeg_quality, optimize=True)
        return jpeg.getvalue(), 'image/jpeg', mode
