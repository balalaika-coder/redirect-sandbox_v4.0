from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .models import Site, Redirect, RedirectLocaleOverride


class LocaleOverrideSerializer(serializers.ModelSerializer):
    """Serializer for RedirectLocaleOverride."""
    
    class Meta:
        model = RedirectLocaleOverride
        fields = ['locale', 'destination']


class RedirectSerializer(serializers.ModelSerializer):
    """Serializer for Redirect with nested locale overrides."""
    
    locale_overrides = LocaleOverrideSerializer(many=True, read_only=True)
    
    class Meta:
        model = Redirect
        fields = [
            'id',
            'source',
            'destination',
            'status_code',
            'match_type',
            'priority',
            'geo_aware',
            'geo_locale_allowlist',
            'locale_overrides',
            'utm_source',
            'utm_medium',
            'utm_campaign',
            'is_active',
            'starts_at',
            'expires_at',
            'published_at'
        ]


@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def site_redirects(request, slug):
    """
    GET /api/v1/sites/{slug}/redirects/
    
    Returns all active redirects for a site plus geo configuration.
    Used by the sync Lambda to build KVS state.
    """
    site = get_object_or_404(Site, slug=slug, is_active=True)
    redirects = Redirect.objects.active_for_site(site)
    
    serializer = RedirectSerializer(redirects, many=True)
    
    return Response({
        'site': {
            'domain': site.domain,
            'slug': site.slug,
            'geo_locale_map': site.geo_locale_map,
            'geo_default_locale': site.geo_default_locale
        },
        'redirects': serializer.data
    })

