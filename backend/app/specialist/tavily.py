from __future__ import annotations

from langchain_core.tools import tool
from tavily import TavilyClient

from app.settings import Settings


class TavilyTravelResearcher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: TavilyClient | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.tavily_configured

    def _get_client(self) -> TavilyClient:
        if self._client is None:
            self._client = TavilyClient(api_key=self.settings.tavily_api_key)
        return self._client

    def research(self, query: str) -> str:
        if not self.enabled:
            return ''

        response = self._get_client().search(
            query=query,
            topic='general',
            max_results=5,
            search_depth='advanced',
            include_answer=True,
        )
        answer = response.get('answer', '')
        results = response.get('results', []) or []

        lines = []
        if answer:
            lines.append(f'Tavily summary: {answer}')
        if results:
            lines.append('Relevant travel research:')
        for item in results[:5]:
            title = item.get('title', 'Untitled')
            url = item.get('url', '')
            content = (item.get('content') or '').strip().replace('\n', ' ')
            snippet = content[:280]
            lines.append(f'- {title} ({url}) :: {snippet}')
        return '\n'.join(lines)


def build_tavily_research_tool(settings: Settings):
    researcher = TavilyTravelResearcher(settings)

    @tool
    def research_travel(query: str) -> str:
        """Search the web for current travel information, destination guides, prices, and recommendations.

        Use this tool when the user asks about specific destinations, current prices, travel conditions,
        hotels, flights, attractions, or any travel information that may benefit from up-to-date web search.

        Args:
            query: A clear, specific travel-related search query.

        Returns:
            A summary of search results including a brief answer and relevant source snippets.
        """
        if not researcher.enabled:
            return 'Tavily travel research is not configured.'
        return researcher.research(query)

    return research_travel
