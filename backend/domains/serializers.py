from rest_framework import serializers

from .models import Domain, Origin
from .validators import validate_hostname, validate_origin_host


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
