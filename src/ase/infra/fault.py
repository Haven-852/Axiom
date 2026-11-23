import os


def crash_point(name: str) -> None:
    """Test-only process kill. A normal request never sets ASE_CRASH_POINT."""

    if os.environ.get("ASE_CRASH_POINT") == name:
        os._exit(86)
