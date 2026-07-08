"""Entry point for the golden fixture app."""
import os

from pyapp.util import helper


def main() -> int:
    return helper(len(os.sep))


class App:
    """Trivial class so the fixture exercises class-chunk extraction."""

    def run(self) -> int:
        return main()
