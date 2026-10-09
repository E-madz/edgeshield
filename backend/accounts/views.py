from rest_framework import generics
from rest_framework.permissions import AllowAny

from .serializers import RegisterSerializer


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    authentication_classes = []


# TODO (your exercise): add `MeView` here. It should return the current user via
# `UserSerializer` and be reachable only with a valid access token.
