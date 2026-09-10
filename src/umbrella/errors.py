"""The one exception this program raises for a person to read.

Its own module because both `lock` and `model` raise it, and `model` reads the
lock. A shared error in either of them would be a cycle.
"""

from __future__ import annotations


class UmbrellaError(RuntimeError):
    """The umbrella is not usable."""
