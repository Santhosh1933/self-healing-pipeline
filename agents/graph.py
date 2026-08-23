"""StateGraph assembly and conditional routing."""

from config.settings import Settings
from langgraph.graph import END, START, StateGraph
from agents.nodes import alert_devops_node, classify_error_node, escalate_human_node, fix_generator_node, pr_creator_node, rca_discovery_node, validator_node
from agents.state import PipelineTriageState


def route_classification(state: PipelineTriageState) -> str:
    """Route transient failures to alerting and defects to RCA."""
    return "alert_devops_node" if state.get("classification") == "TRANSIENT" else "rca_discovery_node"


def route_validation(state: PipelineTriageState, max_attempts: int = 3) -> str:
    """Route validation success, retryable failures, and exhausted failures."""
    if state.get("status") == "validated":
        return "pr_creator_node"
    return "fix_generator_node" if state.get("retry_count", 0) < max_attempts else "escalate_human_node"


def build_graph(settings: Settings):
    """Build and compile the complete asynchronous triage graph."""
    graph = StateGraph(PipelineTriageState)
    graph.add_node("classify_error_node", lambda state: classify_error_node(state, settings))
    graph.add_node("rca_discovery_node", lambda state: rca_discovery_node(state, settings))
    graph.add_node("fix_generator_node", lambda state: fix_generator_node(state, settings))
    graph.add_node("validator_node", lambda state: validator_node(state, settings))
    graph.add_node("pr_creator_node", lambda state: pr_creator_node(state, settings))
    graph.add_node("alert_devops_node", alert_devops_node)
    graph.add_node("escalate_human_node", escalate_human_node)
    graph.add_edge(START, "classify_error_node")
    graph.add_conditional_edges("classify_error_node", route_classification)
    graph.add_edge("rca_discovery_node", "fix_generator_node")
    graph.add_edge("fix_generator_node", "validator_node")
    graph.add_conditional_edges("validator_node", lambda state: route_validation(state, settings.max_repair_attempts))
    for terminal in ("pr_creator_node", "alert_devops_node", "escalate_human_node"):
        graph.add_edge(terminal, END)
    return graph.compile()
