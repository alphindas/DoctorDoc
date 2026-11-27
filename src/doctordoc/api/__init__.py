"""The HTTP API (optional: pip install "doctordoc[api]"). Run it with: doctordoc api"""

from doctordoc.api.app import create_app

__all__ = ["create_app"]
