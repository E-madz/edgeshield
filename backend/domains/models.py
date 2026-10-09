from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .validators import validate_hostname


class Domain(models.Model):
    """A public hostname (e.g. www.example.com) whose traffic EdgeShield manages."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='domains'
    )
    # Globally unique: two accounts must not manage the same hostname.
    name = models.CharField(max_length=253, unique=True, validators=[validate_hostname])
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # A fixed, unique ordering is required for stable pagination.
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['owner', '-created_at'], name='domain_owner_created_idx')]

    def __str__(self):
        return self.name


class Origin(models.Model):
    """A backend server the CDN fetches content from for a domain."""

    class Scheme(models.TextChoices):
        HTTP = 'http', 'HTTP'
        HTTPS = 'https', 'HTTPS'

    domain = models.ForeignKey(Domain, on_delete=models.CASCADE, related_name='origins')
    host = models.CharField(max_length=253)
    port = models.PositiveIntegerField(
        default=443, validators=[MinValueValidator(1), MaxValueValidator(65535)]
    )
    scheme = models.CharField(max_length=5, choices=Scheme.choices, default=Scheme.HTTPS)
    # Relative share of traffic when a domain has several origins.
    weight = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['domain', 'host', 'port'], name='origin_unique_per_domain')
        ]

    def __str__(self):
        return f'{self.scheme}://{self.host}:{self.port}'
