"""`python -m nowcaption`: how the Flatpak, and launch.py when it re-executes, start NowCaption."""
import sys

from .launch import main

sys.exit(main())
