from agents.graph import route_classification, route_validation


def test_transient_failures_are_alerted():
    assert route_classification({"classification": "TRANSIENT"}) == "alert_devops_node"


def test_code_defects_enter_rca():
    assert route_classification({"classification": "CODE_DEFECT"}) == "rca_discovery_node"


def test_validated_patch_creates_pr():
    assert route_validation({"status": "validated", "retry_count": 1}) == "pr_creator_node"


def test_failed_validation_retries_until_three_attempts():
    assert route_validation({"status": "validation_failed", "retry_count": 1}) == "fix_generator_node"
    assert route_validation({"status": "validation_failed", "retry_count": 3}) == "escalate_human_node"
