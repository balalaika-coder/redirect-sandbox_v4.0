from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
import re


class Site(models.Model):
    """A website/domain that can have redirects."""
    domain = models.CharField(max_length=255, unique=True, help_text="e.g., trendaisecurity.com")
    slug = models.SlugField(max_length=100, unique=True, help_text="URL-safe identifier, e.g., trendai")
    webhook_url = models.URLField(blank=True, help_text="API Gateway webhook URL for sync")
    webhook_secret = models.CharField(max_length=255, blank=True, help_text="HMAC secret for webhook auth")
    is_active = models.BooleanField(default=True)
    
    # Geo configuration
    geo_locale_map = models.JSONField(
        default=dict,
        blank=True,
        help_text='Map of country codes to locale paths, e.g., {"US": "/en-us", "DE": "/de"}'
    )
    geo_default_locale = models.CharField(
        max_length=50,
        blank=True,
        help_text='Default locale path for unrecognized countries, e.g., /en-us'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['domain']

    def __str__(self):
        return self.domain

    def clean(self):
        """Validate geo_locale_map contains valid ISO 3166-1 alpha-2 country codes."""
        if self.geo_locale_map:
            iso_pattern = re.compile(r'^[A-Z]{2}$')
            for country_code in self.geo_locale_map.keys():
                if not iso_pattern.match(country_code):
                    raise ValidationError(
                        f'Invalid country code "{country_code}" in geo_locale_map. '
                        f'Must be ISO 3166-1 alpha-2 (e.g., US, DE, JP).'
                    )


class RedirectManager(models.Manager):
    """Custom manager for Redirect model."""
    
    def active_for_site(self, site):
        """Return all active, non-expired redirects for a site."""
        from django.utils import timezone
        now = timezone.now()
        
        return self.filter(
            site=site,
            is_active=True
        ).filter(
            models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now)
        ).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gte=now)
        )


class Redirect(models.Model):
    """A redirect rule."""
    
    MATCH_TYPE_CHOICES = [
        ('exact', 'Exact Match'),
        ('regex', 'Regular Expression'),
        ('vanity', 'Vanity URL'),
    ]
    
    STATUS_CODE_CHOICES = [
        (301, '301 Permanent'),
        (302, '302 Temporary'),
        (307, '307 Temporary (Preserve Method)'),
    ]
    
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='redirects')
    source = models.CharField(max_length=2048, help_text="Source path, e.g., /old-page")
    destination = models.CharField(max_length=2048, help_text="Destination URL or path")
    status_code = models.IntegerField(choices=STATUS_CODE_CHOICES, default=301)
    match_type = models.CharField(max_length=10, choices=MATCH_TYPE_CHOICES, default='exact')
    priority = models.IntegerField(default=0, help_text="Higher priority = evaluated first (for regex)")
    
    # Geo-aware redirect fields
    geo_aware = models.BooleanField(
        default=False,
        help_text="Enable geo-aware routing with locale overrides"
    )
    geo_locale_allowlist = models.JSONField(
        null=True,
        blank=True,
        help_text='List of allowed locales for phased rollout, e.g., ["de", "fr"]. Requires geo_aware=True.'
    )
    
    # UTM parameters for vanity URLs
    utm_source = models.CharField(max_length=255, blank=True)
    utm_medium = models.CharField(max_length=255, blank=True)
    utm_campaign = models.CharField(max_length=255, blank=True)
    
    # Scheduling
    starts_at = models.DateTimeField(null=True, blank=True, help_text="Redirect becomes active at this time")
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Redirect stops being active at this time")
    
    # Metadata
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='redirects_created'
    )
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='redirects_updated'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True, help_text="Last time this redirect was published to CloudFront")

    objects = RedirectManager()

    class Meta:
        ordering = ['-priority', 'source']
        unique_together = [['site', 'source']]
        indexes = [
            models.Index(fields=['site', 'is_active']),
            models.Index(fields=['site', 'source']),
        ]

    def __str__(self):
        return f"{self.source} → {self.destination} ({self.status_code})"

    def clean(self):
        """Validate redirect configuration."""
        errors = {}
        
        # Regex redirects cannot be geo-aware
        if self.match_type == 'regex' and self.geo_aware:
            errors['geo_aware'] = 'Regex redirects cannot be geo-aware.'
        
        # Geo-aware redirects must use relative paths
        if self.geo_aware and self.destination.startswith(('http://', 'https://')):
            errors['destination'] = 'Geo-aware redirects must use relative paths (e.g., /products).'
        
        # geo_locale_allowlist requires geo_aware
        if self.geo_locale_allowlist and not self.geo_aware:
            errors['geo_locale_allowlist'] = 'geo_locale_allowlist requires geo_aware to be enabled.'
        
        # Source must start with /
        if not self.source.startswith('/'):
            errors['source'] = 'Source path must start with / (e.g., /old-page).'
        
        # Expires must be after starts
        if self.starts_at and self.expires_at and self.expires_at <= self.starts_at:
            errors['expires_at'] = 'Expiration date must be after start date.'
        
        if errors:
            raise ValidationError(errors)

    @property
    def has_unpublished_changes(self):
        """Check if this redirect has changes since last publish."""
        if not self.published_at:
            return True
        return self.updated_at > self.published_at


class RedirectLocaleOverride(models.Model):
    """Per-locale destination override for geo-aware redirects."""
    
    redirect = models.ForeignKey(
        Redirect,
        on_delete=models.CASCADE,
        related_name='locale_overrides'
    )
    locale = models.CharField(
        max_length=50,
        help_text='Locale path from site geo_locale_map, e.g., /de'
    )
    destination = models.CharField(
        max_length=2048,
        help_text='Destination path for this locale, e.g., /de/produkte'
    )

    class Meta:
        unique_together = [['redirect', 'locale']]
        ordering = ['locale']

    def __str__(self):
        return f"{self.redirect.source} → {self.destination} (locale: {self.locale})"

    def clean(self):
        """Validate that parent redirect is geo-aware and locale exists in site map."""
        errors = {}
        
        if not self.redirect.geo_aware:
            errors['redirect'] = 'Can only add locale overrides to geo-aware redirects.'
        
        if self.redirect.site.geo_locale_map:
            valid_locales = list(self.redirect.site.geo_locale_map.values())
            if self.locale not in valid_locales:
                errors['locale'] = (
                    f'Locale "{self.locale}" not found in site geo_locale_map. '
                    f'Valid locales: {", ".join(valid_locales)}'
                )
        
        if errors:
            raise ValidationError(errors)


class UserSiteRole(models.Model):
    """Per-site RBAC for users."""
    
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('editor', 'Editor'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='site_roles')
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='user_roles')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='editor')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [['user', 'site']]
        ordering = ['site', 'user__username']

    def __str__(self):
        return f"{self.user.username} - {self.site.domain} ({self.role})"


class AuditLog(models.Model):
    """Audit trail for all changes to redirects and sites."""
    
    ACTION_CHOICES = [
        ('create', 'Create'),
        ('update', 'Update'),
        ('delete', 'Delete'),
        ('publish', 'Publish'),
        ('import', 'Bulk Import'),
    ]
    
    user_email = models.EmailField()
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    entity_type = models.CharField(max_length=50, help_text='e.g., Redirect, Site')
    entity_id = models.IntegerField()
    diff = models.JSONField(
        null=True,
        blank=True,
        help_text='JSON object showing what changed'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['entity_type', 'entity_id']),
            models.Index(fields=['user_email']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return f"{self.user_email} {self.action} {self.entity_type} #{self.entity_id} at {self.created_at}"

