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
        # Serves "my domains, newest first" completely from the index: equality on
        # owner, then the exact sort order of the API (created_at DESC, id DESC) so
        # MySQL needs no filesort. Without `-id` here the tie-breaker forced a sort.
        indexes = [
            models.Index(fields=['owner', '-created_at', '-id'], name='domain_owner_created_id_idx')
        ]

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


class CachePolicy(models.Model):
    """How the CDN caches responses for one domain (exactly one policy per domain)."""

    class QueryStringMode(models.TextChoices):
        IGNORE = 'ignore', 'Ignore query string'
        INCLUDE = 'include', 'Include query string in cache key'

    domain = models.OneToOneField(Domain, on_delete=models.CASCADE, related_name='cache_policy')
    enabled = models.BooleanField(default=True)
    default_ttl_seconds = models.PositiveIntegerField(
        default=3600, validators=[MaxValueValidator(31_536_000)]  # up to 1 year
    )
    browser_ttl_seconds = models.PositiveIntegerField(
        default=0, validators=[MaxValueValidator(31_536_000)]
    )
    # Serve an expired object while it is refreshed in the background.
    serve_stale_seconds = models.PositiveIntegerField(
        default=0, validators=[MaxValueValidator(604_800)]  # up to 7 days
    )
    query_string_mode = models.CharField(
        max_length=10, choices=QueryStringMode.choices, default=QueryStringMode.IGNORE
    )
    # Path prefixes that must never be cached, e.g. ["/admin", "/api/private"].
    bypass_paths = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'cache policy of {self.domain_id}'


class RateLimitPolicy(models.Model):
    """Request rate limit applied per client at the edge for one domain."""

    class Algorithm(models.TextChoices):
        TOKEN_BUCKET = 'token_bucket', 'Token bucket'
        SLIDING_WINDOW = 'sliding_window', 'Sliding window'

    class Scope(models.TextChoices):
        PER_IP = 'per_ip', 'Per client IP'
        GLOBAL = 'global', 'Whole domain'

    class Action(models.TextChoices):
        BLOCK = 'block', 'Reject with 429'
        LOG_ONLY = 'log_only', 'Only record (monitor mode)'

    domain = models.OneToOneField(Domain, on_delete=models.CASCADE, related_name='rate_limit_policy')
    enabled = models.BooleanField(default=False)
    algorithm = models.CharField(
        max_length=20, choices=Algorithm.choices, default=Algorithm.TOKEN_BUCKET
    )
    # "limit requests per window_seconds"; for a token bucket this is the refill rate.
    limit = models.PositiveIntegerField(
        default=100, validators=[MinValueValidator(1), MaxValueValidator(1_000_000)]
    )
    window_seconds = models.PositiveIntegerField(
        default=60, validators=[MinValueValidator(1), MaxValueValidator(86_400)]
    )
    # Token bucket capacity (max burst). Null means "same as limit". Unused by sliding window.
    burst = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(1_000_000)]
    )
    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.PER_IP)
    action = models.CharField(max_length=10, choices=Action.choices, default=Action.BLOCK)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def capacity(self):
        """Token bucket capacity actually used."""
        return self.burst or self.limit

    @property
    def refill_rate_per_second(self):
        return round(self.limit / self.window_seconds, 4)

    def __str__(self):
        return f'rate limit policy of {self.domain_id}'


class PurgeJob(models.Model):
    """A request to remove cached objects of a domain, executed asynchronously by Celery."""

    class Kind(models.TextChoices):
        ALL = 'all', 'Everything'
        PATHS = 'paths', 'Specific paths'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'      # saved, waiting for a worker
        RUNNING = 'running', 'Running'
        RETRYING = 'retrying', 'Retrying'   # last attempt failed, another one is scheduled
        SUCCEEDED = 'succeeded', 'Succeeded'
        FAILED = 'failed', 'Failed'

    ACTIVE_STATUSES = (Status.PENDING, Status.RUNNING, Status.RETRYING)

    domain = models.ForeignKey(Domain, on_delete=models.CASCADE, related_name='purge_jobs')
    kind = models.CharField(max_length=10, choices=Kind.choices)
    paths = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    purged_count = models.PositiveIntegerField(null=True, blank=True)
    error = models.TextField(blank=True)
    # Client-supplied key making the POST safe to retry (see the view for the rules).
    idempotency_key = models.CharField(max_length=64, null=True, blank=True)
    request_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            # NULL keys are exempt (MySQL and SQLite allow many NULLs in a UNIQUE index).
            models.UniqueConstraint(fields=['domain', 'idempotency_key'], name='purge_unique_idempotency_key')
        ]
        indexes = [models.Index(fields=['domain', 'status'], name='purge_domain_status_idx')]

    def __str__(self):
        return f'purge {self.pk} ({self.status})'
