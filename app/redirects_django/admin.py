from django.contrib import admin
from django.urls import path
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils.html import format_html
from django.utils import timezone
from .models import Site, Redirect, RedirectLocaleOverride, UserSiteRole, AuditLog
import csv
import hmac
import hashlib
import json
import urllib3


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ['domain', 'slug', 'is_active', 'locale_count', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['domain', 'slug']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('domain', 'slug', 'is_active')
        }),
        ('Webhook Configuration', {
            'fields': ('webhook_url', 'webhook_secret'),
            'classes': ('collapse',)
        }),
        ('Geo Configuration', {
            'fields': ('geo_locale_map', 'geo_default_locale'),
            'description': 'geo_locale_map example: {"US": "/en-us", "DE": "/de", "FR": "/fr"}'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def locale_count(self, obj):
        """Display number of locales configured."""
        if obj.geo_locale_map:
            return len(obj.geo_locale_map)
        return 0
    locale_count.short_description = 'Locales'


class RedirectLocaleOverrideInline(admin.TabularInline):
    model = RedirectLocaleOverride
    extra = 1
    fields = ['locale', 'destination']


@admin.register(Redirect)
class RedirectAdmin(admin.ModelAdmin):
    list_display = [
        'source',
        'destination',
        'status_code',
        'match_type',
        'geo_aware_display',
        'is_active',
        'has_unpublished_changes',
        'site'
    ]
    list_filter = ['site', 'status_code', 'match_type', 'geo_aware', 'is_active', 'created_at']
    search_fields = ['source', 'destination', 'notes']
    readonly_fields = ['created_at', 'updated_at', 'published_at', 'created_by', 'updated_by']
    inlines = [RedirectLocaleOverrideInline]
    actions = ['publish_selected', 'activate_selected', 'deactivate_selected', 'export_csv']
    
    fieldsets = (
        ('Basic Configuration', {
            'fields': (
                'site',
                'source',
                'destination',
                'status_code',
                'match_type',
                'priority',
                'is_active'
            )
        }),
        ('Geo-Aware Configuration', {
            'fields': ('geo_aware', 'geo_locale_allowlist'),
            'description': 'Enable geo_aware for locale-based routing. Add locale overrides below.'
        }),
        ('UTM Parameters', {
            'fields': ('utm_source', 'utm_medium', 'utm_campaign'),
            'classes': ('collapse',),
            'description': 'For vanity URLs only. Appended as query parameters.'
        }),
        ('Scheduling', {
            'fields': ('starts_at', 'expires_at'),
            'classes': ('collapse',)
        }),
        ('Audit Trail', {
            'fields': ('notes', 'created_by', 'updated_by', 'created_at', 'updated_at', 'published_at'),
            'classes': ('collapse',)
        }),
    )
    
    def geo_aware_display(self, obj):
        """Display geo-aware status with icon."""
        if obj.geo_aware:
            return format_html('<span style="color: green;">✓</span>')
        return format_html('<span style="color: gray;">—</span>')
    geo_aware_display.short_description = 'Geo'
    
    def has_unpublished_changes(self, obj):
        """Display unpublished changes indicator."""
        if obj.has_unpublished_changes:
            return format_html('<span style="color: orange;">⚠ Unpublished</span>')
        return format_html('<span style="color: green;">✓ Published</span>')
    has_unpublished_changes.short_description = 'Publish Status'
    
    def save_model(self, request, obj, form, change):
        """Track who created/updated the redirect."""
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
    
    @admin.action(description='Publish selected redirects to CloudFront')
    def publish_selected(self, request, queryset):
        """Trigger webhook for selected redirects."""
        sites_affected = set(queryset.values_list('site', flat=True))
        
        for site_id in sites_affected:
            site = Site.objects.get(pk=site_id)
            
            if not site.webhook_url or not site.webhook_secret:
                messages.warning(
                    request,
                    f'Site {site.domain} missing webhook configuration. Skipping.'
                )
                continue
            
            # Trigger webhook
            payload = json.dumps({'site_slug': site.slug, 'event': 'publish'})
            signature = hmac.new(
                site.webhook_secret.encode(),
                payload.encode(),
                hashlib.sha256
            ).hexdigest()
            
            http = urllib3.PoolManager()
            try:
                response = http.request(
                    'POST',
                    site.webhook_url,
                    body=payload,
                    headers={
                        'Content-Type': 'application/json',
                        'X-Signature-SHA256': signature
                    }
                )
                
                if response.status == 200:
                    # Update published_at timestamp
                    now = timezone.now()
                    queryset.filter(site=site).update(published_at=now)
                    
                    # Create audit log entry
                    AuditLog.objects.create(
                        user_email=request.user.email,
                        action='publish',
                        entity_type='Redirect',
                        entity_id=site_id,
                        diff={'site': site.domain, 'redirect_count': queryset.filter(site=site).count()}
                    )
                    
                    messages.success(
                        request,
                        f'Published {queryset.filter(site=site).count()} redirects for {site.domain}'
                    )
                else:
                    messages.error(
                        request,
                        f'Webhook failed for {site.domain}: HTTP {response.status}'
                    )
            except Exception as e:
                messages.error(request, f'Error publishing to {site.domain}: {str(e)}')
    
    @admin.action(description='Activate selected redirects')
    def activate_selected(self, request, queryset):
        """Activate selected redirects."""
        count = queryset.update(is_active=True)
        messages.success(request, f'Activated {count} redirects')
    
    @admin.action(description='Deactivate selected redirects')
    def deactivate_selected(self, request, queryset):
        """Deactivate selected redirects."""
        count = queryset.update(is_active=False)
        messages.success(request, f'Deactivated {count} redirects')
    
    @admin.action(description='Export selected as CSV')
    def export_csv(self, request, queryset):
        """Export selected redirects as CSV."""
        import csv
        from django.http import HttpResponse
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="redirects.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'site_slug', 'source', 'destination', 'status_code', 'match_type',
            'geo_aware', 'priority', 'utm_source', 'utm_medium', 'utm_campaign', 'notes'
        ])
        
        for r in queryset:
            writer.writerow([
                r.site.slug,
                r.source,
                r.destination,
                r.status_code,
                r.match_type,
                r.geo_aware,
                r.priority,
                r.utm_source,
                r.utm_medium,
                r.utm_campaign,
                r.notes
            ])
        
        return response
    
    def get_urls(self):
        """Add custom admin views."""
        urls = super().get_urls()
        custom_urls = [
            path('import-csv/', self.admin_site.admin_view(self.import_csv_view), name='redirects_django_import_csv'),
            path('test-url/', self.admin_site.admin_view(self.test_url_view), name='redirects_django_test_url'),
        ]
        return custom_urls + urls
    
    def import_csv_view(self, request):
        """Bulk import redirects from CSV (upsert behavior)."""
        if request.method == 'POST' and request.FILES.get('csv_file'):
            csv_file = request.FILES['csv_file']
            decoded_file = csv_file.read().decode('utf-8').splitlines()
            reader = csv.DictReader(decoded_file)
            
            created_count = 0
            updated_count = 0
            error_count = 0
            
            for row in reader:
                try:
                    site = Site.objects.get(slug=row['site_slug'])
                    
                    # Use update_or_create for upsert behavior
                    redirect, created = Redirect.objects.update_or_create(
                        site=site,
                        source=row['source'],
                        defaults={
                            'destination': row['destination'],
                            'status_code': int(row.get('status_code', 301)),
                            'match_type': row.get('match_type', 'exact'),
                            'geo_aware': row.get('geo_aware', '').lower() in ['true', '1', 'yes'],
                            'priority': int(row.get('priority', 0)),
                            'utm_source': row.get('utm_source', ''),
                            'utm_medium': row.get('utm_medium', ''),
                            'utm_campaign': row.get('utm_campaign', ''),
                            'notes': row.get('notes', ''),
                            'updated_by': request.user
                        }
                    )
                    
                    if created:
                        redirect.created_by = request.user
                        redirect.save()
                        created_count += 1
                    else:
                        updated_count += 1
                        
                except Exception as e:
                    error_count += 1
                    messages.warning(request, f'Error on row {row.get("source", "unknown")}: {str(e)}')
            
            # Create audit log
            AuditLog.objects.create(
                user_email=request.user.email,
                action='import',
                entity_type='Redirect',
                entity_id=0,
                diff={'created': created_count, 'updated': updated_count, 'errors': error_count}
            )
            
            messages.success(
                request,
                f'Import complete: {created_count} created, {updated_count} updated, {error_count} errors'
            )
            return redirect('..')
        
        return render(request, 'admin/redirects_django/redirect_import_csv.html')
    
    def test_url_view(self, request):
        """Test URL resolution with geo-aware logic."""
        result = None
        
        if request.method == 'POST':
            site_id = request.POST.get('site')
            path = request.POST.get('path')
            country = request.POST.get('country', '').upper()
            
            site = Site.objects.get(pk=site_id)
            
            # Exact match lookup
            try:
                redirect = Redirect.objects.active_for_site(site).get(
                    source=path,
                    match_type='exact'
                )
                
                destination = redirect.destination
                geo_info = None
                
                # Geo-aware resolution
                if redirect.geo_aware and country:
                    locale = site.geo_locale_map.get(country, site.geo_default_locale)
                    
                    # Check allowlist
                    if redirect.geo_locale_allowlist:
                        locale_key = locale.lstrip('/')
                        if locale_key not in redirect.geo_locale_allowlist:
                            locale = site.geo_default_locale
                            geo_info = f'Country {country} not in allowlist, using default locale'
                    
                    # Check for locale override
                    override = redirect.locale_overrides.filter(locale=locale).first()
                    if override:
                        destination = override.destination
                        geo_info = f'Locale override: {locale} → {destination}'
                    else:
                        destination = locale + destination
                        geo_info = f'Locale prefix: {locale}'
                
                result = {
                    'match_type': redirect.match_type,
                    'source': redirect.source,
                    'destination': destination,
                    'status_code': redirect.status_code,
                    'geo_aware': redirect.geo_aware,
                    'geo_info': geo_info
                }
                
            except Redirect.DoesNotExist:
                # Try regex match
                import re
                regex_redirects = Redirect.objects.active_for_site(site).filter(
                    match_type='regex'
                ).order_by('-priority')
                
                for redirect in regex_redirects:
                    if re.match(redirect.source, path):
                        result = {
                            'match_type': 'regex',
                            'source': redirect.source,
                            'destination': redirect.destination,
                            'status_code': redirect.status_code,
                            'geo_aware': False,
                            'geo_info': 'Regex redirects are not geo-aware'
                        }
                        break
                
                if not result:
                    result = {'error': f'No redirect found for {path}'}
        
        sites = Site.objects.filter(is_active=True)
        return render(request, 'admin/redirects_django/redirect_test_url.html', {
            'sites': sites,
            'result': result
        })


@admin.register(UserSiteRole)
class UserSiteRoleAdmin(admin.ModelAdmin):
    list_display = ['user', 'site', 'role', 'created_at']
    list_filter = ['role', 'site', 'created_at']
    search_fields = ['user__username', 'user__email', 'site__domain']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'user_email', 'action', 'entity_type', 'entity_id']
    list_filter = ['action', 'entity_type', 'created_at']
    search_fields = ['user_email', 'entity_type']
    readonly_fields = ['user_email', 'action', 'entity_type', 'entity_id', 'diff', 'created_at']
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False

