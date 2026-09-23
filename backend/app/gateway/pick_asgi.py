"""What the pick entrypoint serves: the gateway behind the JSON body sanitizer (pick workbench plan 6.7)."""

from app.gateway.app import app as gateway_app
from app.gateway.json_body_sanitizer import JsonBodySanitizer

app = JsonBodySanitizer(gateway_app)
