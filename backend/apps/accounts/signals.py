"""Signals other apps subscribe to; playback (M7) stops sessions on all three.

Every signal is sent after the change commits, with `user_id` (UUID) and, for
devices, `device_id`.
"""

from django.dispatch import Signal

# A customer's access period ended (the expiry job, every 5 minutes).
access_expired = Signal()
# A customer was suspended or disabled by an admin.
access_suspended = Signal()
# A device was blocked or revoked: its sessions must stop.
device_disabled = Signal()
