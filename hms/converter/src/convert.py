import json
import argparse
import sys
from pathlib import Path

from src.parquet import run
from src.dss_parquet import parquet_to_dss
from src.logging import log_json

__version__ = "0.1.0"


# def setup_logging(debug: bool = False, quiet: bool = False):
#     """Configure logging."""
#     level = logging.DEBUG if debug else (logging.ERROR if quiet else logging.INFO)
#     logging.basicConfig(
#         level=level,
#         format="%(levelname)s: %(message)s",
#     )
#     # Suppress HecDSS library logs
#     logging.getLogger("hecdss").setLevel(logging.ERROR)


def dss_to_parquet_cmd(args) -> int:
    """Handle DSS to Parquet conversion."""
    # Validate input file exists
    input_path = Path(args.input_dss)
    if not input_path.exists():
        log_json(
            "ERROR",
            f"Input file not found: input={input_path} output={args.output or ''}",
        )
        return 1

    # Determine output file path
    if args.output:
        output_path = Path(args.output)
        # If output is a directory, append the default filename
        if output_path.is_dir():
            output_path = output_path / f"{input_path.stem}.parquet"
        output_path = str(output_path)
    else:
        output_path = str(input_path.parent / f"{input_path.stem}.parquet")

    log_json(
        "INFO",
        f"Starting DSS to Parquet conversion: input={input_path}",
    )

    try:
        _ = run(
            input_dss_path=str(input_path),
            output_path=output_path,
            groupby=args.groupby,
            strip_suffix=not args.no_strip_suffix,
            include_parts=args.include_parts,
            group_workers=args.group_workers,
            dss_workers=args.dss_workers,
            event_id=args.event_id,
            suppress_dss_output=not args.verbose,
            use_iceberg_schema=not args.no_iceberg_schema,
        )

        log_json(
            "INFO",
            f"Conversion completed successfully: output={output_path}",
        )
        return 0
    except Exception as e:
        log_json(
            "ERROR",
            f"Conversion failed: input={input_path} output={output_path} error={e}",
        )
        return 1


def parquet_to_dss_cmd(args) -> int:
    """Handle Parquet to DSS conversion."""
    # Validate input file exists
    input_path = Path(args.input_parquet)
    if not input_path.exists():
        log_json(
            "ERROR",
            f"Input file not found: input={input_path} output={args.output or ''}",
        )
        return 1

    # Determine output path
    if args.output:
        output_path = Path(args.output)
        # If output is a directory, append the default filename
        if output_path.is_dir():
            output_path = output_path / f"{input_path.stem}.dss"
        output_path = str(output_path)
    else:
        output_path = str(input_path.parent / f"{input_path.stem}.dss")

    log_json(
        "INFO",
        f"Starting Parquet to DSS conversion: input={input_path}",
    )

    try:
        _ = parquet_to_dss(
            parquet_path=str(input_path),
            output_dss_path=output_path,
            path_f_part=args.f_part,
            suppress_dss_output=not args.verbose,
        )

        log_json(
            "INFO",
            f"Conversion completed successfully: output={output_path} exit_code=0",
        )
        return 0
    except Exception as e:
        log_json(
            "ERROR",
            f"Conversion failed: input={input_path} output={output_path} error={e}",
        )
        return 1


def main() -> int:
    """Main entrypoint with proper exit code handling."""
    try:
        return _main_impl()
    except Exception as e:
        log_json("ERROR", f"Unexpected error in entrypoint: {e}")
        return 1


def _main_impl() -> int:
    """Main implementation."""
    parser = argparse.ArgumentParser(
        description="AUTO FPT DSS utilities - Convert between DSS and Parquet formats"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", help="Conversion direction")

    # DSS to Parquet command
    dss_to_pq = subparsers.add_parser(
        "dss-to-parquet", help="Convert DSS file to Parquet format"
    )
    dss_to_pq.add_argument(
        "input_dss",
        help="Path to input DSS file",
    )
    dss_to_pq.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output Parquet file path (default: input_name.parquet in same directory)",
    )
    dss_to_pq.add_argument(
        "--groupby",
        default="F",
        help="DSS path part to group by (default: F)",
    )
    dss_to_pq.add_argument(
        "--no-strip-suffix",
        action="store_true",
        help="Do not strip version suffix from group keys",
    )
    dss_to_pq.add_argument(
        "--include-parts",
        nargs="+",
        default=["E", "F"],
        help="Additional DSS path parts to include beyond required A, B, C (default: E F; D is always excluded and auto-generated on import)",
    )
    dss_to_pq.add_argument(
        "--group-workers",
        type=int,
        default=4,
        help="Number of workers for group export (default: 4)",
    )
    dss_to_pq.add_argument(
        "--dss-workers",
        type=int,
        default=1,
        help="Number of workers for DSS file processing (default: 1)",
    )
    dss_to_pq.add_argument(
        "--event-id",
        type=int,
        default=None,
        help="Optional event ID to include in output",
    )
    dss_to_pq.add_argument(
        "--sim-name",
        default="",
        help="Optional simulation name to include in output",
    )
    dss_to_pq.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show verbose DSS library output (default: suppressed)",
    )
    dss_to_pq.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    dss_to_pq.add_argument(
        "--no-iceberg-schema",
        action="store_true",
        help="Use generic DSS column names (A, B, C, etc.) instead of iceberg schema names (provider, site_id, variable, etc.)",
    )
    dss_to_pq.set_defaults(func=dss_to_parquet_cmd)

    # Parquet to DSS command
    pq_to_dss = subparsers.add_parser(
        "parquet-to-dss", help="Convert Parquet file to DSS format"
    )
    pq_to_dss.add_argument(
        "input_parquet",
        help="Path to input Parquet file",
    )
    pq_to_dss.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output DSS file path (default: input_basename.dss in same directory)",
    )
    pq_to_dss.add_argument(
        "--f-part",
        default=None,
        help="Optional F part for DSS path",
    )
    pq_to_dss.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show verbose output (default: suppressed)",
    )
    pq_to_dss.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    pq_to_dss.set_defaults(func=parquet_to_dss_cmd)

    # Support legacy mode for backwards compatibility
    args = parser.parse_args()

    if args.command is None:
        # Legacy mode: treat first positional arg as DSS file
        if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
            args.input_dss = sys.argv[1]
            args.output = None
            args.groupby = "F"
            args.no_strip_suffix = False
            args.include_parts = ["E", "F"]
            args.group_workers = 4
            args.dss_workers = 1
            args.event_id = None
            args.sim_name = ""
            args.verbose = False
            args.debug = False
            args.no_iceberg_schema = False

            # Parse remaining args
            parser_legacy = argparse.ArgumentParser(
                description="Convert HEC-DSS files to Parquet format"
            )
            parser_legacy.add_argument("input_dss")
            parser_legacy.add_argument("-o", "--output", default=None)
            parser_legacy.add_argument("--groupby", default="F")
            parser_legacy.add_argument("--no-strip-suffix", action="store_true")
            parser_legacy.add_argument("--include-parts", nargs="+", default=["E", "F"])
            parser_legacy.add_argument("--group-workers", type=int, default=4)
            parser_legacy.add_argument("--dss-workers", type=int, default=1)
            parser_legacy.add_argument("--event-id", type=int, default=None)
            parser_legacy.add_argument("--sim-name", default="")
            parser_legacy.add_argument("-v", "--verbose", action="store_true")
            parser_legacy.add_argument("-d", "--debug", action="store_true")
            parser_legacy.add_argument("--no-iceberg-schema", action="store_true")

            args = parser_legacy.parse_args()
            return dss_to_parquet_cmd(args)
        else:
            parser.print_help()
            return 1
    else:
        return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
