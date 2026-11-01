#!/usr/bin/env python3
"""Deterministically turn an ARM template into a runnable `diagrams` script.

This is the reliable, reproducible path for "diagram from my infrastructure": instead of
asking the model to free-read templates and hand-write Python (which misses or hallucinates
resources), it parses the ARM JSON directly - resources plus their `dependsOn` edges - and
emits a `diagrams` program using a maintained Microsoft.* -> node-class map.

ARM JSON is also what `az bicep build --file main.bicep` emits, so Bicep is covered too:
    az bicep build --file main.bicep --outfile main.json
    python iac_to_diagram.py main.json --render

Usage:
    python iac_to_diagram.py <template.json>            # print the generated diagrams script
    python iac_to_diagram.py <template.json> --render   # also render diagrams/arm-architecture.png

Unmapped resource types fall back to a generic node labelled with the type, so nothing is
silently dropped.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

# Microsoft.* resource type (lowercased) -> (diagrams submodule, class name).
TYPE_MAP = {
    "microsoft.web/sites": ("compute", "AppServices"),
    "microsoft.web/serverfarms": ("web", "AppServicePlans"),
    "microsoft.web/staticsites": ("web", "StaticApps"),
    "microsoft.compute/virtualmachines": ("compute", "VM"),
    "microsoft.compute/virtualmachinescalesets": ("compute", "VMScaleSet"),
    "microsoft.containerservice/managedclusters": ("compute", "AKS"),
    "microsoft.containerregistry/registries": ("compute", "ACR"),
    "microsoft.app/containerapps": ("compute", "ContainerApps"),
    "microsoft.storage/storageaccounts": ("storage", "StorageAccounts"),
    "microsoft.sql/servers": ("database", "SQLServers"),
    "microsoft.sql/servers/databases": ("database", "SQLDatabases"),
    "microsoft.documentdb/databaseaccounts": ("database", "CosmosDb"),
    "microsoft.cache/redis": ("database", "CacheForRedis"),
    "microsoft.dbforpostgresql/flexibleservers": ("database", "DatabaseForPostgresqlServers"),
    "microsoft.keyvault/vaults": ("security", "KeyVaults"),
    "microsoft.managedidentity/userassignedidentities": ("identity", "ManagedIdentities"),
    "microsoft.network/virtualnetworks": ("network", "VirtualNetworks"),
    "microsoft.network/applicationgateways": ("network", "ApplicationGateway"),
    "microsoft.network/privateendpoints": ("network", "PrivateEndpoint"),
    "microsoft.network/azurefirewalls": ("network", "Firewall"),
    "microsoft.network/frontdoors": ("network", "FrontDoors"),
    "microsoft.cdn/profiles": ("network", "CDNProfiles"),
    "microsoft.servicebus/namespaces": ("integration", "ServiceBus"),
    "microsoft.eventgrid/topics": ("integration", "EventGridTopics"),
    "microsoft.eventhub/namespaces": ("analytics", "EventHubs"),
    "microsoft.logic/workflows": ("integration", "LogicApps"),
    "microsoft.apimanagement/service": ("integration", "APIManagement"),
    "microsoft.insights/components": ("monitor", "ApplicationInsights"),
    "microsoft.operationalinsights/workspaces": ("monitor", "LogAnalyticsWorkspaces"),
    "microsoft.cognitiveservices/accounts": ("aimachinelearning", "CognitiveServices"),
}
FALLBACK = ("general", "Resource")


def _safe_var(name: str, used: set) -> str:
    base = "n_" + re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    var = base or "n_res"
    i = 1
    while var in used:
        i += 1
        var = f"{base}_{i}"
    used.add(var)
    return var


def _clean(name: str) -> str:
    # Shorten ARM expressions like "[parameters('x')]" to something readable.
    m = re.search(r"'([^']+)'", name)
    return m.group(1) if name.startswith("[") and m else name


def _dep_targets(depends_on) -> list:
    """Return the referenced resource names from a dependsOn list."""
    targets = []
    for dep in depends_on or []:
        # resourceId('Type/sub', 'name', ...) -> last quoted arg is the name
        names = re.findall(r"'([^']+)'", dep)
        if names:
            targets.append(names[-1])
        else:
            targets.append(_clean(dep))
    return targets


def generate(template: dict) -> str:
    resources = template.get("resources", [])
    used_vars: set = set()
    by_name = {}
    nodes = []  # (var, module, cls, label)
    for res in resources:
        rtype = (res.get("type") or "").strip()
        name = _clean(res.get("name") or rtype.split("/")[-1])
        module, cls = TYPE_MAP.get(rtype.lower(), FALLBACK)
        label = name if (module, cls) != FALLBACK else f"{name}\\n[{rtype}]"
        var = _safe_var(name, used_vars)
        by_name[name] = var
        nodes.append((var, module, cls, label))

    edges = []
    for res, (var, *_rest) in zip(resources, nodes):
        for target in _dep_targets(res.get("dependsOn")):
            if target in by_name:
                edges.append((by_name[target], var))  # dependency >> dependent

    # Build imports grouped by module
    classes_by_module: dict = {}
    for _var, module, cls, _label in nodes:
        classes_by_module.setdefault(module, set()).add(cls)

    lines = ["from diagrams import Diagram, Edge"]
    for module in sorted(classes_by_module):
        names = ", ".join(sorted(classes_by_module[module]))
        lines.append(f"from diagrams.azure.{module} import {names}")
    lines.append("")
    lines.append('with Diagram("Architecture (from ARM)", filename="diagrams/arm-architecture", '
                 'show=False, outformat="png"):')
    for var, _module, cls, label in nodes:
        lines.append(f'    {var} = {cls}("{label}")')
    if edges:
        lines.append("")
        for src, dst in edges:
            lines.append(f"    {src} >> {dst}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("template", help="path to an ARM template JSON (or `az bicep build` output)")
    ap.add_argument("--render", action="store_true", help="also render diagrams/arm-architecture.png")
    args = ap.parse_args()

    with open(args.template, encoding="utf-8") as f:
        template = json.load(f)

    code = generate(template)
    if args.render:
        import os
        os.makedirs("diagrams", exist_ok=True)
        exec(compile(code, "<generated>", "exec"), {"__name__": "__main__"})
        print("Rendered diagrams/arm-architecture.png")
    else:
        print(code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
