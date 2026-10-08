"""Pick query budgets wrap the gateway and JSON body sanitizer before auth/body work."""

from app.gateway.app import app as gateway_app
from app.gateway.json_body_sanitizer import JsonBodySanitizer
from app.gateway.pick_query_budget import PickQueryBudget

app = PickQueryBudget(JsonBodySanitizer(gateway_app))
