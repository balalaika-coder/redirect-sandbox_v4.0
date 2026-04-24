from django.urls import path
from . import api

urlpatterns = [
    path('api/v1/sites/<slug:slug>/redirects/', api.site_redirects, name='site-redirects'),
]

