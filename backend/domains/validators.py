import ipaddress
import re

from django.core.exceptions import ValidationError

_LABEL = re.compile(r'^(?!-)[a-z0-9-]{1,63}(?<!-)$')


def validate_hostname(value):
    """Accept a lowercase DNS hostname with at least two labels (no scheme, port or path)."""
    if len(value) > 253 or value != value.lower():
        raise ValidationError('Enter a lowercase hostname of at most 253 characters.')
    labels = value.split('.')
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        raise ValidationError('Enter a valid hostname such as "www.example.com".')


def validate_origin_host(value):
    """An origin host is either a valid IP address or a valid hostname."""
    try:
        ipaddress.ip_address(value)
        return
    except ValueError:
        pass
    validate_hostname(value)
