"""`edges.<domain>`: standalone edges' control channel and stream-auth (ADR-0022).

Routed by Traefik (TLS, its own rate limit) and by HostURLConfMiddleware; nothing else
is served on this host, and these views are on no other host.
"""

from django.urls import path

from apps.edges import views

urlpatterns = [
    path("edge/v1/poll", views.poll, name="edge-poll"),
    path("edge/v1/ping", views.ping, name="edge-ping"),
    path("edge/v1/stream-auth", views.stream_auth, name="edge-stream-auth"),
]
