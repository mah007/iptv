/*
 * Smart IPTV media edge: media token verification (ADR-0007; SPEC §7.4, §11, §12).
 *
 *     kid.session.title.rendition.exp[.net].sig
 *
 * sig = unpadded base64url(HMAC-SHA256(secret[kid], <token bytes before the last '.'>)).
 * streaming/tools/sign_token.py is the reference implementation; both must pass
 * streaming/tests/vectors.json.
 *
 * Keys come from the key file that nginx preloads at configuration time
 * (`js_preload_object edge_keys from <file>`), so workers never open it.
 *
 * This module never logs anything: every error line nginx writes while serving a
 * request ends with the raw request line, and that line holds the token.
 *
 * Variables (nginx.conf):
 *   $edge_verdict   verify()   "ok", a verdict code below, or "" outside /v/
 *   $edge_object    object()   "<title>/<tail>", the path relative to the storage root
 *   $edge_auth_key  authKey()  SHA-256 of the token, the key of the stream-auth cache
 *   $edge_log_*     log*()     what the access log carries instead of the token
 *
 * Header filters: mediaHeaders (Cache-Control, Accept-Ranges) and originHeaders.
 *
 * Verdicts: ok, malformed, bad_path, unknown_kid, bad_signature, expired, exp_too_far,
 * ip_mismatch, scope, no_keys.
 */
import crypto from 'crypto';

const CLOCK_SKEW_S = 30;
const MAX_TTL_S = 7 * 24 * 3600;
const MAX_TOKEN_LEN = 256;
const MAX_TAIL_SEGMENTS = 8;
const SESSION_LOG_PREFIX = 16;
const SECRET_MIN_BYTES = 32;
const SECRET_MAX_BYTES = 64;

const KID = /^[A-Za-z0-9_-]{1,16}$/;
const SESSION = /^[0-9a-f]{32}$/;
const TITLE = /^[A-Za-z0-9_-]{1,64}$/;
const RENDITION = /^[A-Za-z0-9_-]{1,32}$/;
const EXP = /^[1-9][0-9]{0,11}$/;
const NET = /^(?:[0-9a-f]{6}|[0-9a-f]{16})$/;
const SIG = /^[A-Za-z0-9_-]{43}$/;
const SECRET = /^[A-Za-z0-9_-]{43,86}$/;
const SEGMENT = /^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$/;
const LEAF = /\.[A-Za-z0-9]{1,8}$/;
const EXTENSION = /^[A-Za-z0-9]{1,8}$/;
const OCTET = '(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])';
const IPV4 = new RegExp('^' + OCTET + '\\.' + OCTET + '\\.' + OCTET + '\\.' + OCTET + '$');
const HEXTET = /^[0-9A-Fa-f]{1,4}$/;
const XTREAM_PATH = /^\/(movie|series|live|timeshift)\/[^/]+\/[^/]+(\/.*)?$/i;

function own(obj, name) {
    return Object.prototype.hasOwnProperty.call(obj, name);
}

function isObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/* Grammar only: returns the fields, the signed payload and the signature, or null. */
function parseToken(token) {
    if (typeof token !== 'string' || token.length === 0 || token.length > MAX_TOKEN_LEN) {
        return null;
    }
    var parts = token.split('.');
    if (parts.length !== 6 && parts.length !== 7) {
        return null;
    }
    var net = parts.length === 7 ? parts[5] : null;
    var sig = parts[parts.length - 1];
    if (!KID.test(parts[0]) || !SESSION.test(parts[1]) || !TITLE.test(parts[2])
        || !RENDITION.test(parts[3]) || !EXP.test(parts[4])
        || (net !== null && !NET.test(net)) || !SIG.test(sig))
    {
        return null;
    }
    return {
        kid: parts[0],
        session: parts[1],
        title: parts[2],
        rendition: parts[3],
        exp: parseInt(parts[4], 10),
        net: net,
        sig: sig,
        payload: token.slice(0, token.length - sig.length - 1),
    };
}

/* Canonical unpadded base64url of 32-64 bytes, or null. Buffer.from() alone is
 * lenient (it stops at the first bad character), hence the regex and round trip. */
function decodeSecret(text) {
    if (typeof text !== 'string' || !SECRET.test(text)) {
        return null;
    }
    var raw = Buffer.from(text, 'base64url');
    if (raw.length < SECRET_MIN_BYTES || raw.length > SECRET_MAX_BYTES
        || raw.toString('base64url') !== text)
    {
        return null;
    }
    return raw;
}

function parseKey(entry, where) {
    if (!isObject(entry) || Object.keys(entry).length !== 2 || !own(entry, 'kid')
        || !own(entry, 'secret'))
    {
        return where + ' must be an object with exactly "kid" and "secret"';
    }
    if (typeof entry.kid !== 'string' || !KID.test(entry.kid)) {
        return where + '.kid must match [A-Za-z0-9_-]{1,16}';
    }
    var secret = decodeSecret(entry.secret);
    if (secret === null) {
        return where + '.secret must be canonical unpadded base64url of '
            + SECRET_MIN_BYTES + '-' + SECRET_MAX_BYTES + ' bytes';
    }
    return { kid: entry.kid, secret: secret };
}

/* {"current": {kid, secret}, "previous": {kid, secret} | null} -> {keys, error}.
 * Error messages name fields, never values. */
function parseKeySet(obj) {
    var fail = function (error) {
        return { keys: null, error: error };
    };
    if (!isObject(obj)) {
        return fail('key file must hold a JSON object');
    }
    var unknown = Object.keys(obj).filter(function (name) {
        return name !== 'current' && name !== 'previous';
    }).sort();
    if (unknown.length > 0) {
        return fail('unknown fields: ' + unknown.join(', '));
    }
    if (!own(obj, 'current')) {
        return fail('"current" is required');
    }
    var current = parseKey(obj.current, 'current');
    if (typeof current === 'string') {
        return fail(current);
    }
    var keys = [current];
    if (own(obj, 'previous') && obj.previous !== null) {
        var previous = parseKey(obj.previous, 'previous');
        if (typeof previous === 'string') {
            return fail(previous);
        }
        if (previous.kid === current.kid) {
            return fail('current and previous must have different kids');
        }
        keys.push(previous);
    }
    return { keys: keys, error: null };
}

function findKey(keys, kid) {
    for (var i = 0; i < keys.length; i++) {
        if (keys[i].kid === kid) {
            return keys[i];
        }
    }
    return null;
}

function mac(secret, payload) {
    return crypto.createHmac('sha256', secret).update(payload).digest('base64url');
}

/* Constant-time comparison of two ASCII strings (lengths are public). */
function safeEqual(a, b) {
    if (a.length !== b.length) {
        return false;
    }
    var diff = 0;
    for (var i = 0; i < a.length; i++) {
        diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
    }
    return diff === 0;
}

function hex(value, width) {
    var text = value.toString(16);
    while (text.length < width) {
        text = '0' + text;
    }
    return text;
}

function ipv4Octets(text) {
    var m = IPV4.exec(text);
    return m ? [+m[1], +m[2], +m[3], +m[4]] : null;
}

/* Eight 16-bit groups of an IPv6 address in any RFC 4291 text form, or null. */
function ipv6Groups(text) {
    if (text.length > 45 || !/^[0-9A-Fa-f:.]+$/.test(text)) {
        return null;
    }
    if (text.indexOf('.') !== -1) {
        var colon = text.lastIndexOf(':');
        var octets = colon === -1 ? null : ipv4Octets(text.slice(colon + 1));
        if (octets === null) {
            return null;
        }
        text = text.slice(0, colon + 1) + hex((octets[0] << 8) | octets[1], 1) + ':'
            + hex((octets[2] << 8) | octets[3], 1);
    }
    var halves = text.split('::');
    if (halves.length > 2) {
        return null;
    }
    var head = halves[0] === '' ? [] : halves[0].split(':');
    var tail = halves.length === 1 || halves[1] === '' ? [] : halves[1].split(':');
    var fill = 8 - head.length - tail.length;
    if (halves.length === 2 ? fill < 1 : fill !== 0) {
        return null;
    }
    var groups = [];
    var all = head.concat(tail);
    for (var i = 0; i < all.length; i++) {
        if (!HEXTET.test(all[i])) {
            return null;
        }
        if (i === head.length) {
            for (var z = 0; z < fill; z++) {
                groups.push(0);
            }
        }
        groups.push(parseInt(all[i], 16));
    }
    while (groups.length < 8) {
        groups.push(0);
    }
    return groups;
}

/* The `net` binding for a client address: IPv4 /24 as 6 hex digits, IPv6 /64 as 16.
 * IPv4-mapped IPv6 counts as IPv4. Null for anything that is not an address. */
function clientNet(addr) {
    if (typeof addr !== 'string') {
        return null;
    }
    if (addr.indexOf(':') === -1) {
        var o = ipv4Octets(addr);
        return o ? hex(o[0], 2) + hex(o[1], 2) + hex(o[2], 2) : null;
    }
    var g = ipv6Groups(addr);
    if (g === null) {
        return null;
    }
    if (g[0] === 0 && g[1] === 0 && g[2] === 0 && g[3] === 0 && g[4] === 0
        && g[5] === 0xffff)
    {
        return hex(g[6] >> 8, 2) + hex(g[6] & 0xff, 2) + hex(g[7] >> 8, 2);
    }
    return hex(g[0], 4) + hex(g[1], 4) + hex(g[2], 4) + hex(g[3], 4);
}

/* The path after the token: 1-8 safe segments, the last naming a file. */
function validTail(tail) {
    if (typeof tail !== 'string') {
        return false;
    }
    var segments = tail.split('/');
    if (segments.length > MAX_TAIL_SEGMENTS) {
        return false;
    }
    for (var i = 0; i < segments.length; i++) {
        if (!SEGMENT.test(segments[i])) {
            return false;
        }
    }
    return LEAF.test(segments[segments.length - 1]);
}

/* A token for rendition R reaches the file R.<ext> or anything under R/. */
function inScope(rendition, tail) {
    if (tail.indexOf('/') === -1) {
        return tail.slice(0, rendition.length + 1) === rendition + '.'
            && EXTENSION.test(tail.slice(rendition.length + 1));
    }
    return tail.slice(0, rendition.length + 1) === rendition + '/';
}

/* The whole check, in the order ADR-0007 pins (and sign_token.verify follows). */
function check(keys, token, tail, addr, now) {
    var t = parseToken(token);
    if (t === null) {
        return 'malformed';
    }
    if (!validTail(tail)) {
        return 'bad_path';
    }
    var key = findKey(keys, t.kid);
    if (key === null) {
        return 'unknown_kid';
    }
    if (!safeEqual(mac(key.secret, t.payload), t.sig)) {
        return 'bad_signature';
    }
    if (now >= t.exp + CLOCK_SKEW_S) {
        return 'expired';
    }
    if (t.exp > now + MAX_TTL_S) {
        return 'exp_too_far';
    }
    if (t.net !== null && clientNet(addr) !== t.net) {
        return 'ip_mismatch';
    }
    if (!inScope(t.rendition, tail)) {
        return 'scope';
    }
    return 'ok';
}

/* Module globals live for one request with the njs engine (js_engine njs). */
var keyCache = null;
var parseCache = { token: null, value: null };

function keySet() {
    if (keyCache === null) {
        keyCache = typeof edge_keys === 'undefined'
            ? { keys: null, error: 'no key file preloaded' }
            : parseKeySet(edge_keys);
    }
    return keyCache;
}

function parsed(token) {
    if (parseCache.token !== token) {
        parseCache = { token: token, value: parseToken(token) };
    }
    return parseCache.value;
}

/* The token as the client sent it: the location capture, or the first segment after
 * /v/ when the strict location did not match. Undefined outside /v/. */
function requestToken(r) {
    if (r.variables.token) {
        return r.variables.token;
    }
    if (r.uri.slice(0, 3) !== '/v/') {
        return undefined;
    }
    var rest = r.uri.slice(3);
    var cut = rest.indexOf('/');
    return cut === -1 ? rest : rest.slice(0, cut);
}

/* js_set $edge_verdict */
function verify(r) {
    var token = r.variables.token;
    if (!token) {
        return r.uri.slice(0, 3) === '/v/' ? 'malformed' : '';
    }
    var ks = keySet();
    if (ks.error !== null) {
        return 'no_keys';
    }
    return check(ks.keys, token, r.variables.tail, r.variables.remote_addr,
                 Math.floor(Date.now() / 1000));
}

/* js_set $edge_object: only used once $edge_verdict is "ok". */
function object(r) {
    var t = parsed(r.variables.token);
    var tail = r.variables.tail;
    return t !== null && validTail(tail) ? t.title + '/' + tail : '';
}

/* js_set $edge_auth_key: the cache stores this hash, never the token. */
function authKey(r) {
    var token = r.variables.token;
    return token ? crypto.createHash('sha256').update(token).digest('hex') : '';
}

/* js_set $edge_log_path: the request path with the token replaced by its session
 * prefix and Xtream credentials masked. Paths of any other shape could carry
 * credentials (e.g. /<user>/<pass>/<id>), so they are not logged. */
function logPath(r) {
    var uri = r.uri;
    if (uri.slice(0, 3) === '/v/') {
        var rest = uri.slice(3);
        var cut = rest.indexOf('/');
        var t = parsed(cut === -1 ? rest : rest.slice(0, cut));
        var tail = cut === -1 ? '' : rest.slice(cut + 1);
        return '/v/' + (t !== null ? t.session.slice(0, SESSION_LOG_PREFIX) : '-')
            + (tail === '' ? '' : validTail(tail) ? '/' + tail : '/...');
    }
    var m = XTREAM_PATH.exec(uri);
    if (m) {
        return '/' + m[1].toLowerCase() + '/***/***' + (m[2] || '');
    }
    return uri === '/healthz' ? uri : '/***';
}

function logField(r, name) {
    var t = parsed(requestToken(r));
    if (t === null) {
        return '';
    }
    return name === 'session' ? t.session.slice(0, SESSION_LOG_PREFIX) : t[name];
}

function logSession(r) {
    return logField(r, 'session');
}

function logKid(r) {
    return logField(r, 'kid');
}

function logTitle(r) {
    return logField(r, 'title');
}

function logRendition(r) {
    return logField(r, 'rendition');
}

/* One byte range ("bytes=0-99", "bytes=100-", "bytes=-500"): nginx answers it with a
 * 206 that, unlike its 200, carries no Accept-Ranges of its own. */
const SINGLE_RANGE = /^bytes=(?:[0-9]+-[0-9]*|-[0-9]+)$/;
const PLAYLIST = /\.m3u8$/;
/* Live TV (ADR-0017): what the relay streams, and the playlists that change. */
const LIVE_STREAM = /^(?:live\.ts|archive\/[0-9]+-[0-9]+\.ts)$/;
const LIVE_PLAYLIST = /\.m3u8$/;

/* Cache-Control for a live or catch-up token's response; null for every other token. */
function liveCaching(r) {
    var t = parsed(r.variables.token);
    var tail = r.variables.tail || '';
    if (t === null || (t.rendition !== 'live' && t.rendition !== 'archive')) {
        return null;
    }
    if (LIVE_STREAM.test(tail)) {
        return 'no-store';
    }
    if (LIVE_PLAYLIST.test(tail)) {
        return 'no-cache';
    }
    return t.rendition === 'live' ? 'public, max-age=120' : 'public, max-age=86400';
}

/* js_header_filter for the token locations (edge.conf; edge-s3.conf through
 * originHeaders). This filter runs first in nginx's header filter chain, ahead of
 * the slice, range and not-modified filters: a local file shows status 200 here even
 * when the client gets a 206, 304 or 416, and in S3 mode the status is the origin's
 * 206 for the first slice. nginx runs the chain a second time when the range filter
 * turns the response into a 416 error page; assignments replace earlier values, so
 * that pass leaves one Cache-Control (no-store), where add_header would append a
 * second one with the first pass's value.
 *
 *   playlists (.m3u8)       max-age=60
 *   everything else         public, max-age=31536000, immutable (renditions never change)
 *   any other status        no-store
 *
 * Live and catch-up tokens (rendition `live` or `archive`, ADR-0017):
 *   live.ts, archive/<w>.ts no-store (one continuous stream from the relay)
 *   playlists               no-cache (they change every segment)
 *   live segments           public, max-age=120 (gone from disk a minute later)
 *   archive segments        public, max-age=86400
 *
 * Accept-Ranges: nginx adds it to full 200 answers only; a single-range request
 * (answered 206) gets it here.
 */
function mediaHeaders(r) {
    var status = r.status;
    if (status === 200 || status === 206 || status === 304) {
        var live = liveCaching(r);
        r.headersOut['Cache-Control'] = live !== null
            ? live
            : PLAYLIST.test(r.uri)
                ? 'max-age=60'
                : 'public, max-age=31536000, immutable';
        if (status !== 304 && SINGLE_RANGE.test(r.headersIn.Range || '')) {
            r.headersOut['Accept-Ranges'] = 'bytes';
        }
    } else {
        r.headersOut['Cache-Control'] = 'no-store';
    }
}

/* Origin response headers a client may see in S3 mode. Everything else (x-amz-*,
 * object metadata, the origin's caching and CORS headers) stays at the edge. */
const ORIGIN_HEADERS = {
    'content-type': true,
    'content-length': true,
    'content-range': true,
    etag: true,
    'last-modified': true,
};

/* Content types by extension, as in media.conf's types block: in S3 mode they replace
 * whatever object metadata the bucket holds. */
const CONTENT_TYPES = {
    m3u8: 'application/vnd.apple.mpegurl',
    mp4: 'video/mp4',
    m4v: 'video/mp4',
    m4s: 'video/mp4',
    m4a: 'audio/mp4',
    ts: 'video/mp2t',
    aac: 'audio/aac',
    vtt: 'text/vtt',
    jpg: 'image/jpeg',
    jpeg: 'image/jpeg',
    png: 'image/png',
    webp: 'image/webp',
    avif: 'image/avif',
    webm: 'video/webm',
    mkv: 'video/x-matroska',
};

/* js_header_filter (edge-s3.conf): drop the origin's headers, type the response by
 * its extension, then mediaHeaders. add_header runs after this filter, so the edge's
 * own headers survive. */
function originHeaders(r) {
    Object.keys(r.headersOut).forEach(function (name) {
        if (!own(ORIGIN_HEADERS, name.toLowerCase())) {
            delete r.headersOut[name];
        }
    });
    var dot = r.uri.lastIndexOf('.');
    var ext = dot === -1 ? '' : r.uri.slice(dot + 1).toLowerCase();
    if (r.status < 300) {
        r.headersOut['Content-Type'] = own(CONTENT_TYPES, ext)
            ? CONTENT_TYPES[ext]
            : 'application/octet-stream';
    }
    mediaHeaders(r);
}

/* js_content for /healthz: the edge is healthy when its key file is usable. */
function health(r) {
    if (keySet().error !== null) {
        r.return(503, 'unavailable\n');
        return;
    }
    r.return(200, 'ok\n');
}

export default {
    verify,
    object,
    authKey,
    logPath,
    logSession,
    logKid,
    logTitle,
    logRendition,
    mediaHeaders,
    originHeaders,
    health,
    /* Pure functions for check_keys.js and streaming/tests/njs/token_test.js. */
    parseToken,
    parseKeySet,
    check,
    clientNet,
    validTail,
    inScope,
    mac,
    CLOCK_SKEW_S,
    MAX_TTL_S,
};
