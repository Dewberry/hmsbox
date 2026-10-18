"""Inspect compression of existing file and recommend per-file compression method for writing to archive."""

import struct
import zipfile
from pathlib import Path

from tifftools import read_tiff
from tifftools.constants import Compression, Tag
from tifftools.exceptions import TifftoolsError
from vault_logging import setup_json_logging


SOZIP_CHUNK_SIZE_KB = 512
SOZIP_MIN_DEFLATE_SIZE_KB = 64


def recommended_compression_method(
    path: Path,
    logger,
    min_deflate_size_kb: int = SOZIP_MIN_DEFLATE_SIZE_KB,
    no_inspect_existing_compression: bool = False,
) -> int:
    """Return a recommended zipfile.* compression method to apply for the provided file.

    ``min_deflate_size_kb``: Return ZIP_STORED for files smaller than this. Provide 0 to disable (return ZIP_DEFLATED regardless of size).
    ``no_inspect_existing_compression``: If False (default), for certain file extensions, attempt to inspect their existing compression and return ZIP_STORED instead of ZIP_DEFLATED if they are already compressed. If True, disable this inspection and return ZIP_DEFLATED regardless.
    """
    if min_deflate_size_kb < 0:
        raise ValueError(
            f"min_deflate_size_kb must be greater than 0 but got {min_deflate_size_kb}"
        )

    size_kb = path.stat().st_size / 1024
    if min_deflate_size_kb > 0 and size_kb < min_deflate_size_kb:
        return zipfile.ZIP_STORED
    if no_inspect_existing_compression:
        return zipfile.ZIP_DEFLATED
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
            f"Failed to determine existing compression for path={path!r} (size_kb~{size_kb:.0f}). Proceeding as if not compressed (recommending to apply ZIP_DEFLATED). Error: {e}."
        )
        return zipfile.ZIP_DEFLATED
    return zipfile.ZIP_STORED if compressed else zipfile.ZIP_DEFLATED


def _file_is_known_compressed(path: Path) -> bool:
    """Return True if the file is known to be already compressed, else False.

    Only certain extensions are inspected, and other extensions are assumed False.

    Some use cases still may wish to apply (further) compression to these,
    hence the ``--no-inspect-exist-compress`` option.

    TODO: Consider inspecting more extensions that sometimes have existing compression,
    such as: .gz, .nc/.nc4/.h5/.he5/.hdf/.hdf5, .grb/.grib/.grb2/.grib2, .dss, .parquet, .png.
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
    """Return False if its uncompressed members total more than 2 MB or represent more than 20% of the total bytes. Else return True.

    Look for: ZIP_DEFLATED, ZIP_BZIP2, ZIP_LZMA.
    """
    limit_mb_uncompressed = 4
    limit_ratio_uncompressed = 0.2

    logger = setup_json_logging()
    logger.debug(f"Inspecting compression of zip: {path}")

    compressed_mb = 0.0
    uncompressed_mb = 0.0
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or not entry.file_size:
                continue
            if entry.compress_type in (
                zipfile.ZIP_DEFLATED,
                zipfile.ZIP_BZIP2,
                zipfile.ZIP_LZMA,
            ):
                compressed_mb += entry.compress_size / (1024 * 1024)
            else:
                uncompressed_mb += entry.file_size / (1024 * 1024)

    total_mb = compressed_mb + uncompressed_mb
    uncompressed_ratio = uncompressed_mb / total_mb if total_mb else 0
    is_adequately_compressed = not (
        uncompressed_mb > limit_mb_uncompressed
        or uncompressed_ratio > limit_ratio_uncompressed
    )

    midfix = "*is*" if is_adequately_compressed else "*is not*"
    logger.debug(
        f"zip {midfix} adequately compressed: {path} (compressed={compressed_mb:.2f} MB, uncompressed={uncompressed_mb:.2f} MB, uncompressed_percent={uncompressed_ratio:.1%})"
    )
    return is_adequately_compressed
