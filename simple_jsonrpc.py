import json
import time
import uuid
import traceback
from inspect import signature
from urllib.request import Request, urlopen
from flask import Response, request, abort
import logging


FORMAT = u'%(levelname)s: %(filename)s [line: %(lineno)d] [%(asctime)s]: %(message)s'
logging.basicConfig(format=FORMAT, level=logging.DEBUG)


class SimpleJsonRpcError(Exception):
    def __init__(self, message='', code=0):
        Exception.__init__(self, message)
        self.message = message
        self.code = code

    @property
    def json_rpc_format(self):
        error = {
            'code': self.code,
            'message': self.message
        }
        return error

    def __str__(self):
        return '<JsonRpcError with message {} and code {}>'.format(self.message, self.code)


class SimpleJsonRpcServer(object):
    """Class that implements basic JSON-RPC functionality"""
    def __init__(self, app=None, service_url='/json-rpc/', show_api=False):
        self.service_url = service_url
        self.show_api = show_api
        self.urls = {'ping': self.ping}
        if app is not None:
            self.app = app
            self.init_app(self.app)
        else:
            self.app = None

    def init_app(self, app):
        app.add_url_rule(self.service_url, self.service_url, self.prepare_request, methods=['GET', 'POST', 'OPTIONS'])

    def register(self, name):
        def decorator(f):
            self.urls[name] = f
            return f

        return decorator

    def prepare_request(self):
        if request.method == 'GET':
            return self.__return_jrpc_methods_list()

        # If Content-Type doesn't contain application/json or no POST data in request
        content_type = request.headers.get('Content-Type', '')
        if 'application/json' not in content_type or not request.data:
            logging.error('Invalid Content-Type: ' + content_type)
            abort(404)

        items = json.loads(request.data.decode())

        if isinstance(items, list):
            result = []
            for item in items:
                result.append(self.process_request(item))
        else:
            result = self.process_request(items)

        if isinstance(result, Response):
            return result
        else:
            response = Response(json.dumps(result), mimetype='application/json')

        return response

    def process_request(self, items):
        protocol, tid, action, handler_name, result, error = None, None, None, None, None, None
        try:

            args, kwargs = self.__expand_params(items.get('params'))
            handler_name = items.get('method')
            protocol = items.get('version')
            tid = items.get('id')

            if not all([args or kwargs, handler_name, protocol, tid]):
                message = 'JSON-RPC: Invalid request parameters:' + str(items)
                logging.error(message)
                raise SimpleJsonRpcError(message=message, code=-32600)

            if handler_name not in self.urls:
                message = 'JSON-RPC: Method "{}" not found'.format(handler_name)
                logging.error(message)
                raise SimpleJsonRpcError(message=message, code=-32601)

            handler = self.urls[handler_name]
            if hasattr(handler, 'nolog'):
                logging.debug('JSON-RPC: ' + handler_name)
            else:
                logging.debug('JSON-RPC: {} {}'.format(handler_name, kwargs))

            time_start = time.time()
            result = handler(*args, **kwargs)
            time_end = time.time()

            if hasattr(handler, 'nolog'):
                logging.debug('JSON-RPC: {} ({:.2f})'.format(handler_name, (time_end - time_start)))
            else:
                logging.debug('JSON-RPC: {} {} ({:.2f})'.format(handler_name, kwargs, (time_end - time_start)))
        except SimpleJsonRpcError as e:
            error = e
        except Exception:
            message = traceback.format_exc()
            error = SimpleJsonRpcError(message=message, code=-32603)
            logging.error('JSON-RPC: Failed to process request {}: {}'.format(handler_name, message))

        if isinstance(result, Response):
            return result

        result = {
            'protocol': protocol,
            'tid': tid,
            'action': action,
            'method': handler_name,
            'result': result
        }

        if error:
            result['error'] = error.json_rpc_format

        return result

    def ping(self, *args, kwargs):
        return 'pong'

    @staticmethod
    def __expand_params(params):
        if not params:
            return [], {}

        if isinstance(params, (list, tuple)):
            return params, {}
        if isinstance(params, dict):
            return [], params
        else:
            message = 'Invalid method parameters'
            raise SimpleJsonRpcError(message=message, code=-32602)

    def __return_jrpc_methods_list(self):
        if not self.show_api:
            return Response('', content_type='text/plain')

        desc = []
        for n, v in self.urls.items():
            fdesc = n + str(signature(v))
            fdoc = v.__doc__.strip() if v.__doc__ is not None else "No docstring"
            desc.append('{}\n{}\n{}'.format(fdesc, '-' * len(fdesc), fdoc))
        return Response('\n\n'.join(desc), content_type='text/plain; charset=utf-8')


class SimpleJsonRpcClient(object):
    """Class that allows to call the JSON-RPC service"""
    def __init__(self, service_url, service_name=None, version='2.0', headers=None):
        self.version = version
        self.service_url = service_url
        self.service_name = service_name
        self.headers = headers or {'Content-Type': 'application/json'}

    def __getattr__(self, name):
        if self.service_name is not None:
            name = '{}.{}'.format(self.service_name, name)
        params = dict(self.__dict__, service_name=name)
        return self.__class__(params)

    def __repr__(self):
        return json.dumps({
            'version': self.version,
            'method': self.service_name
        })

    def send_request(self, *args, **kwargs):
        params = kwargs if len(kwargs) else args
        params = list(map(lambda s: s.decode("utf-8"), params))
        data = json.dumps({
            'version': self.version,
            'method': self.service_name,
            'params': params,
            'id': str(uuid.uuid4())
        })
        data_binary = data.encode('utf-8')
        url_request = Request(self.service_url, data_binary, headers=self.headers)
        req = urlopen(url_request).read()
        return json.loads(req.read())
