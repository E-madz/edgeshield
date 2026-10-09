import logging

from rest_framework.throttling import ScopedRateThrottle

logger = logging.getLogger(__name__)


class ResilientScopedRateThrottle(ScopedRateThrottle):
    """Scoped throttle that FAILS OPEN when its cache (Redis) is unreachable.

    Throttling is a protection layer, not the feature itself: if the counter store is
    down we log it and let the request through instead of answering 500 to every
    login. (A deliberate trade-off; a stricter product could fail closed.)
    """

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            logger.exception('Throttle backend unavailable; allowing the request')
            return True
