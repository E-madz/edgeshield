from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    CachePolicyView,
    DomainConfigView,
    DomainViewSet,
    OriginViewSet,
    RateLimitPolicyView,
)

router = DefaultRouter()
router.register(r'domains/(?P<domain_pk>\d+)/origins', OriginViewSet, basename='origin')
router.register(r'domains', DomainViewSet, basename='domain')

urlpatterns = [
    path('domains/<int:domain_pk>/cache-policy/', CachePolicyView.as_view(), name='cache-policy'),
    path('domains/<int:domain_pk>/rate-limit-policy/', RateLimitPolicyView.as_view(), name='rate-limit-policy'),
    path('domains/<int:domain_pk>/config/', DomainConfigView.as_view(), name='domain-config'),
] + router.urls
