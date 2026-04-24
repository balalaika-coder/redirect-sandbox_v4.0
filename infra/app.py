import os
import aws_cdk as cdk
from stacks.core_stack import CoreStack
from stacks.edge_stack import EdgeStack
from stacks.sync_stack import SyncStack

app = cdk.App()
env = cdk.Environment(
    account=os.getenv('CDK_DEFAULT_ACCOUNT'),
    region='us-east-1'
)

core = CoreStack(app, 'redirect-core', env=env)
edge = EdgeStack(app, 'redirect-edge', env=env)
sync = SyncStack(
    app, 'redirect-sync',
    env=env,
    vpc=core.vpc,
    ec2_private_ip=core.ec2_private_ip,
    dynamodb_table_name=edge.table_name,
)

app.synth()
