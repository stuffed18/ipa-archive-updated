#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import urlopen
from base64 import b64decode
import socket
import json


def generatePlist(data: dict) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>items</key><array><dict><key>assets</key><array><dict>
<key>kind</key><string>software-package</string>
<key>url</key><string>{data.get('u')}</string>
</dict><dict>
<key>kind</key><string>display-image</string>
<key>needs-shine</key><false/>
<key>url</key><string>{data.get('i')}</string>
</dict></array><key>metadata</key><dict>
<key>bundle-identifier</key><string>{data.get('b')}</string>
<key>bundle-version</key><string>{data.get('v')}</string>
<key>kind</key><string>software</string>
<key>title</key><string>{data.get('n')}</string>
</dict></dict></array></dict></plist>'''  # noqa: E501


class PlistServer(BaseHTTPRequestHandler):
    def makeHeader(self, contentType):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        if contentType:
            self.send_header('Content-type', contentType)
        self.end_headers()

    def do_GET(self):
        try:
            action, value = self.path.split('?', 1)[-1].split('=', 1)
            if action == 'r':
                # http.client.HTTPResponse
                with urlopen(value) as response:
                    mimeType = response.headers.get('Content-Type')
                    self.makeHeader(mimeType)

                    while True:
                        tmp = response.read(8096)
                        if not tmp:
                            break
                        self.wfile.write(tmp)

            elif action == 'd':
                data = json.loads(b64decode(value + '=='))  # type: dict
                rv = bytes(generatePlist(data), 'utf-8')
                self.makeHeader('application/xml')
                self.wfile.write(rv)

            else:
                return
        except Exception as e:
            print(e)


def getLocalIp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(('10.255.255.255', 80))
    ip = s.getsockname()[0]
    s.close()
    return ip


if __name__ == '__main__':
    webServer = HTTPServer(('0.0.0.0', 8026), PlistServer)
    print('Server started http://%s:%s' % (getLocalIp(), 8026))
    try:
        webServer.serve_forever()
    except KeyboardInterrupt:
        pass
    webServer.server_close()
