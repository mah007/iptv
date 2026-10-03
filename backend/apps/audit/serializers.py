from rest_framework import serializers

from apps.audit.models import AuditLog


class AuditActorSerializer(serializers.Serializer[object]):
    id = serializers.UUIDField(read_only=True)
    username = serializers.CharField(read_only=True)


class AuditLogSerializer(serializers.ModelSerializer[AuditLog]):
    actor = AuditActorSerializer(read_only=True, allow_null=True)

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "at",
            "actor",
            "actor_ip",
            "action",
            "target_type",
            "target_id",
            "before",
            "after",
        )
        read_only_fields = fields
