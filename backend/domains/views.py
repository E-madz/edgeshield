from django.db import IntegrityError
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import serializers, viewsets
from rest_framework.filters import OrderingFilter, SearchFilter

from .models import Domain, Origin
from .serializers import DomainDetailSerializer, DomainSerializer, OriginSerializer


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
