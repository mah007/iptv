"""Import sources' options, secrets and the rclone remote they become (ADR-0025)."""

import json

import pytest

from apps.imports import remotes
from apps.imports.models import SourceKind
from apps.imports.remotes import SourceError


def _obscure(text: str) -> str:
    return f"obscured({text})"


def test_s3_options_are_validated_and_normalised() -> None:
    options = remotes.clean_options(
        SourceKind.S3,
        {
            "provider": "b2",
            "endpoint": "https://s3.eu-central-003.backblazeb2.com/",
            "region": "eu-central-003",
            "root": "/films/classics/",
        },
    )
    assert options == {
        "provider": "b2",
        "endpoint": "https://s3.eu-central-003.backblazeb2.com",
        "region": "eu-central-003",
        "path_style": False,
        "root": "films/classics",
    }
    assert remotes.clean_options(
        SourceKind.S3, {"provider": "minio", "endpoint": "http://nas:9000"}
    )["path_style"]
    with pytest.raises(SourceError) as error:
        remotes.clean_options(SourceKind.S3, {"provider": "r2"})
    assert (error.value.field, error.value.code) == ("endpoint", "required")
    with pytest.raises(SourceError, match="region"):
        remotes.clean_options(SourceKind.S3, {"region": "us east"})
    with pytest.raises(SourceError) as error:
        remotes.clean_options(SourceKind.S3, {"host": "x"})
    assert error.value.code == "unknown_option"


@pytest.mark.parametrize(
    ("endpoint", "code"),
    [
        ("http://127.0.0.1:9000", "unsafe_destination"),
        ("http://169.254.169.254/", "unsafe_destination"),
        ("http://redis-state:6379", "unsafe_destination"),
        ("http://importer:80", "unsafe_destination"),
        ("http://admin.localhost", "unsafe_destination"),
        ("ftp://example.com", "invalid_url"),
        ("https://user:pw@example.com", "credentials_in_url"),
    ],
)
def test_endpoints_that_reach_our_own_network_are_refused(endpoint: str, code: str) -> None:
    with pytest.raises(SourceError) as error:
        remotes.clean_options(SourceKind.S3, {"provider": "other", "endpoint": endpoint})
    assert error.value.code == code


def test_sftp_options_and_pinned_host_keys() -> None:
    keyscan = (
        "files.example.com ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
    )
    options = remotes.clean_options(
        SourceKind.SFTP,
        {"host": "Files.Example.com", "port": "2222", "username": "owner", "host_keys": keyscan},
    )
    assert options["host"] == "files.example.com"
    assert options["port"] == 2222
    assert options["host_keys"].startswith("ssh-ed25519 AAAAC3")
    assert (
        remotes.clean_options(SourceKind.SFTP, {"host": "10.0.0.5", "username": "u"})["port"] == 22
    )
    for bad in ({"host": "127.0.0.1", "username": "u"}, {"host": "web", "username": "u"}):
        with pytest.raises(SourceError, match="Not allowed"):
            remotes.clean_options(SourceKind.SFTP, bad)
    with pytest.raises(SourceError) as error:
        remotes.clean_options(SourceKind.SFTP, {"host": "nas", "username": ""})
    assert error.value.field == "username"
    with pytest.raises(SourceError) as error:
        remotes.clean_options(SourceKind.SFTP, {"host": "nas", "username": "u", "port": 70000})
    assert error.value.code == "invalid_port"
    with pytest.raises(SourceError) as error:
        remotes.clean_options(
            SourceKind.SFTP, {"host": "nas", "username": "u", "host_keys": "junk"}
        )
    assert error.value.code == "invalid_host_key"


def test_paths_and_line_breaks_are_refused() -> None:
    for root in ("../etc", "films/../../x", "a\\b"):
        with pytest.raises(SourceError) as error:
            remotes.clean_root(root)
        assert error.value.code == "invalid_path"
    with pytest.raises(SourceError, match="Line breaks"):
        remotes.clean_options(
            SourceKind.WEBDAV, {"url": "https://dav.example.com", "username": "a\nb"}
        )
    assert (
        remotes.remote_path("bucket/films", "Metropolis (1927)")
        == "src:bucket/films/Metropolis (1927)"
    )
    assert remotes.remote_path("", "") == "src:"


def test_oauth_tokens_are_parsed_from_rclone_authorize() -> None:
    token = {
        "access_token": "ya29.x",
        "token_type": "Bearer",
        "refresh_token": "1//r",
        "expiry": "2026",
    }
    pasted = (
        "Paste the following into your remote machine --->\n"
        + json.dumps(token)
        + "\n<---End paste"
    )
    secrets = remotes.clean_secrets(SourceKind.DRIVE, {"token": pasted})
    assert json.loads(secrets["token"]) == token
    with pytest.raises(SourceError) as error:
        remotes.clean_secrets(SourceKind.DROPBOX, {"token": "not json"})
    assert error.value.code == "invalid_token"
    with pytest.raises(SourceError):
        remotes.clean_secrets(SourceKind.DROPBOX, {"token": json.dumps({"expiry": "x"})})
    with pytest.raises(SourceError) as error:
        remotes.clean_secrets(SourceKind.S3, {"token": "x"})
    assert error.value.code == "unknown_option"
    assert remotes.missing_secrets(SourceKind.ONEDRIVE, {}) == ["token"]
    assert remotes.missing_secrets(SourceKind.SFTP, {"private_key": "k"}) == []
    assert remotes.missing_secrets(SourceKind.S3, {"access_key_id": "a"}) == ["secret_access_key"]


def test_rclone_sections_carry_obscured_passwords_and_no_line_breaks() -> None:
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\nBBBB\n-----END OPENSSH PRIVATE KEY-----"
    sftp = remotes.rclone_section(
        SourceKind.SFTP,
        {"host": "nas", "port": 22, "username": "u", "host_keys": ""},
        {"password": "pw", "private_key": remotes.private_key(key), "key_passphrase": "kp"},
        obscure=_obscure,
        pin_host_key=True,
    )
    assert sftp["pass"] == "obscured(pw)"  # noqa: S105 (the test's stand-in obscuring)
    assert sftp["key_file_pass"] == "obscured(kp)"  # noqa: S105
    assert sftp["key_pem"] == key.replace("\n", "\\n")
    assert sftp["pin_host_key"] == "true"
    s3 = remotes.rclone_section(
        SourceKind.S3,
        {"provider": "r2", "endpoint": "https://acc.r2.cloudflarestorage.com", "path_style": False},
        {"access_key_id": "ak", "secret_access_key": "sk"},
        obscure=_obscure,
    )
    assert (s3["provider"], s3["env_auth"], s3["no_check_bucket"]) == (
        "Cloudflare",
        "false",
        "true",
    )
    assert "force_path_style" not in s3
    drive = remotes.rclone_section(
        SourceKind.DRIVE,
        {"root_folder_id": "abc", "client_id": "cid"},
        {"token": "{}"},
        obscure=_obscure,
    )
    assert drive == {
        "type": "drive",
        "token": "{}",
        "scope": "drive.readonly",
        "root_folder_id": "abc",
        "client_id": "cid",
    }
    onedrive = remotes.rclone_section(
        SourceKind.ONEDRIVE,
        {"drive_id": "b!x", "drive_type": "business"},
        {"token": "{}"},
        obscure=_obscure,
    )
    assert (onedrive["drive_id"], onedrive["drive_type"]) == ("b!x", "business")
    with pytest.raises(SourceError):
        remotes.rclone_section(
            SourceKind.WEBDAV,
            {"url": "https://dav", "vendor": "other", "username": "u"},
            {"password": "a\nb"},
            obscure=lambda text: text,
        )


def test_summaries_and_hosts() -> None:
    assert (
        remotes.summary(SourceKind.S3, {"endpoint": "https://s3.wasabisys.com", "root": "m"})
        == "s3.wasabisys.com/m"
    )
    assert (
        remotes.summary(SourceKind.SFTP, {"username": "u", "host": "nas", "port": 22}) == "u@nas:22"
    )
    assert remotes.hosts(SourceKind.WEBDAV, {"url": "https://dav.example.com:8443/x"}) == [
        ("dav.example.com", 8443)
    ]
    assert remotes.hosts(SourceKind.DRIVE, {}) == []
    assert remotes.hosts(SourceKind.S3, {"provider": "aws", "endpoint": ""}) == []
