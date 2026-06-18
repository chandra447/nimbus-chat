"""LangChain Tavily research tool — app-specific (the SDK is framework-agnostic)."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from tavily import TavilyClient


def _tavily_search(api_key: str, query: str) -> str:
    client = TavilyClient(api_key=api_key)
    response = client.search(
        query=query,
        topic='general',
        max_results=5,
        search_depth='advanced',
        include_answer=True,
    )
    answer = response.get('answer', '')
    results = response.get('results', []) or []

    lines: list[str] = []
    if answer:
        lines.append(f'Summary: {answer}')
    if results:
        lines.append('Relevant sources:')
    for item in results[:5]:
        title = item.get('title', 'Untitled')
        url = item.get('url', '')
        content = (item.get('content') or '').strip().replace('\n', ' ')
        snippet = content[:280]
        lines.append(f'- {title} ({url}) :: {snippet}')
    return '\n'.join(lines)


def build_tavily_research_tool(
    api_key: str,
    *,
    tool_name: str = 'research_web',
    tool_description: str = 'Search the web for up-to-date information.',
) -> StructuredTool:
    """Build a LangChain tool that wraps Tavily search."""

    def _search(query: str) -> str:
        return _tavily_search(api_key, query)

    return StructuredTool.from_function(
        func=_search,
        name=tool_name,
        description=tool_description,
    )
