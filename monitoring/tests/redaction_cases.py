"""Log lines that must come out of the Alloy pipeline with every secret removed.

Each case is one line as a service would write it. Placeholders:

- ``<<name>>`` is a secret. The test fills it with a fresh random value of the right
  shape on every run and fails if that value reaches Loki (line, labels or metadata).
- ``[[name]]`` is a non-secret value (a request id) that must survive unchanged.

Nothing secret-shaped is committed: tokens, passwords and JWTs only exist at run time.
Every case carries a ``case`` marker (cNN) so the test can find its line again.
``expect`` lists substrings the redacted line must contain; ``unchanged`` asserts the
line passes through untouched (guards against over-eager rules).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Case:
    case_id: str
    service: str
    line: str
    expect: tuple[str, ...] = field(default=())
    unchanged: bool = False


CASES: tuple[Case, ...] = (
    # --- Django (structlog JSON). Django redacts at the source; these simulate a line
    # that slipped through, so Alloy is a real second layer.
    Case(
        "c01",
        "web",
        r'{"method": "GET", "host": "tv.localhost", "path": "/movie/<<xt_user>>/<<xt_pass>>/1001.mp4", '
        r'"status": 302, "latency_ms": 4.2, "request_id": "[[rid_web]]", "event": "request", '
        r'"level": "info", "logger": "apps.request", "case": "c01"}',
        expect=('"/movie/***/***/1001.mp4"', '"status": 302', '"request_id": "[[rid_web]]"'),
    ),
    Case(
        "c02",
        "web",
        r'{"event": "request", "path": "/series/<<se_user>>/<<se_pass>>/55.mkv", '
        r'"other": "/timeshift/<<ts_user>>/<<ts_pass>>/120/2026-10-03:12-00/7.ts", "case": "c02"}',
        expect=("/series/***/***/55.mkv", "/timeshift/***/***/120/2026-10-03:12-00/7.ts"),
    ),
    Case(
        "c03",
        "web",
        r'{"event": "request", "path": "/<<sl_user>>/<<sl_pass>>/12345", "status": 404, '
        r'"live": "/live/<<lv_user>>/<<lv_pass>>/77.m3u8", "case": "c03"}',
        expect=('"path": "/***/***/12345"', "/live/***/***/77.m3u8"),
    ),
    Case(
        "c04",
        "web",
        r'{"event": "request", "path": "/player_api.php?username=<<q_user>>&password=<<q_pass>>'
        r'&action=get_vod_streams", "case": "c04"}',
        expect=("username=***&password=***&action=get_vod_streams",),
    ),
    Case(
        "c05",
        "web",
        r'{"event": "readiness check failed", "level": "error", "exception": "ConnectionError: '
        r'Error connecting to redis://:<<redis_pw>>@redis-state:6379/0", "case": "c05"}',
        expect=("redis://***:***@redis-state:6379/0",),
    ),
    Case(
        "c06",
        "web",
        r'{"event": "bad payload", "body": "{\"username\": \"admin\", \"password\": \"<<body_pw>>\"}", '
        r'"case": "c06"}',
        expect=(r"\"password\": \"***\"", r"\"username\": \"admin\""),
    ),
    Case(
        "c07",
        "web",
        r'{"event": "mfa verify", "otp": <<otp_digits>>, "totp_secret": "<<totp_secret>>", '
        r'"footprint": "keep-me", "case": "c07"}',
        expect=('"otp": "***"', '"totp_secret": "***"', '"footprint": "keep-me"'),
    ),
    Case(
        "c08",
        "web",
        r'{"event": "upstream call", "headers": {"Authorization": "Bearer <<bearer>>", '
        r'"Accept": "application/json"}, "case": "c08"}',
        expect=('"Authorization": "***"', '"Accept": "application/json"'),
    ),
    Case(
        "c09",
        "web",
        r'{"event": "issued <<jwt>> for device", "case": "c09"}',
        expect=('"event": "issued *** for device"',),
    ),
    Case(
        "c10",
        "web",
        r"""{"event": "kwargs {'password': '<<repr_pw>>', 'name': 'basic'}", "case": "c10"}""",
        expect=("'password': '***'", "'name': 'basic'"),
    ),
    Case(
        "c11",
        "web",
        r'{"event": "env MEILI_MASTER_KEY=<<meili_key>> DJANGO_SECRET_KEY=<<django_key>> '
        r'PATH=/usr/local/bin", "case": "c11"}',
        expect=("MEILI_MASTER_KEY=***", "DJANGO_SECRET_KEY=***", "PATH=/usr/local/bin"),
    ),
    Case(
        "c12",
        "web",
        r'{"event": "redirect", "next": "/login?next=%2Fget.php%3Fusername%3D<<pe_user>>'
        r'%26password%3D<<pe_pass>>%26type%3Dm3u", "case": "c12"}',
        expect=("username%3D***%26password%3D***%26type%3Dm3u",),
    ),
    Case(
        "c13",
        "web",
        r'{"event": "auth retry password=\"<<lf_pw>>\" attempt=2", "case": "c13"}',
        expect=("attempt=2",),
    ),
    Case(
        "c14",
        "web",
        r'{"event": "config loaded secret: <<prose_secret>> from env", "case": "c14"}',
        expect=("secret: *** from env",),
    ),
    Case(
        "c15",
        "web",
        r'{"event": "redirect", "location": "https://media.localhost/v/<<edge_token>>/1001.mp4", '
        r'"case": "c15"}',
        expect=("https://media.localhost/v/***/1001.mp4",),
    ),
    Case(
        "c16",
        "web",
        r'{"event": "request", "method": "GET", "path": "/api/v1/admin/customers/'
        r'0192f0e2-1111-7abc-8def-0123456789ab", "status": 200, "user_id": "0192f0e2-2222-7abc-8def-'
        r'0123456789ab", "key_count": 3, "case": "c16"}',
        unchanged=True,
    ),
    # --- Traefik access log (Go JSON: & is written as &).
    Case(
        "c17",
        "traefik",
        r'{"ClientHost":"203.0.113.7","DownstreamStatus":200,"RequestHost":"tv.localhost",'
        r'"RequestMethod":"GET","RequestPath":"/get.php?username=<<t_user>>&password=<<t_pass>>'
        r'&type=m3u_plus","RouterName":"tv@docker","downstream_X-Request-Id":"[[rid_traefik]]",'
        r'"request_Authorization":"Basic <<basic_auth>>","request_Cookie":"sessionid=<<cookie_sess>>; '
        r'csrftoken=<<cookie_csrf>>","case":"c17"}',
        expect=(
            r"username=***&password=***&type=m3u_plus",
            '"request_Authorization":"***"',
            '"request_Cookie":"***"',
            '"downstream_X-Request-Id":"[[rid_traefik]]"',
        ),
    ),
    Case(
        "c18",
        "traefik",
        r'{"RequestPath":"/movie/<<t_xt_user>>/<<t_xt_pass>>/2002.mkv","DownstreamStatus":302,'
        r'"OriginStatus":302,"case":"c18"}',
        expect=('"RequestPath":"/movie/***/***/2002.mkv"', '"DownstreamStatus":302'),
    ),
    Case(
        "c19",
        "traefik",
        r'{"RequestPath":"\/series\/<<esc_user>>\/<<esc_pass>>\/3.mp4","note":"password=ab<<<esc_pw>>'
        r'&next=1","case":"c19"}',
        expect=(r"\/series\/***\/***\/3.mp4", r"password=***&next=1"),
    ),
    # --- Nginx edge access log (streaming/nginx/nginx.conf, log_format edge_json). The
    # edge already replaces the token with a session prefix; a token-shaped path here
    # simulates a regression, which Alloy must still catch. Feeds iptv_edge_* too.
    Case(
        "c20",
        "nginx-stream",
        r'{"time":"2026-10-03T12:00:00+00:00","edge":"edge-test","request_id":"[[rid_edge]]",'
        r'"remote_addr":"203.0.113.9","method":"GET","path":"/v/<<edge_token_2>>/hls/seg-1.m4s",'
        r'"status":200,"bytes_sent":1048576,"request_time":0.120,"range":"","session":"0192f0e2",'
        r'"kid":"k1","title":"42","rendition":"hls","verdict":"ok","reason":"","auth_status":"204",'
        r'"auth_cache":"HIT","upstream_cache_status":"HIT","user_agent":"ExoPlayer","case":"c20"}',
        expect=('"path":"/v/***/hls/seg-1.m4s"', '"bytes_sent":1048576'),
    ),
    Case(
        "c21",
        "nginx-stream",
        r'{"time":"2026-10-03T12:00:01+00:00","edge":"edge-test","request_id":"r21","method":"GET",'
        r'"path":"/v/<<edge_token_3>>/compat.mp4","status":206,"bytes_sent":4194304,'
        r'"request_time":0.850,"range":"bytes=0-","verdict":"ok","reason":"","auth_status":"204",'
        r'"auth_cache":"MISS","upstream_cache_status":"MISS","case":"c21"}',
        expect=('"path":"/v/***/compat.mp4"',),
    ),
    Case(
        "c22",
        "nginx-stream",
        r'{"time":"2026-10-03T12:00:02+00:00","edge":"edge-test","request_id":"r22","method":"GET",'
        r'"path":"/v/<<edge_token_4>>/compat.mp4","status":503,"bytes_sent":157,'
        r'"request_time":5.001,"verdict":"ok","reason":"unavailable","auth_status":"502",'
        r'"auth_cache":"-","upstream_cache_status":"MISS","case":"c22"}',
        expect=('"status":503', '"reason":"unavailable"'),
    ),
    Case(
        "c23",
        "nginx-stream",
        r'{"time":"2026-10-03T12:00:03+00:00","edge":"edge-test","request_id":"r23","method":"GET",'
        r'"path":"/v/<<edge_token_5>>/hls/index.m3u8","status":403,"bytes_sent":0,'
        r'"request_time":0.001,"verdict":"expired","reason":"expired","auth_status":"",'
        r'"auth_cache":"","upstream_cache_status":"","case":"c23"}',
        expect=('"status":403', '"reason":"expired"'),
    ),
    # --- Third-party logfmt and plain-text lines.
    Case(
        "c24",
        "alertmanager",
        r'time=2026-10-03T12:00:00Z level=WARN source=notify.go msg="Notify attempt failed" '
        r'integration=telegram err="Post \"https://api.telegram.org/bot<<tg_token>>/sendMessage\": '
        r'dial tcp: lookup api.telegram.org: no such host" case=c24',
        expect=("https://api.telegram.org/bot***/sendMessage",),
    ),
    Case(
        "c25",
        "postgres-exporter",
        r'time=2026-10-03T12:00:00Z level=ERROR msg="Error opening connection to database" '
        r'dsn="postgresql://iptv:<<pg_pw>>@postgres:5432/iptv?sslmode=disable" case=c25',
        expect=("postgresql://***:***@postgres:5432/iptv?sslmode=disable",),
    ),
    Case(
        "c26",
        "redis-state",
        r"case=c26 1:M 03 Oct 2026 12:00:00.000 * valkey-server --requirepass <<valkey_pw>> --appendonly yes",
        expect=("--requirepass *** --appendonly yes",),
    ),
    Case(
        "c27",
        "frontend",
        r"case=c27 upstream request Authorization: Basic <<raw_basic>>",
        expect=("Authorization: ***",),
    ),
    Case(
        "c28",
        "frontend",
        r'case=c28 10.0.0.5 - - "GET /live/<<pl_user>>/<<pl_pass>>/9.ts HTTP/1.1" 200 512',
        expect=('"GET /live/***/***/9.ts HTTP/1.1" 200 512',),
    ),
    Case(
        "c29",
        "frontend",
        r"case=c29 settings: password: <<yaml_pw>>",
        expect=("password: ***",),
    ),
    Case(
        "c30",
        "frontend",
        r"case=c30 Cookie: sessionid=<<raw_sess>>; csrftoken=<<raw_csrf>>",
        expect=("Cookie: ***",),
    ),
    Case(
        "c31",
        "worker",
        r'{"event": "token refresh", "refresh_token": "<<refresh_tok>>", "token_type": "<<tok_type>>", '
        r'"url": "https://api.localhost/api/v1/auth/refresh?access_token=<<acc_tok>>", "case": "c31"}',
        expect=('"refresh_token": "***"', "access_token=***"),
    ),
    # --- M13 additions: the live Traefik format, monitoring sign-in, Grafana, Celery.
    Case(
        "c32",
        "traefik",
        r'{"ClientHost":"203.0.113.7","DownstreamStatus":302,"RequestHost":"grafana.localhost",'
        r'"RequestMethod":"GET","RouterName":"grafana-sso@docker","entryPointName":"web",'
        r'"request_X-Request-Id":"[[rid_traefik_req]]","request_User-Agent":"Mozilla/5.0",'
        r'"request_Cookie":"__Host-iptv_monitor=<<mon_cookie>>","level":"info","msg":"","case":"c32"}',
        expect=('"request_Cookie":"***"', '"RouterName":"grafana-sso@docker"'),
    ),
    Case(
        "c33",
        "web",
        r'{"event": "request", "method": "GET", "host": "grafana.localhost", '
        r'"path": "/_sso/callback?ticket=<<sso_ticket>>&next=%2Fd%2Fiptv-edges", "status": 302, '
        r'"route": "monitoring-sso-callback", "request_id": "[[rid_sso]]", "case": "c33"}',
        expect=('"route": "monitoring-sso-callback"', '"request_id": "[[rid_sso]]"'),
    ),
    Case(
        "c34",
        "grafana",
        r"logger=context userId=3 orgId=1 uname=owner t=2026-10-03T12:00:00Z level=info "
        r'msg="Request Completed" method=GET path=/api/search status=200 '
        r"remote_addr=172.31.254.2 referer= handler=/api/search case=c34 "
        r'header="X-Grafana-Device-Id" token=<<grafana_token>>',
        expect=("token=***", "uname=owner"),
    ),
    Case(
        "c35",
        "worker",
        r'{"event": "Task apps.notifications.tasks.deliver[9f0c] raised: SMTPAuthenticationError '
        r'smtp://mailer:<<smtp_pw>>@smtp.example.com:587", "level": "error", "case": "c35"}',
        expect=("smtp://***:***@smtp.example.com:587",),
    ),
    Case(
        "c36",
        "alertmanager",
        r'time=2026-10-03T12:00:00Z level=ERROR source=dispatch.go msg="Notify for alerts failed" '
        r'err="ops/webhook[0]: notify retry canceled: Post \"<redacted>\": token=<<am_token>>" '
        r"case=c36",
        expect=("token=***",),
    ),
    Case(
        "c37",
        "worker",
        r'{"event": "channel probe failed", "channel": "news-1", '
        r'"source_url": "http://<<src_user>>:<<src_pw>>@origin.example.com/live/1.ts", '
        r'"case": "c37"}',
        expect=('"source_url": "***"', '"channel": "news-1"'),
    ),
    # Development's console format (structlog key=value, coloured): request_id still
    # becomes metadata, and the path is redacted.
    Case(
        "c38",
        "web",
        "\x1b[2m2026-10-06T10:10:31Z\x1b[0m [\x1b[32minfo\x1b[0m] request "
        "\x1b[36mpath\x1b[0m=\x1b[35m/movie/<<con_user>>/<<con_pass>>/1.mp4\x1b[0m "
        "\x1b[36mrequest_id\x1b[0m=\x1b[35m[[rid_console]]\x1b[0m case=c38",
        expect=("/movie/***/***/1.mp4",),
    ),
)
