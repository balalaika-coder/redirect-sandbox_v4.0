import json
import re
import boto3
from typing import Dict, Any, Optional
from urllib.parse import urlencode, parse_qs, urlparse


dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('redirect-rules')
SLUG = 'sandbox'


def get_item(sk: str) -> Optional[Dict[str, Any]]:
    """Get item from DynamoDB."""
    response = table.get_item(Key={'pk': SLUG, 'sk': sk})
    return response.get('Item')


def get_viewer_country(headers: Dict[str, Any]) -> str:
    """Extract viewer country from CloudFront headers."""
    country_header = headers.get('cloudfront-viewer-country', [{}])[0]
    return country_header.get('value', 'US').upper()


def resolve_locale(country: str) -> str:
    """Resolve locale from country code using __GEO_CONFIG__."""
    geo_config = get_item('__GEO_CONFIG__')
    if not geo_config:
        return 'en-us'
    
    locale_map = geo_config.get('locale_map', {})
    default_locale = geo_config.get('default_locale', 'en-us')
    
    return locale_map.get(country, default_locale)


def check_regex_match(uri: str) -> Optional[Dict[str, Any]]:
    """Check if URI matches any regex patterns."""
    regex_item = get_item('__REGEX__')
    if not regex_item:
        return None
    
    rules = regex_item.get('rules', [])
    for rule in rules:
        pattern = rule['pattern']
        if re.match(pattern, uri):
            return {
                'destination': rule['destination'],
                'status_code': rule['status_code'],
            }
    
    return None


def build_redirect_response(status_code: int, location: str) -> Dict[str, Any]:
    """Build CloudFront redirect response."""
    return {
        'status': str(status_code),
        'statusDescription': 'Found' if status_code == 302 else 'Moved Permanently',
        'headers': {
            'location': [{'key': 'Location', 'value': location}],
            'cache-control': [{'key': 'Cache-Control', 'value': 'no-cache, no-store, must-revalidate'}],
        },
    }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda@Edge handler for viewer-request."""
    request = event['Records'][0]['cf']['request']
    uri = request['uri'].rstrip('/')
    if not uri:
        uri = '/'
    
    headers = request['headers']
    query_string = request.get('querystring', '')
    
    # Step 1: Exact match lookup
    item = get_item(uri)
    
    if item:
        destination = item['destination']
        status_code = item.get('status_code', 301)
        geo_aware = item.get('geo_aware', False)
        
        # Step 2: Geo-aware processing
        if geo_aware:
            viewer_country = get_viewer_country(headers)
            locale = resolve_locale(viewer_country)
            
            # Check allowlist
            allowlist = item.get('geo_locale_allowlist')
            if allowlist and locale not in allowlist:
                # Fallback to default locale
                geo_config = get_item('__GEO_CONFIG__')
                locale = geo_config.get('default_locale', 'en-us') if geo_config else 'en-us'
            
            # Check for locale override
            locale_overrides = item.get('locale_overrides', {})
            if locale in locale_overrides:
                destination = locale_overrides[locale]
        
        # Step 3: UTM parameters
        utm_params = item.get('utm_params', {})
        if utm_params:
            parsed = urlparse(destination)
            existing_qs = parse_qs(parsed.query)
            merged_params = {**utm_params, **existing_qs}
            destination = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(merged_params, doseq=True)}"
        
        # Step 4: Preserve query string
        if query_string and '?' not in destination:
            destination = f"{destination}?{query_string}"
        elif query_string:
            destination = f"{destination}&{query_string}"
        
        return build_redirect_response(status_code, destination)
    
    # Step 5: Regex fallback
    regex_match = check_regex_match(uri)
    if regex_match:
        destination = regex_match['destination']
        status_code = regex_match['status_code']
        
        # Preserve query string
        if query_string and '?' not in destination:
            destination = f"{destination}?{query_string}"
        elif query_string:
            destination = f"{destination}&{query_string}"
        
        return build_redirect_response(status_code, destination)
    
    # No match: pass through to origin
    return request
