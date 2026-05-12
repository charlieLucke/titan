"""Entry point for PROJECT_NAME."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the application."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("Hello from PROJECT_NAME")


if __name__ == "__main__":
    main()
