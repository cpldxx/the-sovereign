"""Domain tools template.
Base tools that every domain gets. OpenHands extends these per domain.
"""

from pydantic_ai import RunContext


async def collect(ctx: RunContext, query: str) -> str:
    """Collect raw data from domain-specific sources.
    Override: OpenHands fills in actual API calls, scrapers, etc.
    """
    return f"[TODO] Collect data for: {query}"


async def analyze(ctx: RunContext, data: str) -> str:
    """Analyze collected data.
    Override: OpenHands fills in domain-specific analysis logic.
    """
    return f"[TODO] Analyze: {data}"


async def summarize(ctx: RunContext, data: str) -> str:
    """Summarize findings into storable knowledge.
    Override: OpenHands fills in domain-specific summarization.
    """
    return f"[TODO] Summarize: {data}"
