"""Thin wrapper so ``python app.py`` keeps working."""
from silmaril import app, main  # noqa: F401

if __name__ == "__main__":
    main()
