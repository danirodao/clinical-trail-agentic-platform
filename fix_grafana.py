import json

path = "observability/grafana/provisioning/dashboards/eval-dashboard.json"
with open(path, "r") as f:
    dashboard = json.load(f)

for panel in dashboard.get("panels", []):
    title = panel.get("title", "").lower()
    # Check if this panel relates to toxicity, bias, or pii_leakage
    if "toxicity" in title or "bias" in title or "pii" in title:
        # Invert the thresholds
        if "fieldConfig" in panel and "defaults" in panel["fieldConfig"]:
            custom = panel["fieldConfig"]["defaults"].get("custom", {})
            if "thresholds" in panel["fieldConfig"]["defaults"]:
                thresholds = panel["fieldConfig"]["defaults"]["thresholds"]["steps"]
                # Default steps list normally looks like:
                # [ {"color": "red", "value": null}, {"color": "yellow", "value": 0.4}, {"color": "green", "value": 0.7} ]
                # We should invert it:
                # [ {"color": "green", "value": null}, {"color": "yellow", "value": 0.3}, {"color": "red", "value": 0.6} ]
                panel["fieldConfig"]["defaults"]["thresholds"]["steps"] = [
                    {"color": "green", "value": None},
                    {"color": "yellow", "value": 0.3},
                    {"color": "red", "value": 0.6}
                ]
        
        # If there are overrides, we shouldn't necessarily nuke them, but if they match the metric name, we can fix them.
    if "fieldConfig" in panel and "overrides" in panel["fieldConfig"]:
        for override in panel["fieldConfig"]["overrides"]:
            matcher_id = override.get("matcher", {}).get("options", "")
            if isinstance(matcher_id, str):
                m_lower = matcher_id.lower()
                if "toxicity" in m_lower or "bias" in m_lower or "pii" in m_lower:
                    for prop in override.get("properties", []):
                        if prop.get("id") == "thresholds":
                            prop["value"]["steps"] = [
                                {"color": "green", "value": None},
                                {"color": "yellow", "value": 0.3},
                                {"color": "red", "value": 0.6}
                            ]

with open(path, "w") as f:
    json.dump(dashboard, f, indent=2)

print("Updated Grafana dashboard JSON.")
