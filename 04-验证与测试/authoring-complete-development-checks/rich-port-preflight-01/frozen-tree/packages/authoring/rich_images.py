"""Bounded local raster normalization; no URLs, objects, DB, or renderer here."""

from __future__ import annotations

import io
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from PIL import __version__ as PILLOW_VERSION

from packages.domain.runtime import DomainError, sha

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_NORMALIZED_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 16_000_000
PROTOCOL = "authoring-raster-normalization/1"
MIMES = {"PNG": "image/png", "JPEG": "image/jpeg"}


def reject(message="仅支持有效的单帧PNG/JPEG图片", code="AUTHORING_IMAGE_INVALID"):
    raise DomainError(code, message, 422)


def normalize(source: bytes, declared_media_type: str):
    if not isinstance(source, bytes) or not 0 < len(source) <= MAX_SOURCE_BYTES:
        reject("图片原件须为1字节到8MiB", "AUTHORING_IMAGE_SIZE_LIMIT")
    if declared_media_type not in MIMES.values():
        reject()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(source)) as check:
                if MIMES.get(check.format or "") != declared_media_type:
                    reject("图片解码格式与声明类型不一致")
                width, height = check.size
                if (
                    not 1 <= width <= 10000
                    or not 1 <= height <= 10000
                    or width * height > MAX_PIXELS
                ):
                    reject(
                        "图片超过1万像素边长或1600万像素，请缩小后上传",
                        "AUTHORING_IMAGE_PIXEL_LIMIT",
                    )
                if getattr(check, "n_frames", 1) != 1 or getattr(check, "is_animated", False):
                    reject("当前只支持单帧图片，请导出静态PNG或JPEG")
                check.verify()
            with Image.open(io.BytesIO(source)) as original:
                oriented = ImageOps.exif_transpose(original)
                try:
                    mode = (
                        "RGBA"
                        if "A" in oriented.getbands() or "transparency" in oriented.info
                        else "RGB"
                    )
                    pixels = oriented.convert(mode)
                    try:
                        pixels.info.clear()
                        output = io.BytesIO()
                        pixels.save(output, format="PNG", compress_level=9, optimize=False)
                        normalized = output.getvalue()
                        size = pixels.size
                    finally:
                        pixels.close()
                finally:
                    oriented.close()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        reject()
    if len(normalized) > MAX_NORMALIZED_BYTES:
        reject("规范图片超过16MiB，请缩小图片后上传", "AUTHORING_IMAGE_SIZE_LIMIT")
    proof = {
        "protocol": PROTOCOL,
        "pillow_version": PILLOW_VERSION,
        "normalizer_digest": sha(Path(__file__).read_bytes()),
        "input_digest": sha(source),
        "input_media_type": declared_media_type,
        "output_digest": sha(normalized),
        "output_media_type": "image/png",
        "output_byte_size": len(normalized),
        "width_px": size[0],
        "height_px": size[1],
        "mode": mode,
        "orientation": "EXIF_transposed",
        "metadata": "stripped",
        "single_frame": True,
        "compression": {"level": 9, "optimize": False},
    }
    return {"bytes": normalized, "proof": proof}


def verify_normalized(data, proof):
    """Validate actual embedded PNG bytes, not just a caller-supplied media label."""
    if (
        not isinstance(proof, dict)
        or proof.get("protocol") != PROTOCOL
        or proof.get("pillow_version") != PILLOW_VERSION
        or proof.get("normalizer_digest") != sha(Path(__file__).read_bytes())
        or proof.get("output_media_type") != "image/png"
        or not isinstance(data, bytes)
        or not 0 < len(data) <= MAX_NORMALIZED_BYTES
        or proof.get("output_byte_size") != len(data)
        or proof.get("output_digest") != sha(data)
    ):
        reject("规范图片字节或归一化身份变化", "AUTHORING_IMAGE_BYTES_CHANGED")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format != "PNG"
                or image.mode not in {"RGB", "RGBA"}
                or image.size != (proof.get("width_px"), proof.get("height_px"))
                or any(image.info)
                or getattr(image, "n_frames", 1) != 1
            ):
                reject("规范图片容器与冻结证明不一致", "AUTHORING_IMAGE_BYTES_CHANGED")
            width, height = image.size
            if not 1 <= width <= 10000 or not 1 <= height <= 10000 or width * height > MAX_PIXELS:
                reject("规范图片尺寸不合法", "AUTHORING_IMAGE_BYTES_CHANGED")
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        reject("规范图片不可解码", "AUTHORING_IMAGE_BYTES_CHANGED")
    return True
