/*
 * Unit tests for streaming/nginx/njs/token.js, run with the njs CLI that ships in the
 * nginx image (streaming/tests/run.sh does this):
 *
 *     njs -q -m -p streaming/nginx/njs token_test.js vectors.json [fuzz.json]
 *
 * vectors.json holds the ADR-0007 vectors; fuzz.json (written by edge_test.py
 * prepare) holds random tokens and addresses with the Python reference's verdicts.
 * Every verdict must match the reference exactly.
 */
import fs from 'fs';
import token from 'token.js';

var failures = [];
var checks = 0;

function expect(name, got, want) {
    checks++;
    if (got !== want) {
        failures.push(name + ': got ' + JSON.stringify(got) + ', want ' + JSON.stringify(want));
    }
}

function load(path) {
    return JSON.parse(fs.readFileSync(path, 'utf8'));
}

function runCases(label, keys, now, cases) {
    cases.forEach(function (c) {
        var name = label + ' "' + c.name + '"';
        expect(name, token.check(keys, c.token, c.tail, c.client_ip, now), c.verdict);
        if (c.verdict === 'ok' && c.claims) {
            var t = token.parseToken(c.token);
            expect(name + ' claims', JSON.stringify([t.kid, t.session, t.title, t.rendition,
                                                     t.exp, t.net]),
                   JSON.stringify([c.claims.kid, c.claims.session, c.claims.title,
                                   c.claims.rendition, c.claims.exp, c.claims.net]));
        }
    });
}

function runNets(label, nets) {
    nets.forEach(function (n) {
        expect(label + ' net of ' + JSON.stringify(n.address), token.clientNet(n.address), n.net);
    });
}

function secretOf(bytes) {
    return Buffer.from(bytes).toString('base64url');
}

function keySetCases(good) {
    var cur = good.current;
    var prev = good.previous;
    var alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
    var last = cur.secret[cur.secret.length - 1];
    var sameBytes = cur.secret.slice(0, -1) + alphabet[alphabet.indexOf(last) ^ 1];
    var rejected = [
        ['an array', []],
        ['null', null],
        ['missing current', { previous: prev }],
        ['an unknown field', { current: cur, next: prev }],
        ['an extra key field', { current: { kid: 'k1', secret: cur.secret, note: 'x' } }],
        ['a kid with a dot', { current: { kid: 'k.1', secret: cur.secret } }],
        ['a 17-character kid', { current: { kid: 'k'.repeat(17), secret: cur.secret } }],
        ['a numeric secret', { current: { kid: 'k1', secret: 42 } }],
        ['a 31-byte secret', { current: { kid: 'k1', secret: secretOf(new Array(31).fill(7)) } }],
        ['a 65-byte secret', { current: { kid: 'k1', secret: secretOf(new Array(65).fill(7)) } }],
        ['a padded secret', { current: { kid: 'k1', secret: cur.secret + '=' } }],
        ['a non-canonical secret', { current: { kid: 'k1', secret: sameBytes } }],
        ['a secret with a bad character', { current: { kid: 'k1', secret: cur.secret.slice(0, -1) + '*' } }],
        ['duplicate kids', { current: cur, previous: { kid: cur.kid, secret: prev.secret } }],
        ['an invalid previous key', { current: cur, previous: { kid: 'k0' } }],
    ];
    rejected.forEach(function (c) {
        var result = token.parseKeySet(c[1]);
        expect('key set with ' + c[0] + ' is rejected', result.keys === null && typeof result.error === 'string', true);
        if (typeof result.error === 'string') {
            expect('key set error for ' + c[0] + ' hides the secret', result.error.indexOf(cur.secret.slice(0, 12)), -1);
        }
    });
    var accepted = [
        ['current and previous', good, 'k1,k0'],
        ['previous null', { current: cur, previous: null }, 'k1'],
        ['no previous', { current: cur }, 'k1'],
        ['a 64-byte secret', { current: { kid: 'k1', secret: secretOf(new Array(64).fill(9)) } }, 'k1'],
    ];
    accepted.forEach(function (c) {
        var result = token.parseKeySet(c[1]);
        expect('key set with ' + c[0] + ' is accepted', result.error, null);
        expect('key set with ' + c[0] + ' kids', result.keys ? result.keys.map(function (k) { return k.kid; }).join(',') : '', c[2]);
    });
}

if (process.argv.length < 3) {
    throw new Error('usage: token_test.js <vectors.json> [<fuzz.json>]');
}
var vectors = load(process.argv[2]);
var keys = token.parseKeySet(vectors.keys);
if (keys.error !== null) {
    throw new Error('vector keys rejected: ' + keys.error);
}

expect('clock skew matches the reference', token.CLOCK_SKEW_S, vectors.clock_skew_s);
expect('max lifetime matches the reference', token.MAX_TTL_S, vectors.max_ttl_s);
runCases('vector', keys.keys, vectors.now, vectors.cases);
runNets('vector', vectors.client_nets);
keySetCases(vectors.keys);

if (process.argv.length > 3) {
    var fuzz = load(process.argv[3]);
    var fuzzKeys = token.parseKeySet(fuzz.keys);
    if (fuzzKeys.error !== null) {
        throw new Error('fuzz keys rejected: ' + fuzzKeys.error);
    }
    runCases('fuzz', fuzzKeys.keys, fuzz.now, fuzz.cases);
    runNets('fuzz', fuzz.nets);
}

if (failures.length > 0) {
    throw new Error(failures.length + ' of ' + checks + ' njs checks failed:\n  '
                    + failures.slice(0, 40).join('\n  '));
}
console.log('njs token.js: ' + checks + ' checks passed');
