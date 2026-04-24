import json
import os
import hmac
import hashlib
import urllib3
import boto3
from typing import Dict, List, Any, Optional


dynamodb = boto3.resource('dynamodb')
ssm = boto3.client('ssm')
table_name = os.environ['DYNAMODB_TABLE']
table = dynamodb.Table(table_name)


def get_parameter(name: str) -> str:
    """Retrieve SSM parameter value."""
    response = ssm.get_parameter(Name=name, WithDecryption=True)
    return response['Parameter']['Value']


def verify_webhook_signature(body: str, signature: str, secret: str) -> bool:
    """Verify HMAC SHA-256 signature."""
    expected = hmac.new(
        secret.encode('utf-8'),
        body.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def fetch_redirects(base_url: str, slug: str, token: str) -> List[Dict[str, Any]]:
    """Fetch all redirects for a site from Django API."""
    http = urllib3.PoolManager()
    url = f'{base_url}/api/v1/sites/{slug}/redirects/'
    headers = {'Authorization': f'Token {token}'}
    
    response = http.request('GET', url, headers=headers)
    if response.status != 200:
        raise Exception(f'API request failed: {response.status} {response.data.decode()}')
    
    data = json.loads(response.data.decode('utf-8'))
    return data.get('redirects', [])


def build_dynamodb_items(slug: str, redirects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build DynamoDB items from redirect data."""
    items = []
    geo_config = None
    regex_rules = []
    
    for redirect in redirects:
        source = redirect['source'].rstrip('/')
        destination = redirect['destination']
        status_code = redirect['status_code']
        match_type = redirect['match_type']
        geo_aware = redirect.get('geo_aware', False)
        geo_locale_allowlist = redirect.get('geo_locale_allowlist')
        locale_overrides = redirect.get('locale_overrides', {})
        utm_params = redirect.get('utm_params', {})
        
        if match_type == 'regex':
            # Regex redirects go into __REGEX__ item
            regex_rules.append({
                'pattern': source,
                'destination': destination,
                'status_code': status_code,
            })
        else:
            # Exact or vanity match
            item = {
                'pk': slug,
                'sk': source,
                'destination': destination,
                'status_code': status_code,
            }
            
            if geo_aware:
                item['geo_aware'] = True
                if geo_locale_allowlist:
                    item['geo_locale_allowlist'] = geo_locale_allowlist
            
            if locale_overrides:
                item['locale_overrides'] = locale_overrides
            
            if utm_params:
                item['utm_params'] = utm_params
            
            items.append(item)
    
    # Add __REGEX__ item if there are regex rules
    if regex_rules:
        items.append({
            'pk': slug,
            'sk': '__REGEX__',
            'rules': regex_rules,
        })
    
    # __GEO_CONFIG__ is fetched from the site endpoint
    # For this sandbox, we'll hardcode it
    items.append({
        'pk': slug,
        'sk': '__GEO_CONFIG__',
        'default_locale': 'en-us',
        'locale_map': {
            'US': 'en-us',
            'CA': 'en-us',
            'DE': 'de',
            'AT': 'de',
            'CH': 'de',
        },
    })
    
    return items


def get_current_items(slug: str) -> List[Dict[str, Any]]:
    """Scan DynamoDB for all current items with this pk."""
    response = table.query(
        KeyConditionExpression='pk = :pk',
        ExpressionAttributeValues={':pk': slug},
    )
    return response.get('Items', [])


def compute_diff(current: List[Dict], desired: List[Dict]) -> tuple:
    """Compute items to write and delete."""
    current_map = {item['sk']: item for item in current}
    desired_map = {item['sk']: item for item in desired}
    
    to_write = []
    to_delete = []
    
    # Items to add or update
    for sk, item in desired_map.items():
        if sk not in current_map or current_map[sk] != item:
            to_write.append(item)
    
    # Items to delete
    for sk in current_map:
        if sk not in desired_map:
            to_delete.append({'pk': current_map[sk]['pk'], 'sk': sk})
    
    return to_write, to_delete


def batch_write(to_write: List[Dict], to_delete: List[Dict]) -> None:
    """Write changes to DynamoDB in batches of 25."""
    operations = []
    
    for item in to_write:
        operations.append({'PutRequest': {'Item': item}})
    
    for item in to_delete:
        operations.append({'DeleteRequest': {'Key': item}})
    
    # Process in batches of 25
    for i in range(0, len(operations), 25):
        batch = operations[i:i+25]
        dynamodb.batch_write_item(RequestItems={table_name: batch})


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main Lambda handler."""
    print(f'Event: {json.dumps(event)}')
    
    # Determine source: API Gateway webhook or EventBridge
    if 'source' in event and event['source'] == 'eventbridge':
        # EventBridge scheduled sync
        slug = event.get('siteSlug', 'sandbox')
    elif 'pathParameters' in event:
        # API Gateway webhook
        slug = event['pathParameters']['slug']
        
        # Verify webhook signature
        body = event.get('body', '')
        signature = event.get('headers', {}).get('X-Webhook-Signature', '')
        webhook_secret = get_parameter(os.environ['WEBHOOK_SECRET_PARAM'])
        
        if not verify_webhook_signature(body, signature, webhook_secret):
            return {
                'statusCode': 401,
                'body': json.dumps({'error': 'Invalid signature'}),
            }
    else:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid event source'}),
        }
    
    # Fetch API credentials
    api_base_url = os.environ['API_BASE_URL']
    api_token = get_parameter(os.environ['API_TOKEN_PARAM'])
    
    # Fetch redirects from Django
    redirects = fetch_redirects(api_base_url, slug, api_token)
    print(f'Fetched {len(redirects)} redirects')
    
    # Build desired DynamoDB state
    desired_items = build_dynamodb_items(slug, redirects)
    
    # Get current DynamoDB state
    current_items = get_current_items(slug)
    
    # Compute diff
    to_write, to_delete = compute_diff(current_items, desired_items)
    print(f'To write: {len(to_write)}, To delete: {len(to_delete)}')
    
    # Apply changes
    batch_write(to_write, to_delete)
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Sync completed',
            'slug': slug,
            'redirects_fetched': len(redirects),
            'items_written': len(to_write),
            'items_deleted': len(to_delete),
        }),
    }
