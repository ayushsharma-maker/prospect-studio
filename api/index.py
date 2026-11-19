"""Vercel entry point: every path is rewritten here (vercel.json) and served by src/server.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server import Handler  # noqa: E402


class handler(Handler):  # Vercel's Python runtime looks for a class named `handler`
    pass
