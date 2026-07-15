"""Device profiles used by image mode to size page images for a target screen."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceProfile:
    key: str
    label: str
    width: int
    height: int
    ppi: int
    notes: str = ""


PROFILES: dict[str, DeviceProfile] = {
    "generic-6in": DeviceProfile(
        "generic-6in", "Generic 6-inch e-reader", 758, 1024, 212,
        "Safe default for most 6-inch readers",
    ),
    "generic-6in-hd": DeviceProfile(
        "generic-6in-hd", "Generic 6-inch HD e-reader", 1072, 1448, 300,
        "Kobo Clara HD class screens",
    ),
    "xteink-x4": DeviceProfile(
        "xteink-x4", "Xteink X4 (CrossPoint)", 480, 800, 220,
        "For text mode: 'pdf2ebook fonts install' once + convert with --preshape",
    ),
    "kindle-pw11": DeviceProfile(
        "kindle-pw11", "Kindle Paperwhite 11", 1236, 1648, 300,
        "Send the EPUB via Send-to-Kindle",
    ),
    "none": DeviceProfile(
        "none", "Keep source resolution", 0, 0, 0,
        "No downscaling; largest files",
    ),
}

DEFAULT_PROFILE = "generic-6in"


def get_profile(key: str) -> DeviceProfile:
    try:
        return PROFILES[key]
    except KeyError:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown device profile '{key}'. Valid profiles: {valid}") from None
