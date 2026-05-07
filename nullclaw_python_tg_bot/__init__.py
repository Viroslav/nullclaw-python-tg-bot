__all__ = ["main"]


def main() -> int:
    from .bot import main as _main

    return _main()
