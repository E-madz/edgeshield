import hashlib
import json
import re
from functools import partial

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from redis.exceptions import RedisError
from rest_framework import generics, serializers, status, viewsets
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response
from rest_framework.reverse import reverse

from edge.cache_sim import EdgeCacheSimulator, rules_from_policy, summarize
from edge.purge import EdgeCacheStore
from edge.limiter_factory import build_redis_limiter, client_key
from edge.redis_client import get_redis

from .models import CachePolicy, Domain, Origin, PurgeJob, RateLimitPolicy
from .serializers import (
    CachePolicySerializer,
    CacheSimulationSerializer,
    DomainDetailSerializer,
    DomainSerializer,
    OriginSerializer,
    PurgeJobSerializer,
    PurgeRequestSerializer,
    RateLimitCheckSerializer,
    RateLimitPolicySerializer,
    SeedObjectsSerializer,
)
from .tasks import enqueue_purge


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


class CachePolicySimulateView(generics.GenericAPIView):
    """POST: replay requests against an LRU cache using the stored cache policy.

    Pure computation: nothing is stored, so it is safe to call repeatedly.
    """

    serializer_class = CacheSimulationSerializer
    pagination_class = None

    def post(self, request, domain_pk):
        domain = get_object_or_404(Domain.objects.filter(owner=request.user), pk=domain_pk)
        policy, _ = CachePolicy.objects.get_or_create(domain=domain)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        simulator = EdgeCacheSimulator(rules_from_policy(policy), serializer.validated_data['capacity'])
        results = [
            simulator.request(item['path'], item['at'])
            for item in serializer.validated_data['requests']
        ]
        return Response({'summary': summarize(results, simulator.evictions), 'results': results})


class RateLimitCheckView(generics.GenericAPIView):
    """Sandbox for the stored rate limit policy, backed by the real Redis limiter.

    POST consumes capacity for `requests` simulated hits from `client_ip`;
    DELETE forgets that client's sandbox state. Keys live under the "sim"
    namespace so they can never interfere with real traffic counters.
    """

    serializer_class = RateLimitCheckSerializer
    pagination_class = None
    MAX_LISTED = 50

    def _policy(self, request, domain_pk):
        domain = get_object_or_404(Domain.objects.filter(owner=request.user), pk=domain_pk)
        policy, _ = RateLimitPolicy.objects.get_or_create(domain=domain)
        return policy

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['policy'] = self._policy(self.request, self.kwargs['domain_pk'])
        return context

    def post(self, request, domain_pk):
        policy = self._policy(request, domain_pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sent = serializer.validated_data['requests']

        if not policy.enabled:
            return Response({
                'enabled': False, 'detail': 'Rate limiting is disabled: every request passes.',
                'summary': {'sent': sent, 'allowed': sent, 'limited': 0, 'blocked': 0},
                'results': [],
            })

        key = client_key(policy, serializer.validated_data.get('client_ip'))
        decisions = []
        try:
            limiter = build_redis_limiter(policy, get_redis(), namespace='sim')
            decisions = [limiter.allow(key) for _ in range(sent)]
        except RedisError:
            return Response(
                {'detail': 'Rate limit store (Redis) is unavailable.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        limited = sum(1 for d in decisions if not d.allowed)
        enforced = policy.action == RateLimitPolicy.Action.BLOCK
        results = [
            {
                'n': i + 1,
                'allowed': d.allowed,
                'status': 200 if d.allowed or not enforced else 429,
                'remaining': d.remaining,
                'retry_after': round(d.retry_after, 3),
            }
            for i, d in enumerate(decisions[: self.MAX_LISTED])
        ]
        return Response({
            'enabled': True,
            'algorithm': policy.algorithm,
            'action': policy.action,
            'summary': {
                'sent': sent,
                'allowed': sent - limited,
                'limited': limited,  # exceeded the limit
                'blocked': limited if enforced else 0,  # actually answered with 429
            },
            'results': results,
            'truncated': sent > self.MAX_LISTED,
        })

    def delete(self, request, domain_pk):
        policy = self._policy(request, domain_pk)
        client_ip = request.query_params.get('client_ip')
        if policy.scope == RateLimitPolicy.Scope.PER_IP and not client_ip:
            raise serializers.ValidationError({'client_ip': 'Required when the scope is per client IP.'})
        key = client_key(policy, client_ip)
        try:
            client = get_redis()
            client.delete(build_redis_limiter(policy, client, namespace='sim').key(key))
        except RedisError:
            return Response(
                {'detail': 'Rate limit store (Redis) is unavailable.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


IDEMPOTENCY_KEY_RE = re.compile(r'^[A-Za-z0-9._:-]{1,64}$')
MAX_ACTIVE_PURGES = 5


def _owned_domain(request, domain_pk):
    return get_object_or_404(Domain.objects.filter(owner=request.user), pk=domain_pk)


class PurgeJobListCreateView(generics.ListCreateAPIView):
    """POST /api/domains/<id>/purges/ -> 202 Accepted + a job to poll.

    The work happens later in a Celery worker. Send an `Idempotency-Key` header
    to make the POST safe to retry (network timeout, double click):
      * same key + same body   -> the original job is returned again (200)
      * same key + other body  -> 409, the key is not reusable
    """

    serializer_class = PurgeJobSerializer

    def get_queryset(self):
        queryset = PurgeJob.objects.filter(domain=_owned_domain(self.request, self.kwargs['domain_pk']))
        wanted = self.request.query_params.get('status')
        return queryset.filter(status=wanted) if wanted else queryset

    def create(self, request, domain_pk):
        domain = _owned_domain(request, domain_pk)
        payload = PurgeRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        key = request.headers.get('Idempotency-Key')
        if key is not None and not IDEMPOTENCY_KEY_RE.match(key):
            raise serializers.ValidationError(
                {'Idempotency-Key': 'Use 1-64 characters: letters, digits, ".", "_", ":" or "-".'}
            )
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

        if key:
            existing = PurgeJob.objects.filter(domain=domain, idempotency_key=key).first()
            if existing:
                return self._replay(existing, fingerprint)

        # Back-pressure: a flood of purges must not bury the workers.
        active = PurgeJob.objects.filter(domain=domain, status__in=PurgeJob.ACTIVE_STATUSES).count()
        if active >= MAX_ACTIVE_PURGES:
            return Response(
                {'detail': f'Too many purge jobs in progress ({active}). Try again shortly.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={'Retry-After': '5'},
            )

        try:
            with transaction.atomic():
                job = PurgeJob.objects.create(
                    domain=domain, kind=data['kind'], paths=data['paths'],
                    idempotency_key=key or None, request_hash=fingerprint,
                )
                # Enqueue only AFTER the row is committed. Otherwise a fast worker can
                # receive the message before the row is visible and find nothing.
                transaction.on_commit(partial(enqueue_purge, job.pk))
        except IntegrityError:
            # Two requests with the same key raced past the check above;
            # the UNIQUE constraint picked the winner.
            existing = PurgeJob.objects.filter(domain=domain, idempotency_key=key).first()
            if existing is None:
                raise
            return self._replay(existing, fingerprint)

        job.refresh_from_db()
        body = PurgeJobSerializer(job).data
        if job.status == PurgeJob.Status.FAILED and job.error.startswith('enqueue failed'):
            return Response(
                {'detail': 'Background queue unavailable. The purge was not started.', 'job': body},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        location = reverse('purge-detail', kwargs={'domain_pk': domain.pk, 'pk': job.pk}, request=request)
        return Response(body, status=status.HTTP_202_ACCEPTED, headers={'Location': location})

    def _replay(self, existing, fingerprint):
        if existing.request_hash != fingerprint:
            return Response(
                {'detail': 'This Idempotency-Key was already used with a different request.'},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            PurgeJobSerializer(existing).data,
            status=status.HTTP_200_OK,
            headers={'Idempotent-Replayed': 'true'},
        )


class PurgeJobDetailView(generics.RetrieveAPIView):
    serializer_class = PurgeJobSerializer

    def get_queryset(self):
        return PurgeJob.objects.filter(domain=_owned_domain(self.request, self.kwargs['domain_pk']))


class CacheObjectsView(generics.GenericAPIView):
    """Demo helper: put objects into (and list them from) the simulated edge cache."""

    serializer_class = SeedObjectsSerializer
    pagination_class = None

    def get(self, request, domain_pk):
        domain = _owned_domain(request, domain_pk)
        try:
            paths = EdgeCacheStore(get_redis()).list_paths(domain.pk)
        except RedisError:
            return _redis_down()
        return Response({'count': len(paths), 'paths': paths})

    def post(self, request, domain_pk):
        domain = _owned_domain(request, domain_pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            store = EdgeCacheStore(get_redis())
            for path in serializer.validated_data['paths']:
                store.put(domain.pk, path)
        except RedisError:
            return _redis_down()
        return Response({'stored': len(serializer.validated_data['paths'])}, status=status.HTTP_201_CREATED)


def _redis_down():
    return Response({'detail': 'Cache store (Redis) is unavailable.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
