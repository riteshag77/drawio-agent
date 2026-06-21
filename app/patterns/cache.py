"""Instant pre-baked diagram responses — port of next-ai-draw-io lib/cached-responses.ts"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CachedResponse:
    prompt_text: str
    has_image: bool
    xml: str


_AWS_HA_WEB_APP = """
<mxCell id="cloud" value="AWS Cloud" style="swimlane;startSize=25;fillColor=#F2F2F2;strokeColor=#CCCCCC;rounded=1;fontSize=12;fontStyle=1;" vertex="1" parent="1">
  <mxGeometry x="30" y="30" width="900" height="480" as="geometry"/>
</mxCell>
<mxCell id="users" value="Users" style="shape=mxgraph.aws4.client;fillColor=#232F3D;strokeColor=#ffffff;verticalLabelPosition=bottom;verticalAlign=top;align=center;fontSize=10;" vertex="1" parent="cloud">
  <mxGeometry x="40" y="60" width="55" height="55" as="geometry"/>
</mxCell>
<mxCell id="route53" value="Route 53" style="shape=mxgraph.aws4.route_53;fillColor=#8C4FFF;strokeColor=#ffffff;verticalLabelPosition=bottom;verticalAlign=top;align=center;fontSize=9;" vertex="1" parent="cloud">
  <mxGeometry x="130" y="60" width="55" height="55" as="geometry"/>
</mxCell>
<mxCell id="cloudfront" value="CloudFront" style="shape=mxgraph.aws4.cloudfront;fillColor=#8C4FFF;strokeColor=#ffffff;verticalLabelPosition=bottom;verticalAlign=top;align=center;fontSize=8;" vertex="1" parent="cloud">
  <mxGeometry x="220" y="60" width="55" height="55" as="geometry"/>
</mxCell>
<mxCell id="waf" value="WAF" style="shape=mxgraph.aws4.waf;fillColor=#DD344C;strokeColor=#ffffff;verticalLabelPosition=bottom;verticalAlign=top;align=center;fontSize=9;" vertex="1" parent="cloud">
  <mxGeometry x="310" y="60" width="55" height="55" as="geometry"/>
</mxCell>
<mxCell id="apigw" value="API Gateway" style="shape=mxgraph.aws4.api_gateway;fillColor=#C925D1;strokeColor=#ffffff;verticalLabelPosition=bottom;verticalAlign=top;align=center;fontSize=8;" vertex="1" parent="cloud">
  <mxGeometry x="400" y="60" width="55" height="55" as="geometry"/>
</mxCell>
<mxCell id="vpc" value="VPC" style="swimlane;startSize=20;fillColor=#EDF9FC;strokeColor=#226396;rounded=1;fontSize=12;fontStyle=1;" vertex="1" parent="cloud">
  <mxGeometry x="40" y="150" width="820" height="280" as="geometry"/>
</mxCell>
<mxCell id="pub_subnet" value="Public Subnet" style="swimlane;startSize=20;fillColor=#E8F5E9;strokeColor=#388E3C;rounded=1;fontSize=10;fontStyle=1;" vertex="1" parent="vpc">
  <mxGeometry x="20" y="40" width="360" height="200" as="geometry"/>
</mxCell>
<mxCell id="alb" value="ALB" style="shape=mxgraph.aws4.application_load_balancer;fillColor=#ED7100;strokeColor=#ffffff;verticalLabelPosition=bottom;verticalAlign=top;align=center;fontSize=8;" vertex="1" parent="pub_subnet">
  <mxGeometry x="40" y="60" width="55" height="55" as="geometry"/>
</mxCell>
<mxCell id="priv_subnet" value="Private Subnet" style="swimlane;startSize=20;fillColor=#FFF3E0;strokeColor=#E65100;rounded=1;fontSize=10;fontStyle=1;" vertex="1" parent="vpc">
  <mxGeometry x="420" y="40" width="360" height="200" as="geometry"/>
</mxCell>
<mxCell id="fargate" value="Fargate" style="shape=mxgraph.aws4.ecs;fillColor=#ED7100;strokeColor=#ffffff;verticalLabelPosition=bottom;verticalAlign=top;align=center;fontSize=8;" vertex="1" parent="priv_subnet">
  <mxGeometry x="40" y="60" width="55" height="55" as="geometry"/>
</mxCell>
<mxCell id="aurora" value="Aurora" style="shape=mxgraph.aws4.aurora;fillColor=#8C4FFF;strokeColor=#ffffff;verticalLabelPosition=bottom;verticalAlign=top;align=center;fontSize=8;" vertex="1" parent="priv_subnet">
  <mxGeometry x="140" y="60" width="55" height="55" as="geometry"/>
</mxCell>
<mxCell id="redis" value="ElastiCache" style="shape=mxgraph.aws4.elasticache_for_redis;fillColor=#ED7100;strokeColor=#ffffff;verticalLabelPosition=bottom;verticalAlign=top;align=center;fontSize=8;" vertex="1" parent="priv_subnet">
  <mxGeometry x="240" y="60" width="55" height="55" as="geometry"/>
</mxCell>
<mxCell id="cloudwatch" value="CloudWatch" style="shape=mxgraph.aws4.cloudwatch;fillColor=#DD344C;strokeColor=#ffffff;verticalLabelPosition=bottom;verticalAlign=top;align=center;fontSize=8;" vertex="1" parent="cloud">
  <mxGeometry x="520" y="60" width="55" height="55" as="geometry"/>
</mxCell>
<mxCell id="e1" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=classic;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="cloud" source="users" target="route53" value="">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
<mxCell id="e2" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=classic;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="cloud" source="route53" target="cloudfront" value="">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
<mxCell id="e3" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=classic;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="cloud" source="cloudfront" target="waf" value="">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
<mxCell id="e4" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=classic;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="cloud" source="waf" target="apigw" value="">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
<mxCell id="e5" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=classic;exitX=0.5;exitY=1;entryX=0;entryY=0.5;" edge="1" parent="vpc" source="alb" target="fargate" value="">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
<mxCell id="e6" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=classic;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="priv_subnet" source="fargate" target="aurora" value="">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
<mxCell id="e7" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=classic;dashed=1;exitX=0;exitY=0.5;entryX=1;entryY=0.5;" edge="1" parent="cloud" source="cloudwatch" target="fargate" value="">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
"""

CACHED_EXAMPLE_RESPONSES: list[CachedResponse] = [
    CachedResponse(
        prompt_text=(
            "Highly available web app with API Gateway, Fargate in public/private subnets, "
            "Aurora, ElastiCache, CloudFront, Route 53, WAF, and CloudWatch"
        ),
        has_image=False,
        xml=_AWS_HA_WEB_APP.strip(),
    ),
]


def find_cached_response(prompt_text: str, has_image: bool = False) -> CachedResponse | None:
    normalized = (prompt_text or "").strip()
    for entry in CACHED_EXAMPLE_RESPONSES:
        if entry.prompt_text.strip() == normalized and entry.has_image == has_image and entry.xml:
            return entry
    return None
