from django.db import IntegrityError
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import generics, serializers, viewsets
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response

from .models import CachePolicy, Domain, Origin, RateLimitPolicy
from .serializers import (
    CachePolicySerializer,
    DomainDetailSerializer,
    DomainSerializer,
    OriginSerializer,
    RateLimitPolicySerializer,
)


class DomainViewSet(viewsets.ModelViewSet):
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ['name']
    ordering_fields = ['name', 'created_at']
    ordering = ['-created_at', '-id']

    def get_queryset(self):
        # Scoping to the current user is the ownership rule: someone else's
        # domain simply does not exist for you (404, not 403, so ids do not leak).
        queryset = Domain.objects.filter(owner=self.request.user)
        if self.action == 'retrieve':
            return queryset.prefetch_related('origins')
        # Meta.ordering is dropped on GROUP BY queries, so order explicitly:
        # pagination needs a deterministic order.
        return queryset.annotate(origins_count=Count('origins')).order_by('-created_at', '-id')

    def get_serializer_class(self):
        return DomainDetailSerializer if self.action == 'retrieve' else DomainSerializer

    def perform_create(self, serializer):
        # The owner is never taken from the payload.
        try:
            serializer.save(owner=self.request.user)
        except IntegrityError:
            raise serializers.ValidationError({'name': ['This domain is already registered.']})


class OriginViewSet(viewsets.ModelViewSet):
    """Origins nested under /api/domains/<domain_pk>/origins/."""

    serializer_class = OriginSerializer

    def get_domain(self):
        if not hasattr(self, '_domain'):
            self._domain = get_object_or_404(
                Domain.objects.filter(owner=self.request.user), pk=self.kwargs['domain_pk']
            )
        return self._domain

    def get_queryset(self):
        return Origin.objects.filter(domain=self.get_domain())

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['domain'] = self.get_domain()
        return context

    def perform_create(self, serializer):
        try:
            serializer.save(domain=self.get_domain())
        except IntegrityError:
            raise serializers.ValidationError('This origin already exists for the domain.')


class DomainPolicyView(generics.RetrieveUpdateAPIView):
    """Singleton sub-resource: /api/domains/<domain_pk>/<policy>/ (GET, PUT, PATCH).

    The policy row is created with defaults on first access, so clients never
    have to handle a "no policy yet" state.
    """

    model = None  # set by subclasses

    def get_object(self):
        domain = get_object_or_404(
            Domain.objects.filter(owner=self.request.user), pk=self.kwargs['domain_pk']
        )
        policy, _ = self.model.objects.get_or_create(domain=domain)
        return policy


class CachePolicyView(DomainPolicyView):
    model = CachePolicy
    serializer_class = CachePolicySerializer


class RateLimitPolicyView(DomainPolicyView):
    model = RateLimitPolicy
    serializer_class = RateLimitPolicySerializer


class DomainConfigView(generics.GenericAPIView):
    """Read-only snapshot of everything the data plane needs for one domain."""

    pagination_class = None

    def get(self, request, domain_pk):
        domain = get_object_or_404(
            Domain.objects.filter(owner=request.user).prefetch_related('origins'), pk=domain_pk
        )
        cache, _ = CachePolicy.objects.get_or_create(domain=domain)
        rate_limit, _ = RateLimitPolicy.objects.get_or_create(domain=domain)
        origins = [o for o in domain.origins.all() if o.is_active]
        return Response({
            'domain': domain.name,
            'active': domain.is_active,
            'origins': OriginSerializer(origins, many=True).data,
            'cache': CachePolicySerializer(cache).data,
            'rate_limit': RateLimitPolicySerializer(rate_limit).data,
            'version': max(domain.updated_at, cache.updated_at, rate_limit.updated_at).isoformat(),
        })
