/*
 * Smart IPTV media edge: bytes served per playback session (G-17's second half, ADR-0024).
 *
 * stream-auth runs before a response, so it can't learn the response's size. Instead:
 *
 *   note()    $edge_bytes_note, referenced only by the `edge_bytes` access log (written
 *             to /dev/null), so it runs once at the end of every token request. A 200 or
 *             206 adds its $bytes_sent to the session in the shared dictionary
 *             `edge_bytes`. Live and catch-up tokens are skipped: the live relay reports
 *             its own bytes (X-Bytes on its stream-auth calls).
 *   take()    empties the dictionary into {"<session>": bytes} (at most MAX_REPORT
 *             sessions; the rest waits for the next report), and restore() puts a
 *             report back when it couldn't be delivered.
 *   report()  js_periodic on the local edge (worker 0, every 15 s): POST
 *             {"sessions": {...}} to Django's internal /internal/stream-bytes with
 *             X-Edge-Auth. A standalone edge sends the same object as `session_bytes`
 *             in its control poll instead (control.js).
 *
 * Sessions are named by their full 32-hex id only in these reports, to the control
 * plane over the internal network or TLS; nothing here logs.
 */
import token from 'token.js';

const MAX_REPORT = 2000;
const LIVE_RENDITIONS = { live: true, archive: true };

function dict() {
    return typeof ngx !== 'undefined' && ngx.shared ? ngx.shared.edge_bytes : undefined;
}

function secret(name) {
    return typeof edge_secrets !== 'undefined' && typeof edge_secrets[name] === 'string'
        ? edge_secrets[name]
        : '';
}

/* js_set $edge_bytes_note: counts the request's bytes; always "". */
function note(r) {
    var store = dict();
    if (!store || r.variables.edge_verdict !== 'ok') {
        return '';
    }
    var status = r.variables.status;
    if (status !== '200' && status !== '206') {
        return '';
    }
    var t = token.parseToken(r.variables.token || '');
    if (t === null || LIVE_RENDITIONS[t.rendition]) {
        return '';
    }
    var sent = Number(r.variables.bytes_sent) || 0;
    if (sent > 0) {
        try {
            store.incr(t.session, sent, 0);
        } catch (e) {
            /* The zone is full and evicts: an old session's count is lost, never a request. */
        }
    }
    return '';
}

/* What piled up since the last report, removed from the dictionary. */
function take() {
    var store = dict();
    var sessions = {};
    if (!store) {
        return sessions;
    }
    store.keys(MAX_REPORT).forEach(function (session) {
        var sent = store.pop(session);
        if (typeof sent === 'number' && sent > 0) {
            sessions[session] = sent;
        }
    });
    return sessions;
}

/* A report that didn't arrive goes back, added to whatever came meanwhile. */
function restore(sessions) {
    var store = dict();
    if (!store) {
        return;
    }
    Object.keys(sessions).forEach(function (session) {
        try {
            store.incr(session, sessions[session], 0);
        } catch (e) {
            /* full: dropped */
        }
    });
}

function isEmpty(sessions) {
    return Object.keys(sessions).length === 0;
}

/* js_periodic bytes.report (the local edge): to Django's internal endpoint. */
async function report(s) {
    var sessions = take();
    if (isEmpty(sessions)) {
        return;
    }
    var headers = {
        'Content-Type': 'application/json',
        Host: s.variables.edge_bytes_host,
        'X-Edge-Id': s.variables.edge_id,
    };
    var auth = secret('internal_token');
    if (auth) {
        headers['X-Edge-Auth'] = auth;
    }
    try {
        var reply = await ngx.fetch(s.variables.edge_bytes_url, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ sessions: sessions }),
            max_response_body_size: 4096,
        });
        if (reply.status !== 204) {
            restore(sessions);
        }
    } catch (e) {
        restore(sessions);
    }
}

export default { note, take, restore, report, isEmpty };
