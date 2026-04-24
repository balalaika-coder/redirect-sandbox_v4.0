from aws_cdk import (
    Stack,
    CfnOutput,
    RemovalPolicy,
    Duration,
    aws_dynamodb as dynamodb,
    aws_lambda as _lambda,
    aws_s3 as s3,
    aws_s3_deployment as s3_deployment,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
)
from constructs import Construct


class EdgeStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # DynamoDB Table
        table = dynamodb.Table(
            self, 'RedirectRulesTable',
            table_name='redirect-rules',
            partition_key=dynamodb.Attribute(
                name='pk',
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name='sk',
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.table_name = table.table_name

        # Lambda@Edge Function
        edge_fn = _lambda.Function(
            self, 'EdgeRedirectFunction',
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler='handler.lambda_handler',
            code=_lambda.Code.from_asset('lambdas/edge_redirect'),
            memory_size=128,
            timeout=Duration.seconds(5),
        )

        # Grant DynamoDB read permissions
        table.grant_read_data(edge_fn)

        # Get current version for Lambda@Edge
        edge_version = edge_fn.current_version

        # S3 Bucket for test pages
        test_bucket = s3.Bucket(
            self, 'TestPagesBucket',
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # Deploy test pages to S3
        s3_deployment.BucketDeployment(
            self, 'DeployTestPages',
            sources=[s3_deployment.Source.asset('test_pages')],
            destination_bucket=test_bucket,
        )

        # CloudFront Origin Access Identity
        oai = cloudfront.OriginAccessIdentity(
            self, 'OAI',
            comment='OAI for redirect test pages',
        )
        test_bucket.grant_read(oai)

        # CloudFront Distribution
        distribution = cloudfront.Distribution(
            self, 'TestDistribution',
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_identity(test_bucket, origin_access_identity=oai),
                edge_lambdas=[
                    cloudfront.EdgeLambda(
                        event_type=cloudfront.LambdaEdgeEventType.VIEWER_REQUEST,
                        function_version=edge_version,
                    )
                ],
            ),
            default_root_object='index.html',
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=404,
                    response_page_path='/404.html',
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_page_path='/404.html',
                ),
            ],
        )

        # Outputs
        CfnOutput(self, 'CloudFrontDomain',
                  value=distribution.distribution_domain_name)
        CfnOutput(self, 'CloudFrontURL',
                  value=f'https://{distribution.distribution_domain_name}')
        CfnOutput(self, 'DynamoDBTableName', value=table.table_name)
        CfnOutput(self, 'S3BucketName', value=test_bucket.bucket_name)
