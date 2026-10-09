"""Django settings for the EdgeShield control-plane API.

Every environment-specific value comes from environment variables (see
.env.example), so no secret is ever committed to git.
"""

from datetime import timedelta
from pathlib import Path

import environ
from corsheaders.defaults import default_headers as default_cors_headers

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
)
# Optional local .env file; real environments inject variables directly.
environ.Env.read_env(BASE_DIR.parent / '.env')

# SECURITY WARNING: keep the secret key used in production secret!
# It has no default on purpose: the app must fail loudly if it is missing.
SECRET_KEY = env('DJANGO_SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env('DJANGO_DEBUG')

ALLOWED_HOSTS = env('DJANGO_ALLOWED_HOSTS')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third party
    'rest_framework',
    'corsheaders',
    # Local
    'core',
    'accounts',
    'domains',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Serves collected static files (admin CSS/JS) straight from gunicorn, no extra volume needed.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    # Must sit above CommonMiddleware so CORS headers are added to every response.
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases
# DATABASE_URL example: mysql://user:password@host:3306/dbname
# (tests and quick local runs may use sqlite:///db.sqlite3)

DATABASES = {
    'default': env.db('DATABASE_URL'),
}
if DATABASES['default']['ENGINE'] == 'django.db.backends.mysql':
    DATABASES['default']['OPTIONS'] = {
        'charset': 'utf8mb4',
        # Make MySQL reject invalid data instead of silently truncating it.
        'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
    }
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Must be set before the first migrate of a project: swapping it later is painful.
AUTH_USER_MODEL = 'accounts.User'


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# Static files

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'  # target of `manage.py collectstatic`
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    # Compressed (gzip/brotli) but not hash-renamed, so it also works before collectstatic.
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}


# Django REST framework / JWT

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'core.pagination.StandardPagination',
    # Throttle rates by scope; a view opts in with `throttle_scope = 'auth'`.
    'DEFAULT_THROTTLE_RATES': {'auth': env('AUTH_THROTTLE_RATE', default='10/min')},
    # How many reverse proxies (nginx) sit in front of Django. Needed to find the real
    # client IP in X-Forwarded-For; 0 = trust only the socket address (safe default).
    'NUM_PROXIES': env.int('TRUSTED_PROXY_COUNT', default=0),
    'DEFAULT_RENDERER_CLASSES': (
        'rest_framework.renderers.JSONRenderer',
    ),
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
}


# Redis (shared state for rate limiting)

REDIS_URL = env('REDIS_URL', default='redis://localhost:6379/0')

# Django cache, used by DRF throttling. It must be shared by all gunicorn workers
# (the default in-process cache would give every worker its own counters).
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': env('CACHE_URL', default='redis://localhost:6379/2'),
    }
}


# Celery (background jobs). Job state is kept in our own database (PurgeJob),
# so Celery results are not stored.

CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://localhost:6379/1')
CELERY_TASK_IGNORE_RESULT = True
# Acknowledge a message only AFTER the task finished: if a worker dies mid-task the
# broker redelivers it. This gives at-least-once delivery, so tasks must be idempotent.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # do not hoard messages that a free worker could run
CELERY_TASK_TIME_LIMIT = 120
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {
    'visibility_timeout': 3600,  # redelivery delay for un-acked messages; must exceed the longest task
    'socket_connect_timeout': 2,
    'socket_timeout': 2,
}


# CORS: which browser origins (the Vue dev server) may call this API

CORS_ALLOWED_ORIGINS = env('CORS_ALLOWED_ORIGINS')
# A cross-origin browser may only send/read custom headers the server lists explicitly.
CORS_ALLOW_HEADERS = (*default_cors_headers, 'idempotency-key')
CORS_EXPOSE_HEADERS = ['Location', 'Idempotent-Replayed', 'Retry-After']


# Production hardening (see `manage.py check --deploy`).
# Active only when DEBUG is off so local development keeps working.

# Browsers must be told which origins may POST to the admin over HTTPS (CSRF).
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

if not DEBUG:
    # TLS is terminated by the reverse proxy (nginx / a load balancer), which tells
    # Django the original scheme through this header.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_CONTENT_TYPE_NOSNIFF = True

    # Everything below assumes the site is served over HTTPS. A purely local,
    # plain-HTTP "production-like" run switches it off with DJANGO_USE_HTTPS=False
    # (the compose file for local production does this). Keep it True on a real domain.
    if env.bool('DJANGO_USE_HTTPS', default=True):
        SECURE_SSL_REDIRECT = True
        # Load balancers probe over plain HTTP and must not be redirected.
        SECURE_REDIRECT_EXEMPT = [r'^api/health/']
        SESSION_COOKIE_SECURE = True
        CSRF_COOKIE_SECURE = True
        SECURE_HSTS_SECONDS = env.int('DJANGO_SECURE_HSTS_SECONDS', default=31536000)
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True
        SECURE_HSTS_PRELOAD = True


# Logging: containers log to stdout/stderr and the platform collects it.

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'plain': {'format': '%(asctime)s %(levelname)s %(name)s: %(message)s'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'},
    },
    'root': {'handlers': ['console'], 'level': env('DJANGO_LOG_LEVEL', default='INFO')},
}
