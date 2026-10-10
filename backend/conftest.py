import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def fast_password_hasher(settings):
    """PBKDF2 is deliberately slow (that is its job in production); tests create many users."""
    settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


@pytest.fixture(autouse=True)
def isolated_cache(settings):
    """Throttle counters live in the cache: use a private in-process one and start each test empty."""
    settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    cache.clear()  # (no teardown clear: a test may point CACHES at an unreachable server)
