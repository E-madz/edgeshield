import logging

from django.db import connection
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger(__name__)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def liveness(request):
    """Is the process up? Deliberately touches nothing else (no DB, no cache).

    An orchestrator restarts the container when this fails, so it must not
    fail just because a dependency is down.
    """
    return Response({'status': 'ok'})


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def readiness(request):
    """Can this instance serve traffic? Verifies the database really answers.

    An orchestrator or load balancer stops routing to the instance (without
    restarting it) when this returns 503.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        logger.exception('Readiness check failed: database unreachable')
        return Response(
            {'status': 'unavailable', 'database': 'down'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response({'status': 'ok', 'database': 'up'})
