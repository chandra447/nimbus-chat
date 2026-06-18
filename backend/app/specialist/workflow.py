from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class TravelPlanningState(TypedDict, total=False):
    user_input: str
    travel_brief: str


def build_travel_planning_graph():
    graph = StateGraph(TravelPlanningState)

    def prepare_brief(state: TravelPlanningState) -> TravelPlanningState:
        user_input = state.get('user_input', '')
        return {
            'travel_brief': (
                'Create a practical travel planning response for the following user request. '
                'Include assumptions, budget-awareness if relevant, and actionable suggestions.\n\n'
                f'User request: {user_input}'
            )
        }

    graph.add_node('prepare_brief', prepare_brief)
    graph.add_edge(START, 'prepare_brief')
    graph.add_edge('prepare_brief', END)
    return graph.compile()
