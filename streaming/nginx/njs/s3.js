/*
 * Smart IPTV media edge: the S3 origin mode's object mapping and presigned reads
 * (ADR-0022). streaming/tools/s3_presign.py is the reference; both must pass
 * streaming/tests/sigv4_vectors.json (which pins AWS's own published example).
 *
 * Object storage has no symlinks, so the publisher (backend apps.media.publisher)
 * stores each HLS rung once, as hls/v<n>-h<box>/, and capped presentations' masters
 * name rungs that way. map() turns a verified token's tail into the object, and refuses
 * (null: the "scope" verdict) a rung taller than the presentation's ceiling, so a 720p
 * token still can't reach a 1080p rung:
 *
 *     compat.mp4                      <title>/compat.mp4
 *     <scope>/subs|thumbs/<f>         <title>/subs|thumbs/<f>
 *     hls/<rest>                      <title>/hls/<rest>
 *     hls720/master.m3u8              <title>/hls720/master.m3u8
 *     hls720/a<n>/<f>                 <title>/hls/a<n>/<f>
 *     hls720/v<n>-h<H>/<f>            <title>/hls/v<n>-h<H>/<f>   only when H <= 720
 *     hls2160/uhd/<f>                 <title>/hls2160/uhd/<f>
 *
 * Every read is signed with the edges' read-only key (AWS Signature Version 4, a
 * presigned query: one nocache variable per slice subrequest, so a long progressive
 * response keeps getting fresh dates). The key comes from a preloaded file
 * (`js_preload_object edge_origin from <file>`) or from the control channel's shared
 * dictionary (control.js). Without a key the request goes unsigned, for origins that
 * authorise the edge by network. Nothing here logs.
 */
import crypto from 'crypto';

const UNSIGNED = 'UNSIGNED-PAYLOAD';
const PRESIGN_EXPIRES = 900;
const CAPPED = /^hls([0-9]{3,4})$/;
const RUNG = /^v[0-9]+-h([0-9]{1,4})$/;
const AUDIO = /^a[0-9]+$/;
const SHARED = { subs: true, thumbs: true };

/* The object for a title, a rendition and an in-scope tail; null past the ceiling. */
function map(title, rendition, tail) {
    if (tail.indexOf('/') === -1) {
        return title + '/' + tail;
    }
    var rest = tail.slice(rendition.length + 1);
    var cut = rest.indexOf('/');
    var first = cut === -1 ? rest : rest.slice(0, cut);
    var after = cut === -1 ? '' : rest.slice(cut + 1);
    if (SHARED[first] === true && after !== '') {
        return title + '/' + first + '/' + after;
    }
    if (rendition === 'hls') {
        return title + '/hls/' + rest;
    }
    var capped = CAPPED.exec(rendition);
    if (capped === null) {
        return title + '/' + tail;
    }
    var ceiling = Number(capped[1]);
    if (rest === 'master.m3u8') {
        return title + '/' + rendition + '/master.m3u8';
    }
    if (rendition === 'hls2160' && first === 'uhd' && after !== '') {
        return title + '/hls2160/uhd/' + after;
    }
    if (AUDIO.test(first) && after !== '') {
        return title + '/hls/' + first + '/' + after;
    }
    var rung = RUNG.exec(first);
    if (rung !== null && after !== '' && Number(rung[1]) <= ceiling) {
        return title + '/hls/' + first + '/' + after;
    }
    return null;
}

/* RFC 3986 percent-encoding of one segment, as S3's canonical URI wants it. */
function encodeSegment(segment) {
    return encodeURIComponent(segment).replace(/[!'()*]/g, function (c) {
        return '%' + c.charCodeAt(0).toString(16).toUpperCase();
    });
}

function uriEncode(path) {
    return path.split('/').map(encodeSegment).join('/');
}

function hmac(key, text) {
    return crypto.createHmac('sha256', key).update(text).digest();
}

function sha256hex(text) {
    return crypto.createHash('sha256').update(text).digest('hex');
}

/* The presigned query (X-Amz-* and the signature) for a GET of `path` on `host`. */
function presign(host, path, accessKey, secretKey, region, amzDate, expires) {
    var date = amzDate.slice(0, 8);
    var scope = date + '/' + region + '/s3/aws4_request';
    var query = 'X-Amz-Algorithm=AWS4-HMAC-SHA256'
        + '&X-Amz-Credential=' + encodeSegment(accessKey + '/' + scope)
        + '&X-Amz-Date=' + amzDate
        + '&X-Amz-Expires=' + expires
        + '&X-Amz-SignedHeaders=host';
    var canonical = ['GET', uriEncode(path), query, 'host:' + host + '\n', 'host', UNSIGNED]
        .join('\n');
    var toSign = ['AWS4-HMAC-SHA256', amzDate, scope, sha256hex(canonical)].join('\n');
    var key = hmac('AWS4' + secretKey, date);
    key = hmac(key, region);
    key = hmac(key, 's3');
    key = hmac(key, 'aws4_request');
    var signature = crypto.createHmac('sha256', key).update(toSign).digest('hex');
    return query + '&X-Amz-Signature=' + signature;
}

function amzNow() {
    return new Date().toISOString().replace(/[-:]/g, '').replace(/\.[0-9]+Z$/, 'Z');
}

/* The edges' read-only key: a preloaded file, else the control channel's copy. */
function credentials() {
    if (typeof edge_origin !== 'undefined') {
        return edge_origin;
    }
    var dict = typeof ngx !== 'undefined' && ngx.shared && ngx.shared.edge_ctl;
    if (dict) {
        var raw = dict.get('origin');
        if (raw) {
            try {
                return JSON.parse(raw);
            } catch (e) {
                return null;
            }
        }
    }
    return null;
}

/* js_set $edge_s3_query ... nocache: "?<presigned query>", or "" without a key. */
function presignQuery(r) {
    var creds = credentials();
    if (creds === null || !creds.access_key || !creds.secret_key) {
        return '';
    }
    var path = r.variables.edge_origin_path;
    if (!path) {
        return '';
    }
    return '?' + presign(r.variables.edge_origin_authority, path, creds.access_key,
                         creds.secret_key, creds.region || 'us-east-1', amzNow(),
                         PRESIGN_EXPIRES);
}

export default { map, presign, presignQuery, uriEncode, credentials };
