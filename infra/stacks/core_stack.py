from aws_cdk import (
    Stack,
    CfnOutput,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_ssm as ssm,
)
from constructs import Construct


class CoreStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        my_ip = self.node.try_get_context('my_ip')
        if not my_ip:
            raise ValueError(
                'Context variable "my_ip" is required (format: "1.2.3.4/32")')

        # VPC with public subnets only (no NAT gateway for cost savings)
        self.vpc = ec2.Vpc(
            self, 'VPC',
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name='Public',
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                )
            ],
        )

        # DynamoDB VPC Gateway Endpoint (free, allows Lambda in VPC to reach DynamoDB)
        self.vpc.add_gateway_endpoint(
            'DynamoDBEndpoint',
            service=ec2.GatewayVpcEndpointAwsService.DYNAMODB,
        )

        # Security Group for EC2
        ec2_sg = ec2.SecurityGroup(
            self, 'EC2SecurityGroup',
            vpc=self.vpc,
            description='Allow SSH and HTTP from my IP, and HTTP from VPC CIDR',
            allow_all_outbound=True,
        )
        ec2_sg.add_ingress_rule(
            peer=ec2.Peer.ipv4(my_ip),
            connection=ec2.Port.tcp(22),
            description='SSH from my IP',
        )
        ec2_sg.add_ingress_rule(
            peer=ec2.Peer.ipv4(my_ip),
            connection=ec2.Port.tcp(80),
            description='HTTP from my IP',
        )
        ec2_sg.add_ingress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(80),
            description='HTTP from VPC (for Lambda sync)',
        )

        # Security Group for RDS
        rds_sg = ec2.SecurityGroup(
            self, 'RDSSecurityGroup',
            vpc=self.vpc,
            description='Allow PostgreSQL from EC2 only',
            allow_all_outbound=False,
        )
        rds_sg.add_ingress_rule(
            peer=ec2_sg,
            connection=ec2.Port.tcp(5432),
            description='PostgreSQL from EC2',
        )

        # RDS PostgreSQL Instance
        db_instance = rds.DatabaseInstance(
            self, 'PostgresDB',
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE3,
                ec2.InstanceSize.MICRO,
            ),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[rds_sg],
            publicly_accessible=False,
            database_name='redirectdb',
            credentials=rds.Credentials.from_generated_secret('dbadmin'),
            allocated_storage=20,
            max_allocated_storage=30,
            removal_policy=RemovalPolicy.DESTROY,
            deletion_protection=False,
        )

        # EC2 Key Pair
        key_pair = ec2.KeyPair(
            self, 'KeyPair',
            key_pair_name='redirect-sandbox-keypair',
        )

        # EC2 Instance
        ubuntu_ami = ec2.MachineImage.from_ssm_parameter(
            '/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id',
            os=ec2.OperatingSystemType.LINUX,
        )

        instance = ec2.Instance(
            self, 'DjangoInstance',
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE3,
                ec2.InstanceSize.SMALL,
            ),
            machine_image=ubuntu_ami,
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=ec2_sg,
            key_pair=key_pair,
            user_data=ec2.UserData.custom('''#!/bin/bash
set -e
apt-get update
apt-get install -y python3 python3-venv python3-pip git postgresql-client
'''),
        )

        # Elastic IP for stable EC2 address
        eip = ec2.CfnEIP(self, 'EIP', instance_id=instance.instance_id)

        # Store the private IP for use by sync Lambda
        self.ec2_private_ip = instance.instance_private_ip

        # Store RDS connection info in SSM for Ansible
        ssm.StringParameter(
            self, 'RDSEndpointParam',
            parameter_name='/redirect-sandbox/rds/endpoint',
            string_value=db_instance.db_instance_endpoint_address,
        )
        ssm.StringParameter(
            self, 'RDSPortParam',
            parameter_name='/redirect-sandbox/rds/port',
            string_value=db_instance.db_instance_endpoint_port,
        )
        ssm.StringParameter(
            self, 'RDSDBNameParam',
            parameter_name='/redirect-sandbox/rds/dbname',
            string_value='redirectdb',
        )
        ssm.StringParameter(
            self, 'RDSUserParam',
            parameter_name='/redirect-sandbox/rds/user',
            string_value='dbadmin',
        )

        # Outputs
        CfnOutput(self, 'EC2PublicIP', value=eip.attr_public_ip)
        CfnOutput(self, 'EC2PrivateIP', value=instance.instance_private_ip)
        CfnOutput(self, 'RDSEndpoint',
                  value=db_instance.db_instance_endpoint_address)
        CfnOutput(self, 'RDSPort', value=db_instance.db_instance_endpoint_port)
        CfnOutput(self, 'RDSSecretArn', value=db_instance.secret.secret_arn)
        CfnOutput(self, 'KeyPairId', value=key_pair.key_pair_id)
        CfnOutput(
            self, 'SSHKeyCommand',
            value=f'aws ssm get-parameter --name /ec2/keypair/{key_pair.key_pair_id} --with-decryption --query Parameter.Value --output text > ~/redirect-sandbox-key.pem && chmod 400 ~/redirect-sandbox-key.pem'
        )
        CfnOutput(
            self, 'RDSPasswordCommand',
            value=f'aws secretsmanager get-secret-value --secret-id {db_instance.secret.secret_arn} --query SecretString --output text | python3 -c "import sys,json; print(json.load(sys.stdin)[\'password\'])"'
        )
