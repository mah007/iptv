/*
 * Smart IPTV media edge: a standalone edge's control channel (ADR-0022).
 *
 * Every 5 s (js_periodic, worker 0) it checks whether a poll is due (every 15 s, or at
 * once while the edge has no keys) and then calls the control plane:
 *
 *     POST <EDGE_CONTROL_URL>/edge/v1/poll
 *     Authorization: Bearer <the edge's secret>    X-Edge-Id: <EDGE_ID>
 *     {"kids": [...], "connections": {...}, "tx_bytes": n, "version": "...", "origin_url": "..."}
 *
 * and keeps what comes back in the shared dictionary `edge_ctl`:
 *   keys     the media token key set (current and previous), as the key file holds it
 *   origin   the S3 origin's read-only key for edges (s3.js), or ""
 *   cdn      the secret Bunny sends on its pulls (the /c/ location), or ""
 *   polled   when the last poll succeeded (ms), and `error` when the last one failed
 *
 * token.js reads the keys from there, so a rotation reaches the edge within one poll
 * and without a reload; the control plane waits for every edge to report a new key id
 * before signing with it. A failed poll keeps the last copy (the dictionary is shared
 * memory, gone at a restart: the edge then answers 503 until its first poll succeeds).
 *
 * The secret comes from `js_preload_object edge_control from <file>` ({"token": "..."},
 * written by the entrypoint from the Docker secret). Nothing here logs.
 */
import fs from 'fs';
import token from 'token.js';

const POLL_EVERY_MS = 15000;
const STUB_STATUS = 'http://127.0.0.1:8081/stub_status';

function dict() {
    return typeof ngx !== 'undefined' && ngx.shared ? ngx.shared.edge_ctl : undefined;
}

function loadedKids() {
    var store = dict();
    var raw = store ? store.get('keys') : undefined;
    if (!raw) {
        return [];
    }
    try {
        var keys = JSON.parse(raw);
        var kids = [keys.current.kid];
        if (keys.previous) {
            kids.push(keys.previous.kid);
        }
        return kids;
    } catch (e) {
        return [];
    }
}

/* Bytes sent on every interface but loopback: the edge's egress counter. */
function txBytes() {
    var total = 0;
    try {
        fs.readdirSync('/sys/class/net').forEach(function (name) {
            if (name !== 'lo') {
                var text = fs.readFileSync('/sys/class/net/' + name + '/statistics/tx_bytes');
                total += Number(String(text).trim()) || 0;
            }
        });
    } catch (e) {
        return undefined;
    }
    return total;
}

/* nginx's stub_status page as numbers. */
function parseStubStatus(text) {
    var numbers = (String(text).match(/[0-9]+/g) || []).map(Number);
    if (numbers.length < 7) {
        return {};
    }
    return {
        active: numbers[0],
        accepts: numbers[1],
        handled: numbers[2],
        requests: numbers[3],
        reading: numbers[4],
        writing: numbers[5],
        waiting: numbers[6],
    };
}

async function connections() {
    try {
        var reply = await ngx.fetch(STUB_STATUS, { max_response_body_size: 4096 });
        return reply.status === 200 ? parseStubStatus(await reply.text()) : {};
    } catch (e) {
        return {};
    }
}

function due(store, now) {
    if (!store.get('keys')) {
        return true;
    }
    var last = Number(store.get('attempted') || 0);
    return now - last >= POLL_EVERY_MS;
}

/* Validates the control plane's answer; returns null when it is unusable. */
function accept(answer) {
    if (!answer || typeof answer !== 'object') {
        return null;
    }
    var keys = token.parseKeySet(answer.keys);
    if (keys.error !== null) {
        return null;
    }
    var origin = answer.origin;
    if (origin !== null && origin !== undefined) {
        if (typeof origin.access_key !== 'string' || typeof origin.secret_key !== 'string') {
            return null;
        }
    }
    var cdn = answer.cdn_origin_secret;
    if (cdn !== null && cdn !== undefined && typeof cdn !== 'string') {
        return null;
    }
    return {
        keys: JSON.stringify(answer.keys),
        origin: origin ? JSON.stringify(origin) : '',
        cdn: cdn || '',
    };
}

/* js_periodic control.poll */
async function poll(s) {
    var store = dict();
    var now = Date.now();
    if (!store || !due(store, now) || typeof edge_control === 'undefined') {
        return;
    }
    store.set('attempted', String(now));
    var body = {
        kids: loadedKids(),
        connections: await connections(),
        tx_bytes: txBytes(),
        version: 'nginx/' + s.variables.nginx_version + ' njs/' + njs.version,
        origin_url: s.variables.edge_origin_url || '',
    };
    var headers = {
        Authorization: 'Bearer ' + edge_control.token,
        'X-Edge-Id': s.variables.edge_id,
        'Content-Type': 'application/json',
    };
    if (s.variables.edge_control_host) {
        headers.Host = s.variables.edge_control_host;
    }
    try {
        var reply = await ngx.fetch(s.variables.edge_control_url + '/edge/v1/poll', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(body),
            buffer_size: 16384,
            max_response_body_size: 65536,
        });
        if (reply.status !== 200) {
            store.set('error', 'http_' + reply.status);
            return;
        }
        var accepted = accept(await reply.json());
        if (accepted === null) {
            store.set('error', 'invalid_answer');
            return;
        }
        store.set('keys', accepted.keys);
        store.set('origin', accepted.origin);
        store.set('cdn', accepted.cdn);
        store.set('polled', String(now));
        store.set('error', '');
    } catch (e) {
        store.set('error', 'unreachable');
    }
}

/* js_set $edge_control_bearer: the Authorization header of stream-auth requests. */
function bearer(r) {
    return typeof edge_control === 'undefined' ? '' : 'Bearer ' + edge_control.token;
}

/* js_set $edge_cdn_secret: the secret a CDN pull must present ("" when none is set). */
function cdnSecret(r) {
    if (typeof edge_cdn !== 'undefined' && edge_cdn.secret) {
        return edge_cdn.secret;
    }
    var store = dict();
    return store ? store.get('cdn') || '' : '';
}

export default { poll, bearer, cdnSecret, accept, parseStubStatus };
