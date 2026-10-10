import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('edgeshield')
# Every setting named CELERY_* in settings.py configures Celery.
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
