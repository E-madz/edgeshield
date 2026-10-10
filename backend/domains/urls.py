from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    CacheObjectsView,
    CachePolicySimulateView,
    CachePolicyView,
    DomainConfigView,
    DomainViewSet,
    OriginViewSet,
    PurgeJobDetailView,
    PurgeJobListCreateView,
    RateLimitCheckView,
    RateLimitPolicyView,
)

router = DefaultRouter()
router.register(r'domains/(?P<domain_pk>\d+)/origins', OriginViewSet, basename='origin')
router.register(r'domains', DomainViewSet, basename='domain')

urlpatterns = [
    path('domains/<int:domain_pk>/cache-policy/', CachePolicyView.as_view(), name='cache-policy'),
    path('domains/<int:domain_pk>/rate-limit-policy/', RateLimitPolicyView.as_view(), name='rate-limit-policy'),
    path('domains/<int:domain_pk>/cache-policy/simulate/', CachePolicySimulateView.as_view(), name='cache-simulate'),
    path('domains/<int:domain_pk>/rate-limit-policy/check/', RateLimitCheckView.as_view(), name='rate-limit-check'),
    path('domains/<int:domain_pk>/purges/', PurgeJobListCreateView.as_view(), name='purge-list'),
    path('domains/<int:domain_pk>/purges/<int:pk>/', PurgeJobDetailView.as_view(), name='purge-detail'),
    path('domains/<int:domain_pk>/cache-objects/', CacheObjectsView.as_view(), name='cache-objects'),
    path('domains/<int:domain_pk>/config/', DomainConfigView.as_view(), name='domain-config'),
] + router.urls
