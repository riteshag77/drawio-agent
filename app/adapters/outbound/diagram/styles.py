"""Programmatic draw.io style builder - no external shape files needed.

Derives draw.io shape styles from shape_type names using convention over configuration.
To update to a newer draw.io shape library, change only the version prefix constants below.
"""

_ICON_LABEL = (
    "fontSize=12;fontColor=#232F3E;labelBackgroundColor=#ffffff;"
    "verticalLabelPosition=bottom;verticalAlign=top;align=center;spacingTop=4;spacingBottom=8;"
    "fontFamily=Helvetica;"
)
AWS_LIB = "mxgraph.aws4"           # Also used for generic/user/internet shapes
GCP_LIB = "mxgraph.gcp2"
AZURE_LIB = "img/lib/azure2"       # SVG path prefix for Azure icons


# \u2014\u2014 AWS shape names that don't follow the direct snake_case pattern \u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
_AWS_ICON_EXCEPTIONS: dict[str, str] = {
    "route53": "route_53",
    "shield_advanced": "shield",
    "iam": "identity_and_access_management",
    "kms": "key_management_service",
    "ebs": "elastic_block_store",
    "efs": "elastic_file_system",
    "elb": "elastic_load_balancing",
    "alb": "application_load_balancer",
    "nlb": "network_load_balancer",
    # draw.io still uses the pre-rename OpenSearch stencil id
    "opensearch": "elasticsearch_service",
    "opensearch_service": "elasticsearch_service",
    "privatelink": "vpc_privatelink",
    "xray": "x_ray",
    "ecs_task": "ecs",
    "msk": "managed_streaming_for_kafka",
    "kinesis_data_streams": "kinesis",
    "kinesis_data_stream": "kinesis",
}

# Known-broken / empty-square resIcons → working substitutes (vidanov skill denylist).
_AWS_BROKEN_RESICONS: dict[str, str] = {
    "dynamodb_table": "dynamodb",
    "dynamodb_stream": "dynamodb",
    "general_saml_token": "traditional_server",
    "endpoint": "endpoints",
    "kinesis_data_streams": "kinesis",
}

_AWS_GROUP_ICONS: dict[str, str] = {
    "vpc": "group_vpc2",
    "region": "group_region",
    "az": "group_availability_zone",
    "public_subnet": "group_security_group",
    "private_subnet": "group_security_group",
    "security_group": "group_security_group",
    "account": "group_account",
    "cloud": "group_aws_cloud",
    "auto_scaling_group": "group_auto_scaling_group",
    "generic_group": "group_aws_cloud_alt",
}


# \u2014\u2014 GCP shape names that differ from the snake_case name \u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
_GCP_ICON_EXCEPTIONS: dict[str, str] = {
    "cloud_dns": "dns",
    "cloud_monitoring": "monitoring",
    "cloud_logging": "logging",
    "cloud_trace": "trace",
    "cloud_spanner": "spanner",
    "cloud_nat": "cloud_nat",
    "cloud_tasks": "cloud_tasks",
    "cloud_storage": "cloud_storage",
    "cloud_sql": "cloud_sql",
    "persistent_disk": "persistent_disk",
    "container_registry": "container_registry",
    "artifact_registry": "artifact_registry",
    "cloud_run": "cloud_run",
    "cloud_functions": "cloud_functions",
    "app_engine": "app_engine",
    "compute_engine": "compute_engine",
    "kubernetes_engine": "kubernetes_engine",
    "identity_aware_proxy": "identity_aware_proxy",
    "cloud_armor": "cloud_armor",
    "secret_manager": "secret_manager",
    "load_balancing": "load_balancing",
    "interconnect": "interconnect",
    "api_gateway": "api_gateway",
    "cloud_build": "cloud_build",
}


# \u2014\u2014 Azure SVG path mapping (shape_type suffix -> (category, filename)) \u2014\u2014\u2014\u2014\u2014\u2014\u2014
_AZURE_ICONS: dict[str, tuple[str, str]] = {
    "vm": ("compute", "Virtual_Machines.svg"),
    "aks": ("containers", "Kubernetes_Services.svg"),
    "function": ("compute", "Function_Apps.svg"),
    "app_service": ("compute", "App_Services.svg"),
    "container_registry": ("containers", "Container_Registries.svg"),
    "databricks": ("analytics", "Databricks.svg"),
    "dns": ("networking", "DNS_Zones.svg"),
    "cdn": ("networking", "CDN_Profiles.svg"),
    "api_gateway": ("networking", "API_Management_Services.svg"),
    "load_balancer": ("networking", "Load_Balancers.svg"),
    "application_gateway": ("networking", "Application_Gateways.svg"),
    "nat_gateway": ("networking", "NAT.svg"),
    "front_door": ("networking", "Front_Doors.svg"),
    "express_route": ("networking", "ExpressRoute_Circuits.svg"),
    "vpn_gateway": ("networking", "VPN_Gateways.svg"),
    "ad": ("identity", "Azure_Active_Directory.svg"),
    "key_vault": ("security", "Key_Vaults.svg"),
    "firewall": ("security", "Firewalls.svg"),
    "sentinel": ("security", "Microsoft_Sentinel.svg"),
    "ddos": ("security", "DDoS_Protection.svg"),
    "blob_storage": ("storage", "Storage_Accounts.svg"),
    "sql_db": ("databases", "SQL_Database.svg"),
    "cosmos_db": ("databases", "Cosmos_DB.svg"),
    "cache_redis": ("databases", "Cache_Redis.svg"),
    "synapse": ("analytics", "Synapse_Analytics.svg"),
    "monitor": ("management_governance", "Monitor.svg"),
    "log_analytics": ("management_governance", "Log_Analytics_Workspaces.svg"),
    "event_hub": ("analytics", "Event_Hubs.svg"),
    "service_bus": ("integration", "Service_Bus.svg"),
    "logic_apps": ("integration", "Logic_Apps.svg"),
    "data_factory": ("data_factories", "Data_Factories.svg"),
}

# Aliases so common synonyms resolve to a verified icon above.
_AZURE_ALIASES: dict[str, str] = {
    "load_balancer": "load_balancer",
    "redis": "cache_redis",
    "postgresql": "sql_db",
    "mysql": "sql_db",
    "sql_managed_instance": "sql_db",
    "sql_database": "sql_db",
    "app_services": "app_service",
    "functions": "function",
    "kubernetes_services": "aks",
    "api_management": "api_gateway",
    "synapse_analytics": "synapse",
    "data_lake": "blob_storage",
    "file_storage": "blob_storage",
    "queue_storage": "blob_storage",
    "table_storage": "blob_storage",
    "event_grid": "event_hub",
    "stream_analytics": "synapse",
    "ml": "databricks",
    "openai": "databricks",
    "cognitive_services": "databricks",
    "container_app": "aks",
    "container_instances": "aks",
    "static_web_app": "app_service",
    "vmss": "vm",
    "traffic_manager": "front_door",
    "bastion": "firewall",
    "managed_identity": "ad",
    "defender": "sentinel",
}

# Legacy / pattern-library keys \u2192 canonical AWS suffix (after stripping aws_).
_AWS_ALIASES: dict[str, str] = {
    "route_53": "route53",
    "shield_advanced": "shield",
    "application_load_balancer": "alb",
    "elastic_load_balancer": "alb",
    "network_load_balancer": "nlb",
}

# Legacy GCP keys \u2192 canonical suffix (after stripping gcp_).
_GCP_ALIASES: dict[str, str] = {
    "cloud_cdn": "cdn",
    "cloud_load_balancing": "load_balancing",
}


def normalize_shape_type(shape_type: str) -> str:
    """Map legacy pattern-library keys to canonical catalogue shape keys."""
    if not shape_type:
        return shape_type
    if shape_type.startswith("aws_"):
        suffix = shape_type[4:]
        canonical = _AWS_ALIASES.get(suffix, suffix)
        return f"aws_{canonical}" if canonical != suffix else shape_type
    if shape_type.startswith("gcp_"):
        suffix = shape_type[4:]
        canonical = _GCP_ALIASES.get(suffix, suffix)
        return f"gcp_{canonical}" if canonical != suffix else shape_type
    if shape_type.startswith("azure_"):
        suffix = shape_type[6:]
        canonical = _AZURE_ALIASES.get(suffix)
        return f"azure_{canonical}" if canonical and canonical != suffix else shape_type
    return shape_type


# \u2014\u2014 Category-based color maps \u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
_AWS_COLORS: dict[str, str] = {
    "compute": "#ED7100",
    "networking": "#8C4FFF",
    "security": "#DD344C",
    "storage": "#3F8624",
    "database": "#C925D1",
    "monitoring": "#E7157B",
    "integration": "#E7157B",
    "ml": "#01A88D",
    "devops": "#C7131F",
    "management": "#E7157B",
    "default": "#ED7100",
}

_GCP_COLORS: dict[str, str] = {
    "compute": "#4285F4",
    "networking": "#4285F4",
    "security": "#EA4335",
    "storage": "#FBBC05",
    "database": "#FBBC05",
    "monitoring": "#34A853",
    "integration": "#4285F4",
    "ml": "#4285F4",
    "default": "#4285F4",
}


def _aws_category(name: str) -> str:
    compute = {"ec2", "lambda", "fargate", "ecs", "ecs_task", "eks", "ecr",
               "elastic_beanstalk", "app_runner", "lightsail"}
    networking = {"route53", "cloudfront", "elb", "alb", "nlb",
                  "nat_gateway", "internet_gateway", "vpn_gateway", "direct_connect",
                  "privatelink", "transit_gateway", "global_accelerator", "client_vpn"}
    security = {"waf", "shield", "iam", "kms", "secrets_manager", "certificate_manager",
                "guardduty", "config", "cloudhsm", "cognito", "directory_service"}
    storage = {"s3", "ebs", "efs", "glacier", "storage_gateway", "backup"}
    database = {"rds", "aurora", "dynamodb", "elasticache", "redshift", "neptune",
                "documentdb", "timestream", "qlab"}
    monitoring = {"cloudwatch", "cloudtrail", "xray", "opensearch"}
    # api_gateway is Integration purple (#E7157B) per AWS Architecture Icons / vidanov
    integration = {"api_gateway", "sqs", "sns", "eventbridge", "step_functions", "kinesis",
                   "msk", "appsync"}
    ml = {"sagemaker", "bedrock", "glue", "athena", "emr"}
    devops = {"codepipeline", "codebuild", "codedeploy", "codecommit", "amplify"}
    management = {"systems_manager", "trusted_advisor"}
    if name in compute: return "compute"
    if name in networking: return "networking"
    if name in security: return "security"
    if name in storage: return "storage"
    if name in database: return "database"
    if name in monitoring: return "monitoring"
    if name in integration: return "integration"
    if name in ml: return "ml"
    if name in devops: return "devops"
    if name in management: return "management"
    return "default"


def _gcp_category(name: str) -> str:
    compute = {"compute_engine", "kubernetes_engine", "cloud_run", "cloud_functions",
               "app_engine", "container_registry", "artifact_registry"}
    networking = {"cloud_dns", "cdn", "api_gateway", "apigee", "load_balancing",
                  "router", "interconnect", "cloud_nat"}
    security = {"iam", "kms", "secret_manager", "cloud_armor", "identity_aware_proxy"}
    storage = {"cloud_storage", "persistent_disk"}
    database = {"cloud_sql", "cloud_spanner", "bigtable", "firestore", "memorystore", "bigquery"}
    monitoring = {"cloud_monitoring", "cloud_logging", "cloud_trace"}
    integration = {"pubsub", "cloud_tasks", "workflows", "dataflow", "composer"}
    ml = {"vertex_ai", "cloud_build"}
    if name in compute: return "compute"
    if name in networking: return "networking"
    if name in security: return "security"
    if name in storage: return "storage"
    if name in database: return "database"
    if name in monitoring: return "monitoring"
    if name in integration: return "integration"
    if name in ml: return "ml"
    return "default"


def _generic_shapes(shape_type: str) -> str | None:
    """Return a style string for known generic shapes, or None."""
    if shape_type == "user_client":
        return (f"outlineConnect=0;dashed=0;html=1;shape={AWS_LIB}.client;"
                f"fillColor=#232F3E;{_ICON_LABEL}")
    if shape_type == "user_mobile":
        return (f"outlineConnect=0;dashed=0;html=1;shape={AWS_LIB}.mobile_client;"
                f"fillColor=#232F3E;fontColor=#232F3E;")
    if shape_type == "user_users":
        return (f"outlineConnect=0;dashed=0;html=1;shape={AWS_LIB}.users;"
                f"fillColor=#232F3E;fontColor=#232F3E;")
    if shape_type == "external_service":
        return (f"outlineConnect=0;dashed=0;html=1;shape={AWS_LIB}.traditional_server;"
                f"fillColor=#232F3E;fontColor=#232F3E;")
    if shape_type == "internet":
        return (f"outlineConnect=0;dashed=0;html=1;shape={AWS_LIB}.internet_alt2;"
                f"fillColor=#232F3E;fontColor=#232F3E;")
    if shape_type == "generic_database":
        return ("rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;"
                "arcSize=10;fontSize=12;fontColor=#232F3E;shape=cylinder3;")
    if shape_type == "generic_server":
        return ("rounded=1;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;"
                "arcSize=10;fontSize=12;fontColor=#232F3E;")
    if shape_type == "generic_api":
        return ("rounded=1;whiteSpace=wrap;html=1;fillColor=#ffe6cc;strokeColor=#d79b00;"
                "arcSize=10;fontSize=12;fontColor=#232F3E;shape=step;")
    return None


def _group_points() -> str:
    return ("points=[[0,0],[0.25,0],[0.5,0],[0.75,0],[1,0],[1,0.25],[1,0.5],[1,0.75],"
            "[1,1],[0.75,1],[0.5,1],[0.25,1],[0,1],[0,0.75],[0,0.5],[0,0.25]];")

_GROUP_BASE = (
    "outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=12;fontStyle=1;"
    "container=1;pointerEvents=0;collapsible=0;recursiveResize=0;rounded=1;"
)

_AWS_GROUP = _group_points() + _GROUP_BASE + "shape=" + AWS_LIB + ".group;"
_GCP_AZURE_GROUP = _group_points() + _GROUP_BASE + "arcSize=8;"


def _aws_container(name: str) -> str:
    """Build style for an AWS container (group) shape."""
    icon = _AWS_GROUP_ICONS.get(name, f"group_{name}")
    base = _AWS_GROUP + f"grIcon={AWS_LIB}.{icon};"

    configs = {
        "vpc": "strokeColor=#8C4FFF;fillColor=none;verticalAlign=top;align=left;spacingLeft=40;spacingTop=-4;fontColor=#8C4FFF;dashed=0;strokeWidth=2;",
        "region": "strokeColor=#00A4A6;fillColor=none;verticalAlign=top;align=left;spacingLeft=40;spacingTop=-4;fontColor=#00A4A6;dashed=1;strokeWidth=1.5;",
        "az": "strokeColor=#545B64;fillColor=none;verticalAlign=top;align=left;spacingLeft=40;spacingTop=-4;fontColor=#545B64;dashed=1;strokeWidth=1.5;",
        "public_subnet": "grStroke=0;strokeColor=#7AA116;fillColor=none;verticalAlign=top;align=left;spacingLeft=40;spacingTop=-4;fontColor=#248814;dashed=0;strokeWidth=1.5;",
        "private_subnet": "grStroke=0;strokeColor=#00A4A6;fillColor=none;verticalAlign=top;align=left;spacingLeft=40;spacingTop=-4;fontColor=#147EBA;dashed=0;strokeWidth=1.5;",
        "security_group": "grStroke=0;strokeColor=#DD3522;fillColor=none;verticalAlign=top;align=left;spacingLeft=40;spacingTop=-4;fontColor=#DD3522;dashed=0;strokeWidth=1.5;",
        "account": "strokeColor=#CD2264;fillColor=none;verticalAlign=top;align=left;spacingLeft=40;spacingTop=-4;fontColor=#CD2264;dashed=0;strokeWidth=2;",
        "cloud": "strokeColor=#232F3E;fillColor=none;verticalAlign=top;align=left;spacingLeft=40;spacingTop=-4;fontColor=#232F3E;dashed=0;strokeWidth=2;",
        "generic_group": "strokeColor=#232F3E;fillColor=none;verticalAlign=top;align=left;spacingLeft=40;spacingTop=-4;fontColor=#232F3E;dashed=1;strokeWidth=1.5;",
        "auto_scaling_group": "grStroke=0;strokeColor=#D86613;fillColor=none;verticalAlign=top;align=left;spacingLeft=40;spacingTop=-4;fontColor=#D86613;dashed=1;strokeWidth=1.5;",
    }
    return base + configs.get(name, configs["generic_group"])


def _aws_service(name: str) -> str:
    """Build style for an AWS service icon."""
    name = _AWS_ALIASES.get(name, name)
    icon = _AWS_ICON_EXCEPTIONS.get(name, name)
    icon = _AWS_BROKEN_RESICONS.get(icon, icon)
    # Resource-level silhouettes use shape=mxgraph.aws4.<name> + strokeColor=none
    # (not resourceIcon). Confusing the two yields empty/broken glyphs.
    if icon in _AWS_RESOURCE_LEVEL_SHAPES:
        category = _aws_category(name)
        color = _AWS_COLORS[category]
        return (
            f"outlineConnect=0;dashed=0;html=1;sketch=0;aspect=fixed;"
            f"shape={AWS_LIB}.{icon};fillColor={color};strokeColor=none;{_ICON_LABEL}"
        )
    category = _aws_category(name)
    color = _AWS_COLORS[category]
    return (f"outlineConnect=0;dashed=0;html=1;"
            f"shape={AWS_LIB}.resourceIcon;resIcon={AWS_LIB}.{icon};"
            f"sketch=0;fillColor={color};strokeColor=#ffffff;{_ICON_LABEL}")


# Standalone AWS4 shapes (not resourceIcon frames) — strokeColor=none required.
_AWS_RESOURCE_LEVEL_SHAPES = frozenset({
    "application_load_balancer",
    "network_load_balancer",
    "endpoints",
    "peering",
    "client",
    "traditional_server",
    "internet_alt2",
    "users",
    "mobile_client",
})


def _gcp_container(name: str) -> str:
    """Build style for a GCP container (group) shape."""
    base = _GCP_AZURE_GROUP + "fillColor=none;"
    configs = {
        "cloud": "strokeColor=#4285F4;verticalAlign=top;align=left;spacingLeft=15;fontColor=#4285F4;dashed=0;strokeWidth=2;",
        "project": "strokeColor=#EA4335;verticalAlign=top;align=left;spacingLeft=15;fontColor=#EA4335;dashed=1;strokeWidth=2;",
        "vpc": "strokeColor=#4285F4;verticalAlign=top;align=left;spacingLeft=15;fontColor=#4285F4;dashed=0;strokeWidth=1.5;",
        "subnet": "strokeColor=#34A853;fillColor=#E8F5E9;verticalAlign=top;align=left;spacingLeft=15;fontColor=#2E7D32;dashed=0;strokeWidth=1.5;",
        "region": "strokeColor=#5F6368;verticalAlign=top;align=left;spacingLeft=15;fontColor=#5F6368;dashed=1;strokeWidth=1.5;",
        "zone": "strokeColor=#9AA0A6;verticalAlign=top;align=left;spacingLeft=15;fontColor=#9AA0A6;dashed=1;strokeWidth=1.5;",
        "generic_group": "strokeColor=#5F6368;fillColor=none;verticalAlign=top;align=left;spacingLeft=15;fontColor=#5F6368;dashed=1;strokeWidth=1.5;",
    }
    return base + configs.get(name, configs["generic_group"])


def _gcp_service(name: str) -> str:
    """Build style for a GCP service icon.

    GCP ships single-color stencils in draw.io (``mxgraph.gcp2``). We render the glyph
    in its brand color with ``sketch=0`` (crisp vector, not hand-drawn) and a soft
    shadow so the icons read as polished as the AWS resource icons.
    """
    name = _GCP_ALIASES.get(name, name)
    icon = _GCP_ICON_EXCEPTIONS.get(name, name)
    category = _gcp_category(name)
    color = _GCP_COLORS[category]
    return (
        f"sketch=0;outlineConnect=0;dashed=0;html=1;aspect=fixed;"
        f"shape={GCP_LIB}.{icon};"
        f"fillColor={color};strokeColor=none;{_ICON_LABEL}"
    )


def _azure_container(name: str) -> str:
    """Build style for an Azure container (group) shape."""
    base = _GCP_AZURE_GROUP + "fillColor=none;"
    configs = {
        "cloud": "strokeColor=#0078D4;verticalAlign=top;align=left;spacingLeft=15;fontColor=#0078D4;dashed=0;strokeWidth=2;",
        "subscription": "strokeColor=#00BCF2;verticalAlign=top;align=left;spacingLeft=15;fontColor=#00BCF2;dashed=1;strokeWidth=2;",
        "resource_group": "strokeColor=#7FBA00;verticalAlign=top;align=left;spacingLeft=15;fontColor=#7FBA00;dashed=0;strokeWidth=2;",
        "vnet": "strokeColor=#0078D4;verticalAlign=top;align=left;spacingLeft=15;fontColor=#0078D4;dashed=0;strokeWidth=1.5;",
        "subnet": "strokeColor=#5C2D91;fillColor=#F3EBF9;verticalAlign=top;align=left;spacingLeft=15;fontColor=#5C2D91;dashed=0;strokeWidth=1.5;",
        "generic_group": "strokeColor=#505050;fillColor=none;verticalAlign=top;align=left;spacingLeft=15;fontColor=#505050;dashed=1;strokeWidth=1.5;",
    }
    return base + configs.get(name, configs["generic_group"])


def _azure_service(name: str) -> str:
    """Build style for an Azure service icon."""
    name = _AZURE_ALIASES.get(name, name)
    icon = _AZURE_ICONS.get(name) or _AZURE_ICONS.get(_AZURE_ALIASES.get(name, ""))
    if icon is None:
        return _branded_card("#0078D4")
    category, filename = icon
    return (f"image;html=1;aspect=fixed;points=[];align=center;"
            f"image={AZURE_LIB}/{category}/{filename};"
            f"verticalLabelPosition=bottom;verticalAlign=top;{_ICON_LABEL}")


def _branded_card(color: str) -> str:
    """A clean, shadowed rounded card used as a graceful fallback for unmapped
    services so they read as intentional (matching the polish of icon shapes)
    rather than a flat default rectangle."""
    return (
        "rounded=1;arcSize=14;whiteSpace=wrap;html=1;"
        f"fillColor=#ffffff;strokeColor={color};strokeWidth=2;"
        f"fontSize=11;fontColor=#232F3E;fontStyle=1;align=center;verticalAlign=middle;"
    )


def _generic_fallback(name: str) -> str:
    """Fallback style for unrecognised shape types."""
    return ("rounded=1;arcSize=12;whiteSpace=wrap;html=1;fillColor=#dae8fc;"
            "strokeColor=#6c8ebf;strokeWidth=1.5;fontSize=12;fontColor=#232F3E;")


# Optional per-call style overrides (shape_type → full style string). Prefer passing
# ``overrides=`` into build_style / generate_xml — no module-global request state.


def set_style_overrides(mapping: dict[str, str]) -> None:
    """Deprecated no-op kept for older tests; pass overrides into build_style instead."""
    _ = mapping


def clear_style_overrides() -> None:
    """Deprecated no-op kept for older tests."""
    return


_KNWN_CONTAINER_NAMES: dict[str, str] = {
    "cloud": "aws_cloud", "account": "aws_account", "vpc": "aws_vpc",
    "region": "aws_region", "az": "aws_az", "availability_zone": "aws_az",
    "subnet": "aws_private_subnet", "public_subnet": "aws_public_subnet",
    "private_subnet": "aws_private_subnet", "security_group": "aws_security_group",
    "generic_group": "aws_generic_group", "auto_scaling_group": "aws_auto_scaling_group",
}


def build_style(shape_type: str, overrides: dict[str, str] | None = None) -> str:
    """Derive a draw.io style string from a shape_type key.

    Supports AWS (``aws_*``), GCP (``gcp_*``), Azure (``azure_*``),
    user/generic shapes, and falls back to a default rectangle for unknown types.
    """
    if not shape_type:
        return _generic_fallback("")

    shape_type = normalize_shape_type(shape_type)
    mapping = overrides or {}

    if shape_type in mapping:
        return mapping[shape_type]
    for key, style in mapping.items():
        if normalize_shape_type(key) == shape_type:
            return style

    generic = _generic_shapes(shape_type)
    if generic:
        return generic

    # Normalize unprefixed container names (LLM may emit "vpc"/"subnet" without aws_)
    if shape_type in _KNWN_CONTAINER_NAMES:
        shape_type = _KNWN_CONTAINER_NAMES[shape_type]

    if shape_type.startswith("aws_"):
        name = shape_type[4:]
        if name in _AWS_GROUP_ICONS:
            return _aws_container(name)
        return _aws_service(name)

    if shape_type.startswith("gcp_"):
        name = shape_type[4:]
        gcp_containers = {"cloud", "project", "vpc", "subnet", "region", "zone", "generic_group"}
        if name in gcp_containers:
            return _gcp_container(name)
        return _gcp_service(name)

    if shape_type.startswith("azure_"):
        name = shape_type[6:]
        azure_containers = {"cloud", "subscription", "resource_group", "vnet", "subnet", "generic_group"}
        if name in azure_containers:
            return _azure_container(name)
        return _azure_service(name)

    return _generic_fallback(shape_type)
