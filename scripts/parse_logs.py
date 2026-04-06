#!/usr/bin/env python3
"""
Parse a local build log file and extract error context.
Useful for testing the log parser against captured log files.

Usage:
    python scripts/parse_logs.py --input tests/sample_logs/build_failure.log
    python scripts/parse_logs.py --input logs/build_job_123.log --max-tokens 40000
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.log_parser import extract_error_context, trim_to_token_limit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("parse-logs")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a local build log file")
    parser.add_argument("--input", required=True, help="Path to log file")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=80000,
        help="Maximum tokens for trimmed output (default: 80000)",
    )
    parser.add_argument(
        "--context-lines",
        type=int,
        default=10,
        help="Context lines around each error (default: 10)",
    )
    parser.add_argument(
        "--show-full",
        action="store_true",
        help="Show full trimmed log instead of just error context",
    )
    args = parser.parse_args()

    log_path = Path(args.input)
    if not log_path.exists():
        logger.error("File not found: %s", log_path)
        sys.exit(1)

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    logger.info("Read %d bytes from %s", len(log_text), log_path)

    error_lines = extract_error_context(log_text, context_lines=args.context_lines)
    logger.info("Extracted %d error-context lines", len(error_lines))

    if args.show_full:
        trimmed = trim_to_token_limit(log_text, args.max_tokens)
        print("\n" + "=" * 60)
        print("TRIMMED LOG")
        print("=" * 60)
        print(trimmed)
    else:
        if error_lines:
            print("\n" + "=" * 60)
            print(f"ERROR CONTEXT ({len(error_lines)} lines)")
            print("=" * 60)
            print("\n".join(error_lines))
        else:
            print("\n[No error patterns found in log file]")
            print("Run with --show-full to see the trimmed log content")


if __name__ == "__main__":
    main()
