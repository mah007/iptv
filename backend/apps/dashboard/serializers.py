from typing import Any

from rest_framework import serializers


class KpisSerializer(serializers.Serializer[Any]):
    customers_total = serializers.IntegerField(help_text="Every customer account.")
    customers_active = serializers.IntegerField(
        help_text="Active accounts whose access has not ended."
    )
    customers_expired = serializers.IntegerField(help_text="Active accounts whose access ended.")
    customers_suspended = serializers.IntegerField(help_text="Suspended accounts.")
    expiring_7d = serializers.IntegerField(help_text="Active access ending within 7 days.")
    devices_total = serializers.IntegerField(help_text="Devices that are not revoked.")
    devices_blocked = serializers.IntegerField(help_text="Blocked devices (not revoked).")
    as_of = serializers.DateTimeField(help_text="When the figures were computed (cached 30 s).")
