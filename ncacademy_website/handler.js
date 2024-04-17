const next = require('next');
const compat = require('next-aws-lambda');

const app = next({ dev: false });
const handle = app.getRequestHandler();

module.exports.render = (event, context) => {
    app.prepare().then(() => {
        const req = {
            headers: event.headers,
            url: event.path,
            method: event.httpMethod
        };
        const res = {
            statusCode: 200,
            headers: {},
            body: '',
            setHeader(name, value) {
                this.headers[name] = value;
            },
            send(body) {
                this.body = body;
            }
        };
        return compat(page)(req, res);
    });
};
