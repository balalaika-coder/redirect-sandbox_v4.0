from aws_cdk import (
    Stack,
    CfnOutput,
    Duration,
    aws_lambda as _lambda,
    aws_ec2 as ec2,
    aws_apigateway as apigw,
    aws_events as events,
    aws_events_targets as targets,
    aws_ssm as ssm,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class SyncStack(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        vpc: ec2.IVpc,
        ec2_private_ip: str,
        dynamodb_table_name: str,
        **kwargs
    ) -> None:
        super().__init__(scope, id, **kwargs)

        # Reference the DynamoDB table
        table = dynamodb.Table.from_table_name(
            self, 'RedirectRulesTable',
            table_name=dynamodb_table_name,
        )

        # Webhook secret stored in SSM
        webhook_secret_param = ssm.StringParameter(
            self, 'WebhookSecretParam',
            parameter_name='/redirect-sandbox/webhook-secret',
            string_value='sandbox-webhook-secret-change-in-production',
        )

        # API token for Django stored in SSM
        api_token_param = ssm.StringParameter(
            self, 'ApiTokenParam',
            parameter_name='/redirect-sandbox/api-token',
            string_value='sandbox-api-token-change-in-production',
        )

        # Sync Lambda Function (in VPC to reach EC2)
        sync_fn = _lambda.Function(
            self, 'SyncDynamoDBFunction',
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler='handler.lambda_handler',
            code=_lambda.Code.from_asset('lambdas/sync_dynamodb'),
            memory_size=256,
            timeout=Duration.seconds(120),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            allow_public_subnet=True,
            environment={
                'DYNAMODB_TABLE': dynamodb_table_name,
                'API_BASE_URL': f'http://{ec2_private_ip}',
                'API_TOKEN_PARAM': api_token_param.parameter_name,
                'WEBHOOK_SECRET_PARAM': webhook_secret_param.parameter_name,
            },
        )

        # Grant permissions
        table.grant_read_write_data(sync_fn)
        webhook_secret_param.grant_read(sync_fn)
        api_token_param.grant_read(sync_fn)

        # API Gateway REST API
        api = apigw.RestApi(
            self, 'SyncAPI',
            rest_api_name='redirect-sync-api',
            description='Webhook endpoint for Django to trigger DynamoDB sync',
        )

        # POST /sync/{slug}
        sync_resource = api.root.add_resource('sync')
        slug_resource = sync_resource.add_resource('{slug}')

        slug_resource.add_method(
            'POST',
            apigw.LambdaIntegration(sync_fn),
        )

        # EventBridge Rule (every 6 hours)
        rule = events.Rule(
            self, 'SyncScheduleRule',
            schedule=events.Schedule.rate(Duration.hours(6)),
        )

        rule.add_target(
            targets.LambdaFunction(
                sync_fn,
                event=events.RuleTargetInput.from_object({
                    'source': 'eventbridge',
                    'siteSlug': 'sandbox',
                }),
            )
        )

        # Store API Gateway URL in SSM for Django
        ssm.StringParameter(
            self, 'ApiGatewayUrlParam',
            parameter_name='/redirect-sandbox/sync-api-url',
            string_value=api.url,
        )

        # Outputs
        CfnOutput(self, 'ApiGatewayUrl', value=api.url)
        CfnOutput(
            self, 'SyncWebhookEndpoint',
            value=f'{api.url}sync/sandbox',
        )
