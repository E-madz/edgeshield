import pytest


@pytest.fixture(autouse=True)
def fast_password_hasher(settings):
    """PBKDF2 is deliberately slow (that is its job in production); tests create many users."""
    settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
