"""Command-line entry points for the invoice extraction pipeline."""

from __future__ import annotations

import argparse

from invoice_extractor import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invoice-extract",
        description="Run invoice extraction and reconciliation tasks.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main() -> None:
    parser = build_parser()
    parser.parse_args()
    parser.print_help()
