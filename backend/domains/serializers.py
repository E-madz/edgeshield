from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import CachePolicy, Domain, Origin, PurgeJob, RateLimitPolicy
from .validators import (
    validate_exact_path,
    validate_hostname,
    validate_origin_host,
    validate_purge_path,
)


class HostnameField(serializers.CharField):
    """Trim and lowercase BEFORE validation so 'WWW.Example.com' is accepted and stored normalized."""

    def to_internal_value(self, data):
        return super().to_internal_value(data).lower()


class OriginSerializer(serializers.ModelSerializer):
    class Meta:
        model = Origin
        fields = ('id', 'host', 'port', 'scheme', 'weight', 'is_active', 'created_at')
        read_only_fields = ('id', 'created_at')

    def validate_host(self, value):
        value = value.strip().lower()
        validate_origin_host(value)
        return value

    def validate(self, attrs):
        # The domain comes from the URL, not the payload, so DRF cannot build the
        # unique-together check for us. The DB constraint remains the final guard.
        domain = self.context['domain']
        host = attrs.get('host', getattr(self.instance, 'host', None))
        port = attrs.get('port', getattr(self.instance, 'port', Origin._meta.get_field('port').default))
        duplicates = Origin.objects.filter(domain=domain, host=host, port=port)
        if self.instance:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError('This origin already exists for the domain.')
        return attrs


class DomainSerializer(serializers.ModelSerializer):
    # Filled by an annotate() in the list queryset: one COUNT in the same SQL
    # query instead of one extra query per row (the N+1 problem).
    origins_count = serializers.IntegerField(read_only=True, default=0)
    name = HostnameField(max_length=253, validators=[validate_hostname])

    class Meta:
        model = Domain
        fields = ('id', 'name', 'is_active', 'origins_count', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')

    def validate_name(self, value):
        queryset = Domain.objects.filter(name=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError('This domain is already registered.')
        return value


class DomainDetailSerializer(DomainSerializer):
    # Served from prefetch_related('origins'): 2 queries total, however many origins.
    origins = OriginSerializer(many=True, read_only=True)

    class Meta(DomainSerializer.Meta):
        fields = DomainSerializer.Meta.fields + ('origins',)


class CachePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = CachePolicy
        fields = (
            'enabled', 'default_ttl_seconds', 'browser_ttl_seconds', 'serve_stale_seconds',
            'query_string_mode', 'bypass_paths', 'updated_at',
        )
        read_only_fields = ('updated_at',)

    def validate_bypass_paths(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('Must be a list of path prefixes.')
        if len(value) > 50:
            raise serializers.ValidationError('At most 50 paths are allowed.')
        cleaned = []
        for path in value:
            if not isinstance(path, str) or not path.startswith('/') or len(path) > 200 \
                    or any(ch.isspace() for ch in path) or '?' in path or '#' in path:
                raise serializers.ValidationError(
                    f'Invalid path {path!r}: use prefixes like "/admin" (start with "/", no spaces, query or fragment).'
                )
            if path not in cleaned:
                cleaned.append(path)
        return cleaned


class RateLimitPolicySerializer(serializers.ModelSerializer):
    refill_rate_per_second = serializers.FloatField(read_only=True)

    class Meta:
        model = RateLimitPolicy
        fields = (
            'enabled', 'algorithm', 'limit', 'window_seconds', 'burst', 'scope', 'action',
            'refill_rate_per_second', 'updated_at',
        )
        read_only_fields = ('updated_at',)

    def validate(self, attrs):
        # On PATCH only some fields arrive: judge the merged result, not the fragment.
        def current(name):
            if name in attrs:
                return attrs[name]
            return getattr(self.instance, name, None)

        if current('algorithm') == RateLimitPolicy.Algorithm.SLIDING_WINDOW and current('burst') is not None:
            raise serializers.ValidationError(
                {'burst': 'Burst only applies to the token bucket algorithm.'}
            )
        return attrs


class RateLimitCheckSerializer(serializers.Serializer):
    client_ip = serializers.IPAddressField(required=False)
    requests = serializers.IntegerField(min_value=1, max_value=1000, default=1)

    def validate(self, attrs):
        policy = self.context['policy']
        if policy.scope == RateLimitPolicy.Scope.PER_IP and 'client_ip' not in attrs:
            raise serializers.ValidationError({'client_ip': 'Required when the scope is per client IP.'})
        return attrs


class CacheSimulationRequestItem(serializers.Serializer):
    path = serializers.CharField(max_length=2000)
    at = serializers.FloatField(min_value=0)

    def validate_path(self, value):
        if not value.startswith('/'):
            raise serializers.ValidationError('Path must start with "/".')
        return value


class CacheSimulationSerializer(serializers.Serializer):
    capacity = serializers.IntegerField(min_value=1, max_value=1000, default=100)
    requests = CacheSimulationRequestItem(many=True, allow_empty=False, max_length=500)

    def validate_requests(self, value):
        times = [item['at'] for item in value]
        if times != sorted(times):
            raise serializers.ValidationError('"at" must not decrease from one request to the next.')
        return value


class PurgeJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurgeJob
        fields = (
            'id', 'kind', 'paths', 'status', 'attempts', 'purged_count', 'error',
            'created_at', 'started_at', 'finished_at',
        )
        read_only_fields = fields


class PurgeRequestSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=PurgeJob.Kind.choices)
    paths = serializers.ListField(
        child=serializers.CharField(max_length=200), required=False, max_length=100
    )

    def validate(self, attrs):
        kind, paths = attrs['kind'], attrs.get('paths', [])
        if kind == PurgeJob.Kind.ALL:
            if paths:
                raise serializers.ValidationError({'paths': 'Do not send paths when kind is "all".'})
            return {'kind': kind, 'paths': []}
        if not paths:
            raise serializers.ValidationError({'paths': 'At least one path is required.'})
        for path in paths:
            try:
                validate_purge_path(path)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({'paths': exc.messages})
        # Order and duplicates are not meaningful: normalise so equal requests compare equal.
        return {'kind': kind, 'paths': sorted(set(paths))}


class SeedObjectsSerializer(serializers.Serializer):
    paths = serializers.ListField(
        child=serializers.CharField(max_length=200), allow_empty=False, max_length=100
    )

    def validate_paths(self, value):
        for path in value:
            try:
                validate_exact_path(path)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(exc.messages)
        return sorted(set(value))
