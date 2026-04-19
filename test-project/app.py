# Simple test HTTP server for Phase 1 validation
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/healthz':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok'}).encode())

        elif self.path == '/ready':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'ready': True}).encode())

        else:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'message': 'GuardOps test app running',
                'path': self.path
            }).encode())

    # Silence the default request logging to keep output clean
    def log_message(self, format, *args):
        pass

if __name__ == '__main__':
    print('GuardOps test server starting on port 8080...')
    server = HTTPServer(('0.0.0.0', 8080), Handler)
    server.serve_forever()