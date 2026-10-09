from rest_framework import generics
from rest_framework.permissions import AllowAny

from .serializers import RegisterSerializer, UserSerializer


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    authentication_classes = []


class MeView(generics.RetrieveAPIView):
    """Return the authenticated user.

    No permission_classes needed: settings.py makes IsAuthenticated the default,
    so a missing or invalid access token already yields 401.
    """

    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user
