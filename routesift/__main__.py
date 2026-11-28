"""Run RouteSift with python -m routesift."""

import logging
import socket
from contextlib import ExitStack

import uvicorn

from routesift.app import app
from routesift.config import BIND_HOST, HOST, LAN_HOST, LAN_PORT, PORT

logging.basicConfig(level=logging.INFO)
if BIND_HOST:
    with ExitStack() as stack:
        binds = [(BIND_HOST, PORT)]
        if LAN_HOST:
            binds.append((BIND_HOST, LAN_PORT))
        listeners = [stack.enter_context(socket.create_server(addr)) for addr in binds]
        if LAN_HOST:
            logging.info(
                "RouteSift · http://%s:%s · http://%s:%s (container ports %s/%s)",
                HOST, PORT, LAN_HOST, PORT, PORT, LAN_PORT,
            )
        else:
            logging.info("RouteSift · http://%s:%s (container port %s)", HOST, PORT, PORT)
        uvicorn.Server(uvicorn.Config(app, access_log=False, proxy_headers=False)).run(sockets=listeners)
elif LAN_HOST:
    with ExitStack() as stack:
        listeners = [stack.enter_context(socket.create_server((address, PORT))) for address in (HOST, LAN_HOST)]
        logging.info("RouteSift · http://%s:%s · http://%s:%s", HOST, PORT, LAN_HOST, PORT)
        uvicorn.Server(uvicorn.Config(app, access_log=False, proxy_headers=False)).run(sockets=listeners)
else:
    uvicorn.run(app, host=HOST, port=PORT, proxy_headers=False, access_log=False)
