/*
 * Unit tests for streaming/nginx/njs/s3.js and control.js's pure parts (ADR-0022), run
 * with the njs CLI in the nginx image (streaming/tests/run.sh does this):
 *
 *     njs -q -m -p streaming/nginx/njs s3_test.js sigv4_vectors.json
 *
 * sigv4_vectors.json comes from streaming/tools/s3_presign.py and pins AWS's own
 * published presigned-URL example, so the signature is checked against AWS, not only
 * against our reference.
 */
import fs from 'fs';
import s3 from 's3.js';
import control from 'control.js';

var failures = [];
var checks = 0;

function expect(name, got, want) {
    checks++;
    if (got !== want) {
        failures.push(name + ': got ' + JSON.stringify(got) + ', want ' + JSON.stringify(want));
    }
}

if (process.argv.length < 3) {
    throw new Error('usage: s3_test.js <sigv4_vectors.json>');
}
var vectors = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

vectors.presign.forEach(function (c) {
    var query = s3.presign(c.host, c.path, vectors.access_key, vectors.secret_key, c.region,
                           c.amz_date, c.expires);
    expect('presign ' + c.host + c.path, query, c.query);
    if (c.expect_signature) {
        expect('AWS example signature', query.split('X-Amz-Signature=')[1], c.expect_signature);
    }
});

vectors.map.forEach(function (c) {
    expect('map ' + c.rendition + ' ' + c.tail, s3.map(c.title, c.rendition, c.tail), c.object);
});

expect('uri encoding keeps unreserved characters', s3.uriEncode('/a-b_c.d~e/f g'), '/a-b_c.d~e/f%20g');
expect('uri encoding of sub-delims', s3.uriEncode("/x!'()*"), '/x%21%27%28%29%2A');

var status = control.parseStubStatus(
    'Active connections: 3 \nserver accepts handled requests\n 10 10 42 \n' +
    'Reading: 0 Writing: 1 Waiting: 2 \n');
expect('stub_status active', status.active, 3);
expect('stub_status requests', status.requests, 42);
expect('stub_status waiting', status.waiting, 2);
expect('a short stub_status is ignored', JSON.stringify(control.parseStubStatus('Active: 1')), '{}');

var keys = {
    current: { kid: 'k2', secret: Buffer.from(new Array(32).fill(3)).toString('base64url') },
    previous: null,
};
var accepted = control.accept({ keys: keys, origin: { access_key: 'a', secret_key: 'b' }, cdn_origin_secret: null });
expect('a valid answer is accepted', accepted !== null, true);
expect('the origin key is kept as JSON', accepted ? JSON.parse(accepted.origin).access_key : '', 'a');
expect('an answer with bad keys is refused', control.accept({ keys: { current: { kid: 'k', secret: 'x' } } }), null);
expect('an answer with a bad origin is refused', control.accept({ keys: keys, origin: { access_key: 1 } }), null);
expect('a non-object answer is refused', control.accept('nope'), null);

if (failures.length > 0) {
    failures.forEach(function (f) { console.log('  FAIL  ' + f); });
    throw new Error(failures.length + ' of ' + checks + ' checks failed');
}
console.log('njs s3.js and control.js: ' + checks + ' checks passed');
