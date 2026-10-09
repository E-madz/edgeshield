from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    """Manager for a user model that logs in with an email instead of a username."""

    use_in_migrations = True

    def get_by_natural_key(self, email):
        # Used by Django's auth backend at login: make the lookup case-insensitive
        # so "Ali@Example.com" and "ali@example.com" are the same account.
        return self.get(email__iexact=email)

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('The email must be set')
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)  # hashes the password (PBKDF2), never stores plain text
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        if extra_fields['is_staff'] is not True or extra_fields['is_superuser'] is not True:
            raise ValueError('Superuser must have is_staff=True and is_superuser=True')
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    # unique=True creates a UNIQUE index in the database. That index, not the
    # serializer check, is what really guarantees no duplicate emails.
    email = models.EmailField('email address', unique=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email
