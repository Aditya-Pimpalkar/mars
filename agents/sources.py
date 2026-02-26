"""
agents/sources.py
─────────────────
Data source profiles for MARS.
Kept in a separate file to avoid circular imports.
"""

DATA_SOURCES = {
    "demo": {
        "label":       "Demo Data",
        "description": "Synthetic incident — Jan 21 2026 DB pool exhaustion with planted contradictions",
        "metrics_index":     "metrics-mars",
        "logs_index":        "logs-mars",
        "deployments_index": "deployments-mars",
        "incidents_index":   "incidents-mars",
        "runbooks_index":    "runbooks-mars",
        "query_start":       "2026-01-21T13:30:00Z",
        "query_end":         "2026-01-21T16:00:00Z",
        "threshold":         200,
    },
    "weblogs": {
        "label":       "Sample Web Logs",
        "description": "Elastic sample nginx web logs — 14k real HTTP requests with response codes and bytes",
        "metrics_index":     "kibana_sample_data_logs",
        "logs_index":        "kibana_sample_data_logs",
        "deployments_index": "deployments-mars",
        "incidents_index":   "incidents-mars",
        "runbooks_index":    "runbooks-mars",
        "query_start":       "now-7d",
        "query_end":         "now",
        "threshold":         1000,
    },
    "ecommerce": {
        "label":       "Sample eCommerce",
        "description": "Elastic sample eCommerce orders — 4.6k orders with revenue, products, and customer data",
        "metrics_index":     "kibana_sample_data_ecommerce",
        "logs_index":        "kibana_sample_data_ecommerce",
        "deployments_index": "deployments-mars",
        "incidents_index":   "incidents-mars",
        "runbooks_index":    "runbooks-mars",
        "query_start":       "now-30d",
        "query_end":         "now",
        "threshold":         100,
    },
}

def get_source_config(source_id: str = "demo") -> dict:
    return DATA_SOURCES.get(source_id, DATA_SOURCES["demo"])