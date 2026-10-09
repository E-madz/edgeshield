from rest_framework.routers import DefaultRouter

from .views import DomainViewSet, OriginViewSet

router = DefaultRouter()
router.register(r'domains/(?P<domain_pk>\d+)/origins', OriginViewSet, basename='origin')
router.register(r'domains', DomainViewSet, basename='domain')

urlpatterns = router.urls
