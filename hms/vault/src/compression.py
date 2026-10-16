"""Inspect compression of existing file and recommend per-file compression method for writing to archive."""

import struct
import zipfile
from pathlib import Path

from tifftools import read_tiff
from tifftools.constants import Compression, Tag
from tifftools.exceptions import TifftoolsError
from vault_logging import setup_json_logging


def recommended_compression_method(path: Path, logger) -> int:
    """Return a recommended zipfile.* compression method to apply for the provided file.

    Files that are already known to be compressed are recommended to be ZIP_STORED (not compressed further) when added to an archive.
    Other files are recommended to be ZIP_DEFLATED.
    """
    try:
        compressed = _file_is_known_compressed(path)
    except (
        ValueError,
        OSError,
        struct.error,
        zipfile.BadZipFile,
        TifftoolsError,
    ) as e:
        logger.error(
            f"Failed to determine compression for {path}: {e}. Proceeding as if not compressed (recommending to apply ZIP_DEFLATED)"
        )
        compressed = False
        return zipfile.ZIP_DEFLATED
    return zipfile.ZIP_STORED if compressed else zipfile.ZIP_DEFLATED


def _file_is_known_compressed(path: Path) -> bool:
    """Return True if the file is known to be already compressed, else False.

    Only certain extensions are inspected, and other extensions are assumed False.
    """
    if path.suffix.lower() in (".tif", ".tiff"):
        return _tif_is_compressed(path)
    elif path.suffix.lower() == ".zip":
        return _zip_is_compressed(path)
    return False


def _tif_is_compressed(path: Path) -> bool:
    """Return True if the TIFF is compressed, else False. Use pure-Python tifftools instead of heavier GDAL-based packages.

    See tifftools ``_tiff_dump_ifds`` for IFD/tag traversal:
    https://github.com/DigitalSlideArchive/tifftools/blob/master/tifftools/commands.py
    """
    compressed = False
    logger = setup_json_logging()
    logger.debug(f"Inspecting compression of TIFF: {path}")

    if not path.name.lower().endswith((".tif", ".tiff")):
        raise ValueError(f"Unexpected extension: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")

    try:
        pending = list(read_tiff(path)["ifds"])
        found_image = False
        while pending:
            # Discover IFDs.
            tags = pending.pop()["tags"]
            for tag in tags.values():
                for children in tag.get("ifds", []):
                    pending.extend(children)
            # Skip IFDs that don't have an image dimension tag (not an image).
            image_tags = {int(Tag.ImageWidth), int(Tag.ImageLength)}
            if not image_tags.intersection(tags):
                continue
            # Get the codec, dropping back to 1 (uncompressed) if missing.
            codec = tags.get(int(Tag.Compression), {"data": [1]}).get("data", [])
            if len(codec) != 1 or codec[0] not in Compression:
                logger.error(f"Invalid compression tag: {codec}")
                break
            found_image = True
            # Used below to distinguish image IFDs from metadata-only TIFFs.
            if codec[0] == 1:
                logger.debug(
                    f"TIFF *is not* compressed (missing compression tag and/or contains uncompressed imagery): {path}"
                )
                break
        else:
            if found_image:
                compressed = True
                logger.debug(f"TIFF *is* compressed (imagery): {path}")
            else:
                logger.error(f"No valid TIFF image IFDs found: {path}")
    except (OSError, struct.error, TifftoolsError) as e:
        logger.error(f"Failed to inspect {path}: {e}")

    return compressed


def _zip_is_compressed(path: Path) -> bool:
    """Return True if the zip file members are stored as one of (ZIP_DEFLATED, ZIP_BZIP2, ZIP_LZMA), else return False."""
    compressed = False
    logger = setup_json_logging()
    logger.debug(f"Inspecting compression of zip: {path}")

    with zipfile.ZipFile(path) as archive:
        # Check if every non-empty member is compressed.
        members = [
            entry
            for entry in archive.infolist()
            if not entry.is_dir() and entry.file_size
        ]
        compressed = not members or all(
            entry.compress_type
            in (zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA)
            for entry in members
        )

    midfix = "*is*" if compressed else "*is not*"
    logger.debug(f"zip {midfix} compressed: {path}")
    return compressed
