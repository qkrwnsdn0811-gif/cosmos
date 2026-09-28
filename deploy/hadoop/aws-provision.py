#!/usr/bin/env python3
"""Provision the COSMOS workers with AWS CLI. No AWS secrets reach the instances.

bootstrap-iam uses the existing account credential only to create a restricted
deployment identity. create uses that identity and never upgrades the account plan.
Runtime state and the generated SSH key remain outside the repository.
"""
import argparse
import configparser
import json
import os
from pathlib import Path
import subprocess
import tempfile

ACCOUNT = '853377042824'
REGION = 'ap-northeast-2'
VPC = 'vpc-0e650c3c7c22670a5'
VPC_CIDR = '172.31.0.0/16'
SUBNET = 'subnet-0eb1f6f509ed30c81'
AMI = 'ami-086a43496cb46286c'
PROFILE = 'cosmos-cluster-deployer'
KEY_NAME = 'cosmos-hadoop-workers-20260914'
TAGS = [{'Key': 'Project', 'Value': 'COSMOS'}, {'Key': 'Component', 'Value': 'hadoop-worker'}, {'Key': 'ManagedBy', 'Value': 'cosmos-infrastructure'}]
STATE = Path.home() / '.aws' / 'cosmos-cluster'
SSH_KEY = Path.home() / '.ssh' / KEY_NAME


def aws(service, operation, parameters=None, profile=PROFILE, region=REGION, allow_error=False):
    command = ['aws', '--profile', profile, '--region', region, '--no-cli-pager', service, operation, '--output', 'json']
    temporary = None
    if parameters is not None:
        handle = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.json', delete=False)
        json.dump(parameters, handle); handle.close(); temporary = handle.name
        command += ['--cli-input-json', 'file://' + temporary]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
        if result.returncode:
            if allow_error:
                return None, result.stderr
            raise RuntimeError(f'{service} {operation}: {result.stderr.strip()}')
        data=json.loads(result.stdout) if result.stdout.strip() else {}
        return (data, '') if allow_error else data
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def save(name, value):
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / name
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    return path


def policy():
    regional = {'StringEquals': {'aws:RequestedRegion': REGION}}
    owned = {'StringEquals': {'aws:RequestedRegion': REGION, 'ec2:ResourceTag/Project': 'COSMOS', 'ec2:ResourceTag/Component': 'hadoop-worker'}}
    arn = f'arn:aws:ec2:{REGION}:{ACCOUNT}:'
    return {'Version': '2012-10-17', 'Statement': [
        {'Sid': 'InspectRegionalCompute', 'Effect': 'Allow', 'Action': ['ec2:DescribeInstances','ec2:DescribeImages','ec2:DescribeInstanceTypes','ec2:DescribeInstanceTypeOfferings','ec2:DescribeSubnets','ec2:DescribeVpcs','ec2:DescribeRouteTables','ec2:DescribeSecurityGroups','ec2:DescribeKeyPairs','ec2:DescribeVolumes','ec2:DescribeInstanceStatus','ec2:DescribeTags','ec2:DescribeAvailabilityZones'], 'Resource': '*', 'Condition': regional},
        {'Sid': 'LaunchDependencies', 'Effect': 'Allow', 'Action': 'ec2:RunInstances', 'Resource': [f'arn:aws:ec2:{REGION}::image/{AMI}', arn+'subnet/'+SUBNET, arn+'network-interface/*', arn+'security-group/*', arn+'key-pair/'+KEY_NAME], 'Condition': regional},
        {'Sid': 'LaunchTaggedWorkersAndDisks', 'Effect': 'Allow', 'Action': 'ec2:RunInstances', 'Resource': [arn+'instance/*', arn+'volume/*'], 'Condition': {'StringEquals': {'aws:RequestedRegion': REGION,'aws:RequestTag/Project':'COSMOS','aws:RequestTag/Component':'hadoop-worker'}}},
        {'Sid': 'CreateWorkerNetworkBoundaryInApprovedVpc', 'Effect': 'Allow', 'Action': 'ec2:CreateSecurityGroup', 'Resource': arn+'vpc/'+VPC, 'Condition': regional},
        {'Sid': 'CreateTaggedWorkerNetworkBoundary', 'Effect': 'Allow', 'Action': 'ec2:CreateSecurityGroup', 'Resource': arn+'security-group/*', 'Condition': {'StringEquals': {'aws:RequestedRegion': REGION,'aws:RequestTag/Project':'COSMOS','aws:RequestTag/Component':'hadoop-worker'}}},
        {'Sid': 'ImportWorkerPublicKey', 'Effect': 'Allow', 'Action': 'ec2:ImportKeyPair', 'Resource': arn+'key-pair/'+KEY_NAME, 'Condition': {'StringEquals': {'aws:RequestedRegion': REGION,'aws:RequestTag/Project':'COSMOS','aws:RequestTag/Component':'hadoop-worker'}}},
        {'Sid': 'TagAtCreation', 'Effect': 'Allow', 'Action': 'ec2:CreateTags', 'Resource': [arn+'instance/*',arn+'volume/*',arn+'security-group/*',arn+'key-pair/*'], 'Condition': {'StringEquals': {'aws:RequestedRegion': REGION,'ec2:CreateAction':['RunInstances','CreateSecurityGroup','ImportKeyPair']}}},
        {'Sid': 'ManageOwnedWorkersOnly', 'Effect': 'Allow', 'Action': ['ec2:StartInstances','ec2:StopInstances','ec2:RebootInstances','ec2:TerminateInstances','ec2:ModifyInstanceAttribute','ec2:CreateTags','ec2:AuthorizeSecurityGroupIngress','ec2:RevokeSecurityGroupIngress','ec2:DeleteSecurityGroup','ec2:DeleteKeyPair'], 'Resource': [arn+'instance/*',arn+'security-group/*',arn+'key-pair/*'], 'Condition': owned},
    ]}


def bootstrap_iam():
    identity = aws('sts','get-caller-identity',profile='team-member')
    if identity['Account'] != ACCOUNT:
        raise RuntimeError('Unexpected AWS account')
    existing, error = aws('iam','get-user',{'UserName':PROFILE},profile='team-member',allow_error=True)
    if existing is None:
        if 'NoSuchEntity' not in error: raise RuntimeError(error)
        aws('iam','create-user',{'UserName':PROFILE,'Tags':TAGS},profile='team-member')
    else:
        tags = {t['Key']:t['Value'] for t in existing['User'].get('Tags',[])}
        if tags.get('Project') != 'COSMOS': raise RuntimeError('Existing IAM user is not owned by this project')
    policy_arn=f'arn:aws:iam::{ACCOUNT}:policy/COSMOSRegionalWorkers'
    existing_policy,error=aws('iam','get-policy',{'PolicyArn':policy_arn},profile='team-member',allow_error=True)
    if existing_policy is None:
        if 'NoSuchEntity' not in error: raise RuntimeError(error)
        aws('iam','create-policy',{'PolicyName':'COSMOSRegionalWorkers','PolicyDocument':json.dumps(policy(),separators=(',',':')),'Tags':TAGS},profile='team-member')
    else:
        version=aws('iam','get-policy-version',{'PolicyArn':policy_arn,'VersionId':existing_policy['Policy']['DefaultVersionId']},profile='team-member')['PolicyVersion']['Document']
        if version!=policy():
            aws('iam','create-policy-version',{'PolicyArn':policy_arn,'PolicyDocument':json.dumps(policy(),separators=(',',':')),'SetAsDefault':True},profile='team-member')
    aws('iam','attach-user-policy',{'UserName':PROFILE,'PolicyArn':policy_arn},profile='team-member')
    credentials_file = Path.home()/'.aws'/'credentials'
    credentials = configparser.RawConfigParser()
    credentials.read(credentials_file, encoding='utf-8')
    if not credentials.has_section(PROFILE):
        existing_keys = aws('iam','list-access-keys',{'UserName':PROFILE},profile='team-member')['AccessKeyMetadata']
        if existing_keys: raise RuntimeError('IAM key exists but local profile is absent; recover it before continuing')
        result = aws('iam','create-access-key',{'UserName':PROFILE},profile='team-member')['AccessKey']
        credentials.add_section(PROFILE)
        credentials.set(PROFILE,'aws_access_key_id',result['AccessKeyId'])
        credentials.set(PROFILE,'aws_secret_access_key',result['SecretAccessKey'])
        with credentials_file.open('w', encoding='utf-8') as f: credentials.write(f)
    identity = aws('sts','get-caller-identity')
    if identity['Account'] != ACCOUNT or not identity['Arn'].endswith('/'+PROFILE): raise RuntimeError('Wrong deployment identity')
    save('iam-policy.json',policy())
    print('Restricted deployment identity ready:', identity['Arn'])


def create_workers(management_cidrs, tailscale_peer_cidrs):
    import base64
    import ipaddress
    for cidr in management_cidrs:
        if ipaddress.ip_network(cidr).prefixlen != 32: raise ValueError('SSH bootstrap sources must be IPv4 /32')
    for cidr in tailscale_peer_cidrs:
        network = ipaddress.ip_network(cidr, strict=True)
        if network.version != 4 or network.prefixlen != 32:
            raise ValueError('Remote Tailscale direct peers must use explicit IPv4 /32 addresses')
    identity = aws('sts','get-caller-identity')
    if identity['Account'] != ACCOUNT or identity['Arn'].endswith(':root'): raise RuntimeError('Use restricted deployment identity')
    SSH_KEY.parent.mkdir(parents=True, exist_ok=True)
    if not SSH_KEY.exists():
        subprocess.run(['ssh-keygen','-t','ed25519','-f',str(SSH_KEY),'-N','','-C','cosmos-hadoop-workers'],capture_output=True,check=True)
    keys=aws('ec2','describe-key-pairs',{'Filters':[{'Name':'key-name','Values':[KEY_NAME]}]})['KeyPairs']
    if not keys:
        public_key=Path(str(SSH_KEY)+'.pub').read_bytes()
        aws('ec2','import-key-pair',{'KeyName':KEY_NAME,'PublicKeyMaterial':base64.b64encode(public_key).decode(),'TagSpecifications':[{'ResourceType':'key-pair','Tags':TAGS}]})
    groups=aws('ec2','describe-security-groups',{'Filters':[{'Name':'vpc-id','Values':[VPC]},{'Name':'group-name','Values':['cosmos-hadoop-workers']}]})['SecurityGroups']
    if groups:
        group=groups[0]
        if {t['Key']:t['Value'] for t in group.get('Tags',[])}.get('Project')!='COSMOS': raise RuntimeError('Unowned security group')
        sg=group['GroupId']
    else:
        sg=aws('ec2','create-security-group',{'GroupName':'cosmos-hadoop-workers','Description':'COSMOS workers: restricted SSH and encrypted Tailscale transport only','VpcId':VPC,'TagSpecifications':[{'ResourceType':'security-group','Tags':TAGS}]})['GroupId']
    udp_sources = [VPC_CIDR, *tailscale_peer_cidrs]
    permissions=[{'IpProtocol':'tcp','FromPort':22,'ToPort':22,'IpRanges':[{'CidrIp':c,'Description':'COSMOS bootstrap management'} for c in management_cidrs]}, {'IpProtocol':'udp','FromPort':41641,'ToPort':41641,'IpRanges':[{'CidrIp':c,'Description':'Encrypted Tailscale direct transport'} for c in udp_sources]}]
    for permission in permissions:
        _,error=aws('ec2','authorize-security-group-ingress',{'GroupId':sg,'IpPermissions':[permission]},allow_error=True)
        if error and 'InvalidPermission.Duplicate' not in error: raise RuntimeError(error)
    # A first bootstrap may have used a broad transport rule. Remove it only after
    # the exact VPC and remote-peer rules above are confirmed by AWS.
    _, error = aws('ec2', 'revoke-security-group-ingress', {
        'GroupId': sg,
        'IpPermissions': [{'IpProtocol':'udp','FromPort':41641,'ToPort':41641,
                           'IpRanges':[{'CidrIp':'0.0.0.0/0'}]}]}, allow_error=True)
    if error and 'InvalidPermission.NotFound' not in error:
        raise RuntimeError(error)
    save('network.json',{'account':ACCOUNT,'region':REGION,'vpc':VPC,'subnet':SUBNET,'security_group':sg,'management_cidrs':management_cidrs,'tailscale_peer_cidrs':tailscale_peer_cidrs,'key_path':str(SSH_KEY)})
    inventory=[]
    for index in range(1,4):
        name=f'cosmos-worker-{index}'
        found=aws('ec2','describe-instances',{'Filters':[{'Name':'tag:Name','Values':[name]},{'Name':'tag:Project','Values':['COSMOS']},{'Name':'instance-state-name','Values':['pending','running','stopping','stopped']}]})['Reservations']
        instances=[i for r in found for i in r['Instances']]
        if len(instances)>1: raise RuntimeError('Duplicate worker name: '+name)
        if instances:
            instance=instances[0]
        else:
            tags=TAGS+[{'Key':'Name','Value':name}]
            request={'ImageId':AMI,'InstanceType':'m7i-flex.large','MinCount':1,'MaxCount':1,'KeyName':KEY_NAME,'ClientToken':f'cosmos-hadoop-20260914-worker-{index}', 'NetworkInterfaces':[{'DeviceIndex':0,'SubnetId':SUBNET,'Groups':[sg],'AssociatePublicIpAddress':True,'DeleteOnTermination':True}], 'MetadataOptions':{'HttpTokens':'required','HttpEndpoint':'enabled','HttpPutResponseHopLimit':1},'BlockDeviceMappings':[{'DeviceName':'/dev/sda1','Ebs':{'VolumeSize':30,'VolumeType':'gp3','Encrypted':True,'DeleteOnTermination':True}},{'DeviceName':'/dev/sdf','Ebs':{'VolumeSize':200,'VolumeType':'gp3','Encrypted':True,'DeleteOnTermination':False}}],'TagSpecifications':[{'ResourceType':'instance','Tags':tags},{'ResourceType':'volume','Tags':tags}]}
            save(name+'-request.json',request)
            instance=aws('ec2','run-instances',request)['Instances'][0]
        inventory.append({'name':name,'instance_id':instance['InstanceId'],'state':instance['State']['Name']})
        save('workers.json',inventory)
        print(json.dumps(inventory[-1]))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['bootstrap-iam','create','policy'])
    parser.add_argument('--management-cidr',action='append',default=[])
    parser.add_argument('--tailscale-peer-cidr',action='append',default=[],
                        help='Remote Master public IPv4 /32 allowed to form a direct Tailscale path')
    args=parser.parse_args()
    if args.action=='bootstrap-iam': bootstrap_iam()
    elif args.action=='create':
        if not args.management_cidr:parser.error('At least one --management-cidr required')
        if not args.tailscale_peer_cidr:parser.error('At least one --tailscale-peer-cidr required')
        create_workers(args.management_cidr, args.tailscale_peer_cidr)
    else: print(json.dumps(policy(),indent=2))
