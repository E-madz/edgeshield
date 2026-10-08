from django.urls import path

from . import views

urlpatterns = [
    path('health/', views.readiness, name='health-ready'),
    path('health/live/', views.liveness, name='health-live'),
]
