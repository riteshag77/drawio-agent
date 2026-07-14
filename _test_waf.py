import json, sys
sys.path.insert(0, '.')
from app.adapters.outbound.well_architected import assess_well_architected
xml = '<root><mxCell id="2" style="shape_type=aws_ec2"/><mxCell id="3" style="shape_type=aws_rds"/><mxCell id="4" style="shape_type=aws_lambda"/><mxCell id="5" style="shape_type=aws_waf"/><mxCell id="6" style="shape_type=aws_cloudwatch"/></root>'
r = assess_well_architected(xml, 'aws')
print(json.dumps(r, indent=2))
