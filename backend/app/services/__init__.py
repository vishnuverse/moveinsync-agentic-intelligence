"""Persona-scoping / cross-cutting helpers used by the API and scheduler
layers (plan §11's backend/app/services/). Distinct from app/graph/ -- these
are plain read/write helpers around act-layer output tables and the new
pipeline_runs log, not LangGraph nodes.
"""
