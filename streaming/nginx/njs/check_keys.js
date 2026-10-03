/*
 * Validate a media token key file with the edge's own rules, without printing any
 * key material. docker-entrypoint.d/40-edge-config.sh runs it before nginx starts:
 *
 *     njs -q -m -p /etc/nginx/edge/njs /etc/nginx/edge/njs/check_keys.js <key file>
 *
 * Exits non-zero (an uncaught error) when the file is unusable.
 */
import fs from 'fs';
import token from 'token.js';

if (process.argv.length < 3) {
    throw new Error('usage: check_keys.js <key file>');
}

var path = process.argv[process.argv.length - 1];
var text;
try {
    text = fs.readFileSync(path, 'utf8');
} catch (e) {
    throw new Error(path + ': cannot read the key file (' + (e.code || 'error') + ')');
}

var data;
try {
    data = JSON.parse(text);
} catch (e) {
    /* The parser's message can quote the file, secrets included. */
    throw new Error(path + ': not valid JSON');
}

var result = token.parseKeySet(data);
if (result.error !== null) {
    throw new Error(path + ': ' + result.error);
}

console.log('media token keys ok: ' + result.keys.map(function (k) { return k.kid; }).join(', '));
