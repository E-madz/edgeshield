from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView

from core.throttling import ResilientScopedRateThrottle

from .serializers import RegisterSerializer, UserSerializer


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    # Throttle by client IP: slows down mass account creation.
    throttle_classes = [ResilientScopedRateThrottle]
    throttle_scope = 'auth'


class LoginView(TokenObtainPairView):
    """Token login, throttled per client IP to slow down password guessing."""

    throttle_classes = [ResilientScopedRateThrottle]
    throttle_scope = 'auth'


class MeView(generics.RetrieveAPIView):
    """Return the authenticated user.

    No permission_classes needed: settings.py makes IsAuthenticated the default,
    so a missing or invalid access token already yields 401.
    """

    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user
