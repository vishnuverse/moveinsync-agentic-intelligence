"""FastAPI routers (plan §11 backend/app/api/): /roles, /dashboard,
/notifications, /threads/{id}/trace, /reports, /chat, /activity, /ws.

Every route here matches frontend/src/api/types.ts's ApiClient interface and
apiClient.ts's exact HTTP paths/methods/params/bodies -- see each module's
docstring for the specific contract it fulfills. app/main.py wires all of
these routers into one FastAPI app.
"""
