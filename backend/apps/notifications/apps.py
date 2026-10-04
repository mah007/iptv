from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "apps.notifications"
    label = "notifications"

    def ready(self) -> None:
        # Customer sign-in (ADR-0013) emails password links through us (ADR-0012).
        from apps.accounts import customer_auth  # noqa: PLC0415 (apps are loaded now)
        from apps.notifications.services import send_password_link  # noqa: PLC0415

        customer_auth.set_password_link_sender(send_password_link)
