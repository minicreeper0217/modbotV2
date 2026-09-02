from discord.ext import commands
import asyncio
import base64
import json
import logging
import secrets
import uuid
from aiohttp import web
from enum import IntEnum
import passkeys as psk
import sqlite3
import config


class Opcode(IntEnum):
  DISPATCH = 0
  HEARTBEAT = 1

  HELLO = 10
  HEARTBEAT_ACK = 11

  AUTHENTICATE = 20
  AUTHENTICATION_CHALLENGE = 21
  AUTHENTICATION_RESPONSE = 22

HEARTBEAT_INTERVAL = 42500

class GatewayState:
	CONNECTED = "connected"
	AUTHENTICATING = "authenticating"
	AUTHENTICATED = "authenticated"

class GatewayConnection:

	def __init__(self, ws: web.WebSocketResponse):
		self.ws = ws

		self.state = GatewayState.CONNECTED

		self.session_id = None
		self.resume_url = None

		self.seq = 0

		self.user_id = None

	async def send(
		self,
		op: int,
		data=None,
		*,
		seq=None,
		event=None,
	):
		packet = {
			"op": op,
			"d": data,
		}

		if seq is not None:
			packet["s"] = seq

		if event is not None:
			packet["t"] = event

		await self.ws.send_str(
			json.dumps(packet)
		)

	async def send_hello(self):
		await self.send(
			Opcode.HELLO,
			{
				"heartbeat_interval":
					HEARTBEAT_INTERVAL,
			},
		)

	async def dispatch(
		self,
		event: str,
		data,
	):
		self.seq += 1

		await self.send(
			Opcode.DISPATCH,
			data,
			seq=self.seq,
			event=event,
		)

	async def heartbeat(self, client_seq):
		logging.debug(
			"Heartbeat received: seq=%r",
			client_seq,
		)

		await self.send(
			Opcode.HEARTBEAT_ACK
		)

	async def begin_authentication(self):
		if self.state != GatewayState.CONNECTED:
			return

		self.state = GatewayState.AUTHENTICATING

		with sqlite3.connect(config.dir / 'database' / 'webapp.db') as db:
			challage = psk.get_login_challage(db=db)

		await self.send(
			Opcode.AUTHENTICATION_CHALLENGE,
			{
				"publicKey": challage
			},
		)

	async def authenticate(self, assertion):
		if self.state != GatewayState.AUTHENTICATING:
			return False

		try:
			with sqlite3.connect(config.dir / 'database' / 'webapp.db') as db:
				await psk.validate_login(credential=assertion, db=db)
		except psk.PasskeyError as e:
			logging.warning(f"WebAuthn authentication failed | {e}")
			return False

		self.user_id = "White Fox"

		self.authentication_challenge = None

		self.state = GatewayState.AUTHENTICATED

		self.session_id = uuid.uuid4().hex

		self.resume_url = f"wss://{config.domain}/api/ws"

		await self.dispatch(
			"READY",
			{
				"session_id": self.session_id,
				"resume_url": self.resume_url,
			},
		)

		return True

class WS_handler(commands.Cog):
	def __init__(self):
		pass
		
	async def websocket_handler(self, request: web.Request):
		ws = web.WebSocketResponse()

		await ws.prepare(request)

		connection = GatewayConnection(ws)

		try:
			await connection.send_hello()

			async for message in ws:

				match message.type:

					case web.WSMsgType.TEXT:
						try:
							packet = json.loads(
								message.data
							)
						except json.JSONDecodeError:
							logging.warning(
								"Received invalid JSON"
							)

							await ws.close(
								code=1007,
								message=b"Invalid JSON",
							)

							break

						if not isinstance(packet, dict):
							logging.warning(
								"Received invalid Gateway packet"
							)

							await ws.close(
								code=1007,
								message=b"Invalid packet",
							)

							break

						op = packet.get("op")
						data = packet.get("d")

						match op:

							case Opcode.HEARTBEAT:
								await connection.heartbeat(
									data
								)

							case Opcode.AUTHENTICATE:
								if (
									connection.state
									!= GatewayState.CONNECTED
								):
									logging.warning(
										"Authentication requested "
										"in state %s",
										connection.state,
									)
									continue

								method = (data or {}).get("method")

								if method != "passkey":
									logging.warning(
										"Unsupported authentication "
										"method: %r",
										method,
									)

									await ws.close(
										code=1008,
										message=b"Unsupported authentication",
									)

									break

								await connection.begin_authentication()

							case Opcode.AUTHENTICATION_RESPONSE:
								if (
									connection.state
									!= GatewayState.AUTHENTICATING
								):
									logging.warning(
										"Authentication response "
										"received in state %s",
										connection.state,
									)
									continue

								success = (
									await connection.authenticate(
										data
									)
								)

								if not success:
									await ws.close(
										code=1008,
										message=b"Authentication failed",
									)

									break

							case _:
								logging.warning(
									"Unknown opcode: %r",
									op,
								)

					case web.WSMsgType.BINARY:
						logging.debug(
							"Received binary message: %d bytes",
							len(message.data),
						)

					case web.WSMsgType.ERROR:
						logging.error(
							"WebSocket error: %s",
							ws.exception(),
						)

					case web.WSMsgType.CLOSE:
						pass

		finally:
			pass

		return ws
	
async def setup(bot):
	wscog = WS_handler()
	await bot.add_cog(wscog)