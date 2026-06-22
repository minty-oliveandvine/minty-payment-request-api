"""Best-effort file downsizing to a 1 MB target.

Used by attachment_service before persisting uploaded invoices / bank slips to
S3 so storage stays small and presigned-URL responses are cheap. Preserves the
original mime type / extension (no format conversion).

If the file cannot be brought under the target, the smallest produced variant
is returned anyway — callers should not block on size.
"""

import io
import logging

logger = logging.getLogger("minty-api")

TARGET_BYTES = 1 * 1024 * 1024  # 1 MB

# Image MIME types we attempt to recompress with Pillow.
_IMAGE_MIMES = {
    "image/jpeg",
    "image/jpg",
    "image/pjpeg",
    "image/png",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/gif",
}


def downsize_bytes(data: bytes, mime_type: str) -> bytes:
    """Return downsized bytes for the file, or original bytes if no win.

    Never raises. On any error, returns the original input unchanged.
    """
    if not data or len(data) <= TARGET_BYTES:
        return data

    try:
        if mime_type in _IMAGE_MIMES:
            return _downsize_image(data, mime_type)
        if mime_type == "application/pdf":
            return _downsize_pdf(data)
    except Exception as exc:
        logger.warning(
            "downsize_bytes: best-effort failed for %s (%d bytes): %s",
            mime_type, len(data), exc,
        )
    return data


def _save_format_for(mime_type: str) -> str:
    if mime_type == "image/png":
        return "PNG"
    if mime_type == "image/webp":
        return "WEBP"
    if mime_type == "image/gif":
        return "GIF"
    if mime_type == "image/bmp":
        return "BMP"
    if mime_type == "image/tiff":
        return "TIFF"
    return "JPEG"


def _downsize_image(data: bytes, mime_type: str) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    img.load()

    save_format = _save_format_for(mime_type)

    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )
    if save_format == "JPEG" and img.mode != "RGB":
        img = img.convert("RGB")
    elif save_format == "PNG" and not has_alpha and img.mode != "RGB":
        img = img.convert("RGB")

    best = data
    max_dim = 2400
    quality = 85
    for _ in range(7):
        w, h = img.size
        scale = min(1.0, max_dim / max(w, h))
        resized = (
            img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
            if scale < 1.0
            else img
        )

        buf = io.BytesIO()
        if save_format == "JPEG":
            resized.save(
                buf, format="JPEG", quality=quality, optimize=True, progressive=True
            )
        elif save_format == "PNG":
            resized.save(buf, format="PNG", optimize=True)
        elif save_format == "WEBP":
            resized.save(buf, format="WEBP", quality=quality, method=6)
        elif save_format == "GIF":
            resized.save(buf, format="GIF", optimize=True)
        elif save_format == "TIFF":
            resized.save(buf, format="TIFF", compression="tiff_lzw")
        else:
            resized.save(buf, format=save_format)
        out = buf.getvalue()

        if len(out) < len(best):
            best = out
        if len(out) <= TARGET_BYTES:
            break

        max_dim = max(800, int(max_dim * 0.8))
        quality = max(40, quality - 10)

    return best


def _downsize_pdf(data: bytes) -> bytes:
    try:
        import pikepdf
    except ImportError:
        logger.warning("downsize_bytes: pikepdf not installed; PDF passthrough")
        return data

    src = io.BytesIO(data)
    out = io.BytesIO()
    with pikepdf.open(src) as pdf:
        _resample_pdf_images(pdf)
        pdf.save(
            out,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            compress_streams=True,
            linearize=False,
        )
    result = out.getvalue()
    return result if len(result) < len(data) else data


def _resample_pdf_images(pdf, target_max_dim: int = 1600, jpeg_quality: int = 65) -> None:
    """In-place: downscale + JPEG-recompress embedded images larger than target.

    Skips masks/transparent images and anything PIL can't decode round-trip.
    Per-image failures are swallowed so one bad XObject doesn't break the file.
    """
    import pikepdf
    from PIL import Image

    seen = set()
    for page in pdf.pages:
        try:
            xobjects = page.Resources.get("/XObject")
        except Exception:
            continue
        if xobjects is None:
            continue
        for name in list(xobjects.keys()):
            try:
                obj = xobjects[name]
                key = (obj.objgen if hasattr(obj, "objgen") else id(obj))
                if key in seen:
                    continue
                seen.add(key)
                if obj.get("/Subtype") != pikepdf.Name("/Image"):
                    continue
                if "/SMask" in obj or "/Mask" in obj:
                    continue
                pdfimg = pikepdf.PdfImage(obj)
                pil = pdfimg.as_pil_image()
            except Exception:
                continue

            try:
                w, h = pil.size
                if max(w, h) <= 1000:
                    continue
                scale = target_max_dim / max(w, h)
                new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
                pil = pil.resize(new_size, Image.LANCZOS)
                if pil.mode != "RGB":
                    pil = pil.convert("RGB")

                buf = io.BytesIO()
                pil.save(
                    buf, format="JPEG", quality=jpeg_quality, optimize=True, progressive=True,
                )
                jpeg_bytes = buf.getvalue()

                obj.write(jpeg_bytes, filter=pikepdf.Name("/DCTDecode"))
                obj.Width = pil.width
                obj.Height = pil.height
                obj.BitsPerComponent = 8
                obj.ColorSpace = pikepdf.Name("/DeviceRGB")
                for k in ("/DecodeParms", "/Decode"):
                    if k in obj:
                        del obj[k]
            except Exception as exc:
                logger.warning("PDF image resample skipped one XObject: %s", exc)
                continue
