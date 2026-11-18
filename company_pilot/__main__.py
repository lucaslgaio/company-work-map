"""Entrada python -m company_pilot, sem efeitos durante importação."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
