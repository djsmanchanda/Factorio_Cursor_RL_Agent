# Path: planners/city_planner/capability_registry.py
# Purpose: Static registry of available planning capabilities and prerequisites.

CAPABILITIES = {
    "rail_corridor_planning": {
        "requires": ["city_grid"],
        "provides": ["inter_block_transport"],
    },
    "station_interface_definition": {
        "requires": ["city_grid"],
        "provides": ["station_interfaces"],
    },
    "belt_backbone_planning": {
        "requires": [],
        "provides": ["local_transport"],
    },
    "power_block_planning": {
        "requires": ["city_grid"],
        "provides": ["power_capacity"],
    },
    "upgrade_phase_planning": {
        "requires": [],
        "provides": ["upgrade_phasing"],
    },
    "expansion_governance": {
        "requires": [],
        "provides": ["expansion_control"],
    },
    "buffer_block_planning": {
        "requires": ["city_grid"],
        "provides": ["buffer_capacity"],
    },
    "block_planning": {
        "requires": ["city_grid"],
        "provides": ["block_boundaries"],
    },
    "dependency_graph_planning": {
        "requires": [],
        "provides": ["block_dependency_graph"],
    },
    "flow_balancing_planning": {
        "requires": ["city_grid"],
        "provides": ["flow_balance"],
    },
    "anomaly_investigation": {
        "requires": [],
        "provides": ["anomaly_reports"],
    },
}
