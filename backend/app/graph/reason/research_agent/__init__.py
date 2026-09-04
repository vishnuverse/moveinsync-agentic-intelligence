"""Research agent -- curated static benchmark lookups (plan §8, §4 Reason subgraph).

Public surface: run_research_agent() for black-box use by the reason
subgraph's call_research_agent node, ResearchComparison as the shared result
shape, ResearchAgentError for the "no such benchmark" failure mode.
"""

from .lookup import ResearchAgentError, ResearchComparison, run_research_agent

__all__ = ["run_research_agent", "ResearchComparison", "ResearchAgentError"]
