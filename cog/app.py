import discord
from discord.ext import commands
from aiohttp import web
import aiohttp
from aiohttp import web_exceptions
import aiohttp_jinja2
import jinja2
import asyncio
import logging
import logging.handlers
from urllib.parse import urlparse, parse_qs
import config
import os
import json
import datetime
import hmac
import hashlib
import youtube.youtube as yt
import youtube.twitch as twitch
from bs4 import BeautifulSoup
import uuid
import jwt as pyjwt
import sqlite3
import base64
import re
import pytz
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
import shutil
import inspect
import passkeys as psk
import mailer
import secrets
from onion.onion import Onion_APP

webapp = None
tz = pytz.timezone('Asia/Taipei')

class JWTResult():
	def __init__(self, decode_data:dict | None, verify:bool):
		self.decode_data = decode_data
		self.verify = verify

	def __bool__(self):
		return self.verify

class APP(commands.Cog):
	routes = web.RouteTableDef()

	def __init__(self, bot:commands.Bot) -> None:
		self.bot = bot
		self.iddb = sqlite3.connect(config.dir / 'database' / 'idata.db', isolation_level=None)
		self.webappdb = sqlite3.connect(config.dir / 'database' / 'webapp.db')
		self.youtubedb = sqlite3.connect(config.dir / 'database' / 'youtube.db')
		self.chatgptdb = sqlite3.connect(config.dir / 'database' / 'chatgpt.db', isolation_level=None)
		self.youtubedb.execute('PRAGMA auto_vacuum = FULL')
		self.youtubedb.execute('VACUUM')
		handler = logging.handlers.RotatingFileHandler(filename=config.dir / 'data' / 'logs' / 'webapplog.txt',maxBytes=1048576,backupCount=2,encoding="UTF-8")
		handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
		self.logger = logging.getLogger("webapp")
		self.logger.addHandler(handler)
		self.logger.setLevel(logging.INFO)
		self.task = None
		self.clean_db_task = None

	@routes.route(path="/webhook/youtube/{pathid}", method="POST")
	async def ytpost(self, request:web.Request):
		if request.headers.get("X-Hub-Signature") and request.headers['User-Agent'] == "FeedFetcher-Google; (+http://www.google.com/feedfetcher.html)":
			try:
				with sqlite3.connect(os.path.join(config.dir, 'database', 'youtube.db')) as db:
					secret = db.execute('SELECT secret FROM subscribe WHERE id = ?', (request.match_info['pathid'],)).fetchone()
					if secret is None:
						return web.Response(status=404,text="")
				msg = await request.text()
				bkey = bytes(secret[0],"utf-8")
				bdata = bytes(msg,"utf-8")
				hash = f"sha1={hmac.new(bkey, bdata, hashlib.sha1).hexdigest()}"
				signature = request.headers['X-Hub-Signature']
				if hmac.compare_digest(signature, hash):
					soup = BeautifulSoup(msg, 'xml')
					if soup.find('yt:videoId') is not None:
						id = soup.find('yt:videoId').text
						channel_id = soup.find('yt:channelId').text
						asyncio.create_task(yt.youtube(channel_id=channel_id, video_id=id, youtubedb=self.youtubedb))
					elif soup.find('at:deleted-entry') is not None:
						id = soup.find('at:deleted-entry')['ref'].replace("yt:video:", "")
						channel_id = soup.find('at:by').find('uri').text.split('/')[-1]
						asyncio.create_task(yt.youtube_delete(id, channel_id, self.youtubedb))
			except Exception as e:
				logging.warning(f"Process message failed! Reason: {e.__class__.__name__}: {e}\nHeaders: {dict(request.headers)}\ndata: {msg}")
			finally:
				headers = {'Cache-Control': 'no-cache'}
				return web.Response(status=204,text="", headers=headers)
		else:
			return web.Response(status=404,text="")

	@routes.route(path="/webhook/youtube/{pathid}", method="GET")
	async def ytfetch(self, request:web.Request):
		if request.query.get('hub.challenge') and request.query.get("hub.verify_token"):
			parsed_url = urlparse(request.query['hub.topic'])
			if parsed_url.netloc != "www.youtube.com" or "/xml/feeds/videos.xml" not in parsed_url.path:
				return web.Response(status=404, text="")
			query_params = parse_qs(parsed_url.query)
			channel_id = query_params['channel_id'][0]
			if request.match_info['pathid'] != channel_id:
				return web.Response(status=400, text="")
			with sqlite3.connect(os.path.join(config.dir, 'database', 'youtube.db')) as db:
				secret = db.execute('SELECT secret FROM subscribe WHERE id = ?', (channel_id,)).fetchone()
				if secret is None:
					return web.Response(status=404,text="")
				verify_token = hmac.new(bytes(config.secret,"utf-8"), bytes(secret[0],"utf-8"), hashlib.sha256).hexdigest()
				if request.query.get("hub.verify_token") != verify_token:
					return web.Response(status=401, text="")
				if request.query.get("hub.mode") == "subscribe":
					db.execute('UPDATE subscribe SET time = ? WHERE id = ?', (int(datetime.datetime.now().timestamp()) + int(request.query.get("hub.lease_seconds", "0")), channel_id))
				elif request.query.get("hub.mode") == "unsubscribe":
					db.execute('DELETE FROM subscribe WHERE id = ?', (channel_id,))
				db.commit()
			challenge = request.query['hub.challenge']
			headers = {'Cache-Control': 'no-cache'}
			return web.Response(status=200,text=challenge, headers=headers)
		else:
			return web.Response(status=404)

	@routes.route(path="/test/youtube", method="POST")
	async def yttest(self, request:web.Request):
		if request.headers.get("Authorization") == config.secret:
			video = request.query.get('video_id')
			if not video:
				return web.Response(status=400,text="No video id")
			asyncio.create_task(yt.youtube(video_id=video, test=True, youtubedb=self.youtubedb))
			headers = {'Cache-Control': 'no-cache'}
			return web.Response(status=202,text="", headers=headers)
		else:
			return web.Response(status=404,text="")

	@routes.route(path="/webhook/twitch/{pathid}", method="POST")
	async def twitchpost(self, request:web.Request):
		if request.headers.get('Twitch-Eventsub-Message-Type') and request.headers.get('Twitch-Eventsub-Message-Signature') and request.headers.get('Twitch-Eventsub-Message-Id') and request.headers.get('Twitch-Eventsub-Message-Timestamp') and request.headers.get('Twitch-Eventsub-Subscription-Type'):
			text = await request.text()
			data = f"{request.headers['Twitch-Eventsub-Message-Id']}{request.headers['Twitch-Eventsub-Message-Timestamp']}{text}"
			bkey = bytes(config.secret,"utf-8")
			bdata = bytes(data,"utf-8")
			hash = f"sha256={hmac.new(bkey, bdata, hashlib.sha256).hexdigest()}"
			signature = request.headers['Twitch-Eventsub-Message-Signature']
			if hmac.compare_digest(signature, hash):
				jsondata = json.loads(text)
				userid = jsondata['subscription']['condition']['broadcaster_user_id']
				headers = {'Cache-Control': 'no-cache'}
				if request.match_info['pathid'] != userid:
					return web.Response(status=400, text="")
				if request.headers['Twitch-Eventsub-Message-Type'] == "webhook_callback_verification":
					if request.headers['Twitch-Eventsub-Subscription-Type'] not in ["stream.online", 'stream.offline']:
						return web.Response(status=404,text="")
					challenge = jsondata['challenge']
					return web.Response(status=200,text=challenge, headers=headers)
				elif request.headers['Twitch-Eventsub-Message-Type'] == "notification":
					if request.headers['Twitch-Eventsub-Subscription-Type'] == "stream.online":
						asyncio.create_task(twitch.notification(jsondata, request.headers['Twitch-Eventsub-Message-Id']))
						return web.Response(status=204,text="", headers=headers)
					elif request.headers['Twitch-Eventsub-Subscription-Type'] == "stream.offline":
						asyncio.create_task(twitch.offline(jsondata, request.headers['Twitch-Eventsub-Message-Id']))
						return web.Response(status=204,text="", headers=headers)
					else:
						return web.Response(status=404,text="")
				elif request.headers['Twitch-Eventsub-Message-Type'] == "revocation":
					asyncio.create_task(twitch.revocation(jsondata))
					return web.Response(status=204,text="", headers=headers)
				else:
					return web.Response(status=202,text="", headers=headers)
			else:
				return web.Response(status=401, text="")
		else:
			return web.Response(status=404, text="")

	@routes.route(path="/webhook/line", method="POST")
	async def line(self, request:web.Request):
		body = await request.text()
		signature = base64.b64encode(hmac.new(config.line_secret.encode('utf-8'), body.encode('utf-8'), hashlib.sha256).digest()).decode()
		if hmac.compare_digest(signature, request.headers.get("x-line-signature")):
			body_json = json.loads(body)
			log = f"A line message:\n{json.dumps(body_json, indent=2, ensure_ascii=False)}"
			logging.info(log)
			return web.Response(status=200, text="")
		else:
			return web.Response(status=401, text="")

	@routes.route(path="/webhook/discord", method="POST")
	async def command(self, request:web.Request):
		signature = request.headers.get("X-Signature-Ed25519")
		timestamp = request.headers.get("X-Signature-Timestamp")
		if not signature or not timestamp:
			return web.Response(status=401, text="")

		body = await request.text()
		verify_key = VerifyKey(bytes.fromhex(config.bot_public_key))
		try:
			verify_key.verify(f'{timestamp}{body}'.encode(), bytes.fromhex(signature))
		except BadSignatureError:
			logging.info(f"Discord send a invalid signature\nSignature: {signature}\nTimestamp: {timestamp}\nData: {body}")
			return web.Response(status=401, text="")

		data = await request.json()
		interaction = discord.Interaction(data=data, state=self.bot._connection)
		if data["type"] == 1:
			headers = {"Content-Type": "application/json"}
			r = {"type": 1}
			return web.Response(status=200, headers=headers, body=json.dumps(r))

		try:
			if data["type"] == 2:
				asyncio.create_task(self.bot.tree._call(interaction))

			elif data["type"] == 3:
				v = self.bot._connection._view_store._views.get(int(data["message"]["id"]))
				if v:
					c = v.get((int(data["data"]["component_type"]), data["data"]["custom_id"]))
					if inspect.iscoroutinefunction(c.callback):
						asyncio.create_task(c.callback(interaction))
					else:
						c.callback(interaction)

			elif data["type"] == 5:
				m = self.bot._connection._view_store._modals.get(data["data"]["custom_id"])
				if m:
					m._dispatch_submit(interaction, data["data"]["components"], data["data"].get('resolved', {}))

		except:
			logging.exception("Can't reslove Discord interaction!!")
		finally:
			return web.Response(status=202, text="")

	@routes.route(path="/robots.txt", method="GET")
	async def robots(self, request:web.Request):
		text = 'User-agent: *\nDisallow: /'
		return web.Response(status=200, text=text, content_type='text/plain')

	@routes.route(path="/status", method="GET")
	async def status(self, request:web.Request):
		if request.headers.get('Authorization') == config.secret:
			now = datetime.datetime.now().timestamp()
			signature = hmac.new(bytes(config.token,"utf-8"), bytes(str(now),"utf-8"), hashlib.sha256).hexdigest()
			header = {
				"X-Time": str(now),
				"X-Signature": signature
			}
			return web.Response(status=204,text="",headers=header)
		else:
			return web.Response(status=404, text="")

	@routes.route(path="/entrance", method="GET")
	async def entrance(self, request:web.Request):
		logging.warning(f"A user want to enter this site!\n{request.headers}")
		return web.Response(status=404, text="")

	@web.middleware
	async def rdns(self, request:web.Request, handler):
		if not request.headers.get('X-Real-IP') or not request.headers.get('User-Agent'):
			return web.Response(status=400,text="")
		if all(request.method != x for x in ["GET", "POST"]):
			return web.Response(status=400,text="")
		if request.headers.get('Host') == config.onion_domain:
			onion_cls = Onion_APP()
			return await onion_cls.middle_ware(request)
		try:
			if handler.__name__ == "_handle":
				return web.Response(status=404,text="")
			response = await handler(self, request)
			return response
		except web_exceptions.HTTPNotFound:
			return web.Response(status=404,text="")
		except:
			ex = {"Code": 500, "Message": "Internal_Server_Error"}
			logging.exception(f"An error occurred while handling request!")
			return web.Response(status=500,text=json.dumps(ex), content_type="application/json")

	#---------------------------------------------------------------------

	@routes.route(path="/api/{path:.*}", method="GET")
	@routes.route(path="/api/{path:.*}", method="POST")
	async def api(self, request:web.Request):
			if request.headers.get("Early-Data"):
				return web.Response(status=425,text="")

			path = request.match_info['path'].split("/")
			match path:
				case ["turnstile"]:
					return await self.turnstile(request)
				case ["chatgpt", "images", image_id]:
					return await self.chatgpt_get_image(request, image_id)
				case ["passkeys"]:
					if request.method == "GET":
						return await self.get_passkeys(request)
					elif request.method == "POST":
						body = await request.json()
						match body["op"]:
							case "begin_login":
								return await self.passkey_begin_login(request, body["d"])
							case "finish_login":
								return await self.passkey_finish_login(request, body["d"])
							case "begin_register":
								return await self.passkey_begin_register(request, body["d"])
							case "finish_register":
								return await self.passkey_finish_register(request, body["d"])
							case "detail":
								return await self.passkey_detail(request, body["d"])
							case "rename":
								return await self.passkey_rename(request, body["d"])
							case "delete":
								return await self.passkey_delete(request, body["d"])
							case _:
								return web.Response(status=405,text="")
					else:
						return web.Response(status=405,text="")

			if await self.site_verify(request=request):
				match path:
					case ["log", log_type]:
						return await self.log(request, log_type)
					case ["chat", chatid]:
						return await self.chat_post(request, chatid)
					case ["chatlog", chatid]:
						return await self.chat_log(request, chatid)
					case ["chat-images", image_id]:
						return await self.chat_image(request, image_id)
					case ["chatlist", chatid]:
						return await self.chat_op(request, chatid)
					case ["subscription-list"]:
						subscription_list = {}
						with sqlite3.connect(os.path.join(config.dir, 'database', 'youtube.db')) as db:
							yt_db_list = db.execute('SELECT id, name FROM subscribe').fetchall()
						yt_list = []
						for cursor in yt_db_list:
							yt_list.append({"id":cursor[0], "name":cursor[1]})
						subscription_list["Youtube"] = yt_list
						return web.Response(status=200,body=json.dumps(subscription_list, ensure_ascii=False), content_type="application/json", charset="UTF-8")
					case ["subscription", "youtube", channel_id]:
							return await self.ytsubscription(request, channel_id)
					case ["ws"]:
						wscog = self.bot.get_cog("WS_handler")
						if wscog is None:
							web.Response(status=503, text="")
						return await wscog.websocket_handler(request)
					case _:
						return web.Response(status=404, text="")
			else:
				data = {
					"Code": 401,
					"Message": "Unauthorized"
				}
				header = {
					"Www-Authenticate": 'Basic realm="Restricted Area"'
				}
				return web.Response(status=401,text=json.dumps(data), content_type="application/json", headers=header)

	async def turnstile(self, request:web.Request):
		try:
			token = await request.json()
			if not isinstance(token, dict):
				return web.Response(status=415, text="")
		except:
			return web.Response(status=415, text="")
		headers = {
			"Content-Type": "application/json",
		}
		data = {
				"secret": config.cloudflare_turnstile_key,
	 			"response": token.get("token"),
				"remoteip": request.headers.get('X-Real-IP')
		}
		async with aiohttp.ClientSession() as s:
			async with s.post("https://challenges.cloudflare.com/turnstile/v0/siteverify", headers=headers, data=json.dumps(data)) as r:
				r.raise_for_status()
				payload = await r.json()

		if payload.get("success") and payload.get("action") == "Entrance" and payload.get("cdata"):
			state_uuid, state_hmac, state_time = payload["cdata"].split("-")
			if not state_uuid or not state_hmac or not state_time:
				logging.warning(f"Got wrong turnstile token! | Wrong cdata\n{token}\n{payload}")
				return web.Response(status=400, text="")
			state_hmac_new = hmac.new(bytes(config.web_secret, "UTF-8"), bytes(f"{state_uuid}-{state_time}", "UTF-8"), hashlib.sha256).hexdigest()
			if not hmac.compare_digest(state_hmac, state_hmac_new):
				logging.warning(f"Got wrong turnstile token! | Wrong hmac signature\n{token}\n{payload}")
				return web.Response(status=400, text="")

			can_refresh = await self.site_refresh(request)
			if isinstance(can_refresh, dict):
				response = web.Response(status=200, text=f"https://{config.domain}/home/main")
				response.set_cookie(name="Authorization", value=can_refresh["access_token"], path="/", secure=True, httponly=True, samesite="Lax", max_age=619200)
				response.set_cookie(name="Refresh", value=can_refresh["refresh_token"], path="/", secure=True, httponly=True, samesite="Lax", max_age=2592000)
				return response

			try:
				time_stamp = int(state_time)
				if datetime.datetime.now().timestamp() > (time_stamp / 1000) + 300:
					logging.warning(f"Got wrong turnstile token! | Time stamp expire\n{token}\n{payload}")
					return web.Response(status=403, text="")
				extend = {
					"hmac": state_hmac
				}
				ticket = self.jwt_create(scope="app.ticket.login", exp_offset=300, jti=state_uuid, extend=extend)
			except (ValueError, sqlite3.IntegrityError):
				logging.warning(f"Got wrong turnstile token! | state replay\n{token}\n{payload}")
				return web.Response(status=403, text="")
			response = web.Response(status=200, text=f"https://{config.domain}/passkey")
			response.set_cookie(name="ticket", value=ticket, path="/", secure=True, httponly=True, samesite="Strict", max_age=300)
			if request.cookies.get("Refresh"):
				response.set_cookie(name="Refresh", value="", path="/", secure=True, httponly=True, samesite="Lax", max_age=0)
			return response
		else:
			logging.warning(f"Got wrong turnstile token!\n{token}\n{payload}")
			return web.Response(status=403, text="")

	@routes.route(path="/home/{path:.*}", method="GET")
	async def home(self, request:web.Request):
		if not await self.site_verify(request):
			headers = {"Location": "/"}
			response = web.Response(status=302, headers=headers)
			if request.cookies.get("Authorization"):
				response.set_cookie(name="Authorization", value="", path="/", secure=True, httponly=True, samesite="Lax", max_age=0)
			return response
		path = request.match_info['path']
		match path:
			case "main":
				with open(config.dir / 'html' / "home.html", "r") as d:
					c = d.read()
				return web.Response(status=200,body=c, content_type="text/html")
			case "syslog":
				with open(config.dir / 'html' / "syslog.html", "r") as d:
					c = d.read()
				return web.Response(status=200,body=c, content_type="text/html")
			case "backuplog":
				with open(config.dir / 'html' / "syslog_backup.html", "r") as d:
					c = d.read()
				return web.Response(status=200,body=c, content_type="text/html")
			case "applog":
				with open(config.dir / 'html' / "applog.html", "r") as d:
					c = d.read()
				return web.Response(status=200,body=c, content_type="text/html")
			case "backupapplog":
				with open(config.dir / 'html' / "applog_backup.html", "r") as d:
					c = d.read()
				return web.Response(status=200,body=c, content_type="text/html")
			case "misskey":
				with open(config.dir / 'html' / "misskey_statistics.html", "r") as d:
					c = d.read()
				return web.Response(status=200,body=c, content_type="text/html")
			case "nginx":
				with open(config.dir / 'html' / "nginx.html", "r") as d:
					c = d.read()
				return web.Response(status=200,body=c, content_type="text/html")
			case "tor":
				with open(config.dir / 'html' / "torlog.html", "r") as d:
					c = d.read()
				return web.Response(status=200,body=c, content_type="text/html")
			case "passkeys":
				with open(config.dir / 'html' / "passkey_list.html", "r") as d:
					c = d.read()
				return web.Response(status=200,body=c, content_type="text/html")
			case "ws-playground":
				with open(config.dir / 'html' / "ws.html", "r") as d:
					c = d.read()
				return web.Response(status=200,body=c, content_type="text/html")
			case _:
				return web.Response(status=404,text="")

	async def log(self, request:web.Request, log_type:str):
		match log_type:
			case "syslog":
				with open(config.dir / 'data' / 'logs' / "syslog.txt", "r") as d:
					c = d.read()
				return web.Response(status=200,text=c)
			case "backuplog":
				if os.path.isfile(config.dir / 'data' / 'logs' / "syslog.txt.1"):
					with open(config.dir / 'data' / 'logs' / "syslog.txt.1", "r") as d:
						c = d.read()
					return web.Response(status=200,text=c)
				else:
					return web.Response(status=404,text="")
			case "applog":
				with open(config.dir / 'data' / 'logs' / "webapplog.txt", "r") as d:
					c = d.read()
				return web.Response(status=200,text=c)
			case "backupapplog":
				if os.path.isfile(config.dir / 'data' / 'logs' / "webapplog.txt.1"):
					with open(config.dir / 'data' / 'logs' / "webapplog.txt.1", "r") as d:
						c = d.read()
					return web.Response(status=200,text=c)
				else:
					return web.Response(status=404,text="")
			case "misskey":
				with open(config.dir / 'data' / 'misskey' / "statistic.json", "r") as d:
					c = d.read()
				return web.Response(status=200,body=c, content_type="application/json", charset="UTF-8")
			case "chatlist":
				cursor = self.chatgptdb.execute('SELECT * FROM list').fetchall()
				c = {}
				for data in cursor:
					e = {
						"name": data[1],
						"uuid": data[2],
						"updatedAt": data[4],
						"lastMessage": data[5],
						"sex": data[6]
					}
					c[str(data[0])] = e
				return web.Response(status=200,body=json.dumps(c, ensure_ascii=False), content_type="application/json", charset="UTF-8")
			case "nginx":
				with open("/var/log/nginx/access.log", "r") as d:
					c = d.read()
					matches = re.findall(r'\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+\d{2}:\d{2})\]', c)
					if matches:
						for m in matches:
							log_time = datetime.datetime.strptime(m, "%Y-%m-%dT%H:%M:%S%z").strftime("%Y-%m-%d %H:%M:%S")
							c = c.replace(m, log_time)
				return web.Response(status=200,text=c)
			case "tor":
				with open("/home/user/.tor/log/notices.log", "r") as d:
					c = d.read()
					return web.Response(status=200,text=c)
			case _:
				return web.Response(status=404,text="")

	def jwt_create(self, scope:str, exp_offset:int=0, key:str=None, jti:str=None, extend:dict=None):
		now = int(datetime.datetime.now().timestamp())
		payload = {
			"scope": scope,
			"iat": now,
			"exp": now + exp_offset,
			"jti": jti or str(uuid.uuid4()).replace("-", ""),
			"iss": "modbot:app",
			"aud": "app.web"
		}
		if key is None:
			key = config.web_secret
		if extend is not None:
			payload = payload | extend
		return pyjwt.encode(payload=payload, key=key, algorithm='HS256')

	async def jwt_verify(self, scope:str, jwt:str, key:str=None):
		if key is None:
			key = config.web_secret
		try:
			options = {
				"require": [
						"exp",
						"iat",
						"jti",
						"aud",
						"iss",
						"scope"
				]
			}
			decoded_token = pyjwt.decode(jwt, key, algorithms=['HS256'], options=options, audience="app.web", issuer="modbot:app")
		except pyjwt.InvalidTokenError:
			return JWTResult(decode_data=None, verify=False)
		if decoded_token.get("scope") != scope:
			return JWTResult(decode_data=None, verify=False)
		if scope == 'app.access.token':
			session_id = decoded_token.get('jti')
			if session_id is None:
				return JWTResult(decode_data=None, verify=False)
			cursor = self.webappdb.execute('SELECT last_use FROM session WHERE id = ?', (session_id,)).fetchone()
			if not cursor:
				return JWTResult(decode_data=None, verify=False)
			now = int(datetime.datetime.now().timestamp())
			if now - cursor[0] > 60:
				self.webappdb.execute('UPDATE session SET last_use = ? WHERE id = ?', (now, session_id))
				self.webappdb.commit()
		return JWTResult(decode_data=decoded_token, verify=True)

	async def site_verify(self, request:web.Request):
		token = request.cookies.get("Authorization")
		if token is None:
			return JWTResult(decode_data=None, verify=False)
		return await self.jwt_verify("app.access.token", token)

	async def site_refresh(self, request:web.Request):
		if request.headers.get('Cookie'):
			if not request.cookies.get("Refresh"):
				return False
			try:
				refresh_data = await self.jwt_verify(scope="app.refresh.token", jwt=request.cookies["Refresh"])
				if not refresh_data:
					return False
				jti = refresh_data.decode_data["jti"]
				assert await self.discord_refresh(jti)
				tokens = {
					"access_token": self.jwt_create(scope="app.access.token", exp_offset=691200, jti=jti),
					"refresh_token": self.jwt_create(scope="app.refresh.token", exp_offset=3153600, jti=jti)
				}
				return tokens
			except:
				return False
		return False

	@routes.route(path="/auth", method="GET")
	async def discord_auth(self, request:web.Request):
		code = request.query.get('code')
		state = request.query.get('state')
		if request.query.get('error') == 'access_denied':
			return web.Response(status=403, text='Sorry, You must login from discord to enter this site')
		if not code or not state:
			return web.Response(status=400, text='')
		state_data = self.webappdb.execute('SELECT * FROM state WHERE id = ?', (state,)).fetchone()
		if state_data is None:
			return web.Response(status=400, text='')
		state, userip, useragant, expire, is_used, state_uuid = state_data
		if is_used:
			return web.Response(status=403, text='')
		self.webappdb.execute('UPDATE state SET is_used = ? WHERE id = ?', (1,state))
		self.webappdb.commit()
		now = int(datetime.datetime.now().timestamp())
		if userip != request.headers.get('X-Real-IP') or useragant != request.headers.get('User-Agent'):
			return web.Response(status=400, text='')
		if datetime.datetime.now().timestamp() > expire:
			return web.Response(status=408, text='')
		data = {
			'grant_type': 'authorization_code',
			'code': code,
			'redirect_uri': f'https://{config.domain}/auth'
		}
		headers = {'Content-Type': 'application/x-www-form-urlencoded'}
		try:
			async with aiohttp.ClientSession() as s:
				async with s.post("https://discord.com/api/v10/oauth2/token", headers=headers, data=data, auth=aiohttp.BasicAuth(str(config.applications_id), config.bot_auth_secret)) as r:
					d = await r.json()
					if r.status == 400 and d.get("error") == "invalid_grant":
						return web.Response(status=400, text='')
					r.raise_for_status()
					access_token = d['access_token']
					refresh_token = d['refresh_token']
					expires = now + d['expires_in']
				headers = {'Authorization': f'Bearer {access_token}'}
				async with s.get("https://discord.com/api/v10/users/@me", headers=headers) as r:
					r.raise_for_status()
					d = await r.json()
					userid = d['id']
					if userid != str(config.ownerid):
						logging.warning(f"A unknown user want login from Discord\n{d}")
						return web.Response(status=403, text="Sorry, You don't have permission to enter this site")
					session_id = str(uuid.uuid4()).replace("-", "")
					jwt = self.jwt_create(scope='app.access.token', exp_offset=619200, jti=session_id)
					rjwt = self.jwt_create(scope="app.refresh.token", exp_offset=2592000, jti=session_id)
					self.webappdb.execute('INSERT INTO session VALUES (?, ?, ?, ?, ?)', (session_id, access_token, refresh_token, now, "Discord"))
					self.webappdb.commit()
					action_url = self.revoke_action_url(session_id=session_id)
					asyncio.create_task(asyncio.to_thread(mailer.login_notification, headers=request.headers, action_url=action_url, method="Discord"))
					headers = {
						"Location": "/home/main"
					}
					response = web.Response(status=302, headers=headers)
					response.set_cookie(name="Authorization", value=jwt, path="/", secure=True, httponly=True, samesite="Lax", max_age=619200)
					response.set_cookie(name="Refresh", value=rjwt, path="/", secure=True, httponly=True, samesite="Lax", max_age=2592000)
					return response
		except Exception as e:
			logging.error(f"Get discord auth token failed!! | Reason: {e}")
			return web.Response(status=500, text=f'')

	async def discord_refresh(self, session_id:str):
		refresh_token = self.webappdb.execute('SELECT refresh_token FROM session WHERE id = ?', (session_id,)).fetchone()
		if not refresh_token:
			return False
		refresh_token = refresh_token[0]
		data = {
			'grant_type': 'refresh_token',
			'refresh_token': refresh_token
		}
		headers = {'Content-Type': 'application/x-www-form-urlencoded'}
		try:
			async with aiohttp.ClientSession() as s:
				async with s.post("https://discord.com/api/v10/oauth2/token", headers=headers, data=data, auth=aiohttp.BasicAuth(str(config.applications_id), config.bot_auth_secret)) as r:
					if r.status == 400:
						d = await r.json()
						if d.get('error') == 'invalid_grant':
							self.webappdb.execute('DELETE FROM session WHERE refresh_token = ?', (refresh_token,))
							self.webappdb.commit()
							return False
					r.raise_for_status()
					d = await r.json()
					new_access_token = d['access_token']
					new_refresh_token = d['refresh_token']
					expires = int(datetime.datetime.now().timestamp()) + d['expires_in']
				headers = {'Authorization': f'Bearer {new_access_token}'}
				async with s.get("https://discord.com/api/v10/users/@me", headers=headers) as r:
					r.raise_for_status()
					d = await r.json()
					userid = d['id']
					if userid != str(config.ownerid):
						self.webappdb.execute('DELETE FROM session WHERE refresh_token = ?', (refresh_token,))
						self.webappdb.commit()
						return False
					self.webappdb.execute('UPDATE session SET access_token = ?, refresh_token = ? WHERE refresh_token = ?', (new_access_token, new_refresh_token, refresh_token))
					self.webappdb.commit()
					return True
		except Exception as e:
			logging.error(f"Refesh discord auth token failed!! | Reason: {e}")
			raise

	async def get_passkeys(self, request:web.Request):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")

		cur = self.webappdb.execute("""
			SELECT
				credential_id,
				name,
				created_at,
				last_used_at
			FROM passkeys
			ORDER BY created_at DESC
		""").fetchall()

		data = [
			{
				"credentialId": psk.base64url_encode(row[0]),
				"name": row[1],
				"createdAt": row[2],
				"lastUsedAt": row[3]
			}
			for row in cur
		]

		return web.Response(status=200, body=json.dumps(data, ensure_ascii=False), content_type="application/json")

	async def passkey_begin_register(self, request:web.Request, d:dict):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")

		count = self.webappdb.execute("SELECT COUNT(*) FROM passkeys").fetchone()[0]

		if count >= 1:
			credential = d.get("credential")
			if credential is None:
				return web.Response(status=401, text="驗證尚未完成，禁止新增")

			try:
				psk.validate_login(credential=credential, db=self.webappdb)
			except psk.PasskeyError as e:
				return web.Response(status=400, text=str(e))

		data = psk.get_register_challage(db=self.webappdb)
		return web.Response(status=200, body=json.dumps(data, ensure_ascii=False), content_type="application/json")

	async def passkey_finish_register(self, request:web.Request, d:dict):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")

		credential = d.get("credential")
		validated_result = psk.validate_webauthn_credential(credential, assertion=False)
		if validated_result:
			return web.Response(status=422, text=validated_result)

		try:
			psk.register_passkey(credential=credential, db=self.webappdb)
		except psk.PasskeyError as e:
			return web.Response(status=400, text=str(e))

		data = {
			"ok": True
		}
		return web.Response(status=200, body=json.dumps(data, ensure_ascii=False), content_type="application/json")

	async def passkey_detail(self, request:web.Request, d:dict):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")

		row = self.webappdb.execute("""
			SELECT
				name,
				credential_id,
				aaguid,
				sign_count,
				public_key,
				created_at,
				last_used_at
			FROM
				passkeys
			WHERE
				credential_id = ?
			""", (psk.base64url_decode(d["credentialId"]),)).fetchone()
		if row is None:
			return web.Response(status=404,text="Passkey not found")

		data = {
			"name": row[0],
			"credentialId": psk.base64url_encode(row[1]),
			"aaguid": uuid.UUID(bytes=row[2]).hex,
			"signCount": row[3],
			"publicKey": psk.base64url_encode(row[4]),
			"createdAt": row[5],
			"lastUsedAt": row[6]
		}
		return web.Response(status=200, body=json.dumps(data, ensure_ascii=False), content_type="application/json")

	async def passkey_rename(self, request:web.Request, d:dict):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")

		credential_id = psk.base64url_decode(d["credentialId"])
		name = d["name"].strip()

		if not name:
			return web.Response(status=400,text="Name cannot be empty")

		if len(name) > 64:
			return web.Response(status=400,text="Name too long")

		self.webappdb.execute("UPDATE passkeys SET name = ? WHERE credential_id = ?", (name, credential_id))
		self.webappdb.commit()

		data = {
			"ok": True
		}
		return web.Response(status=200, body=json.dumps(data, ensure_ascii=False), content_type="application/json")

	async def passkey_delete(self, request:web.Request, d:dict):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")

		count = self.webappdb.execute("SELECT COUNT(*) FROM passkeys").fetchone()[0]

		if count <= 1:
			return web.Response(status=409, text="你必須保留至少1個Passkey。")

		credential_id = psk.base64url_decode(d["credentialId"])

		self.webappdb.execute("DELETE FROM passkeys WHERE credential_id = ?", (credential_id,))
		self.webappdb.commit()

		data = {
			"ok": True
		}
		return web.Response(status=200, body=json.dumps(data, ensure_ascii=False), content_type="application/json")

	@routes.route(path="/passkey", method="GET")
	async def passkey_login(self, request:web.Request):
		try:
			ticket = request.cookies["ticket"]
			verify = await self.jwt_verify(scope="app.ticket.login", jwt=ticket)
			if not verify:
				raise Exception()
		except:
			headers = {"Location": "/"}
			response = web.Response(status=302, headers=headers)
			if request.cookies.get("ticket"):
				response.set_cookie(name="ticket", value="", path="/", secure=True, httponly=True, samesite="Lax", max_age=0)
			return response

		with open(os.path.join(config.dir, 'html',"passkey_login.html"), "r") as d:
			c = d.read()
		return web.Response(status=200,body=c, content_type="text/html")

	async def passkey_begin_login(self, request:web.Request, d:dict):
		try:
			ticket = request.cookies["ticket"]
			ticket_verify = await self.jwt_verify(scope="app.ticket.login", jwt=ticket)
			if not ticket_verify:
				raise Exception()
		except:
			if not await self.site_verify(request):
				return web.Response(status=400, text="Invalid Ticket")

		if d.get("provider") == "discord":
			try:
				state_hmac = ticket_verify.decode_data["hmac"]
				state_uuid = ticket_verify.decode_data["jti"]
			except KeyError:
				return web.Response(status=401, text="")
			state = hmac.new(bytes(config.web_secret, "UTF-8"), bytes(state_hmac, "UTF-8"), hashlib.sha256).hexdigest()
			self.webappdb.execute('INSERT INTO state VALUES (?, ?, ?, ?, ?, ?)', (state, request.headers.get('X-Real-IP'), request.headers.get('User-Agent'), int(datetime.datetime.now().timestamp() + 1800), 0, state_uuid))
			self.webappdb.commit()
			data = {
				"url": f"https://discord.com/oauth2/authorize?response_type=code&client_id={config.applications_id}&scope=identify&state={state}"
			}
			return web.Response(status=200, body=json.dumps(data, ensure_ascii=False), content_type="application/json")
		elif d.get("provider") != "passkey":
			return web.Response(status=400, text="Invalid Provider")

		data = psk.get_login_challage(db=self.webappdb)
		return web.Response(status=200, body=json.dumps(data, ensure_ascii=False), content_type="application/json")

	async def passkey_finish_login(self, request:web.Request, d:dict):
		credential = d.get("credential")

		validated_result = psk.validate_webauthn_credential(credential, assertion=True)
		if validated_result:
			return web.Response(status=422, text=validated_result)

		try:
			psk.validate_login(credential=credential, db=self.webappdb)
		except psk.PasskeyError as e:
			return self.passkey_error(credential_id=credential["id"], message=str(e), request=request)

		data = {
			"ok": True,
			"url": f"https://{config.domain}/home/main"
		}
		response = web.Response(status=200, body=json.dumps(data, ensure_ascii=False), content_type="application/json")

		session_id = str(uuid.uuid4()).replace("-", "")
		now = datetime.datetime.now().timestamp()
		access_token = self.jwt_create(scope='app.access.token', exp_offset=619200, jti=session_id)
		self.webappdb.execute('INSERT INTO session VALUES (?, ?, ?, ?, ?)', (session_id, None, None, now, "Passkey"))
		response.set_cookie(name="Authorization", value=access_token, path="/", secure=True, httponly=True, samesite="Lax", max_age=619200)
		self.webappdb.commit()
		action_url = self.revoke_action_url(session_id=session_id)
		asyncio.create_task(asyncio.to_thread(mailer.login_notification, headers=request.headers, action_url=action_url, method="Passkey"))

		return response

	def passkey_error(s, credential_id:str, message:str, request:web.Request):
		logging.warning(
			"Passkey Login Failed!!\n"
			f"Credential ID: {credential_id}\n"
			f"Message: {message}\n"
			f"IP: {request.headers['X-Real-IP']}"
		)
		return web.Response(status=400, text=message)

	def revoke_action_url(self, session_id:str):
		token = psk.base64url_encode(secrets.token_bytes(32))
		expires = int(datetime.datetime.now().timestamp()) + 172800
		self.webappdb.execute('INSERT INTO revoke VALUES (?, ?, ?)', (token, session_id, expires))
		return f"https://{config.domain}/revoke/{token}"

	@routes.route(path="/revoke/{token:.*}", method="GET")
	async def revoke_action(self, request:web.Request):
		token = request.match_info['token']

		revoke_cursor = self.webappdb.execute('SELECT session_id FROM revoke WHERE token = ?', (token,)).fetchone()
		if revoke_cursor is None:
			return aiohttp_jinja2.render_template("action_expired.html", request, {"reason": "invalid_token"})

		session_id = revoke_cursor[0]

		session_cursor = self.webappdb.execute('SELECT COUNT(*) FROM session WHERE id = ?', (session_id,)).fetchone()
		if not session_cursor[0]:
			return aiohttp_jinja2.render_template("action_expired.html", request, {"reason": "session_not_found"})

		self.webappdb.execute('DELETE FROM session WHERE id = ?', (session_id,))
		self.webappdb.commit()

		return aiohttp_jinja2.render_template("session_revoked.html", request, {"session_id": session_id})

	@routes.route(path="/logout", method="GET")
	async def logout(self, request:web.Request):
		if request.headers.get('Cookie'):
			if not request.cookies.get("Authorization"):
				headers = {"Location": "/"}
				return web.Response(status=302, headers=headers)
			try:
				decoded_token = await self.jwt_verify(scope="app.access.token", jwt=request.cookies["Authorization"])
			except:
				headers = {"Set-Cookie": 'Authorization=; Max-Age=0;', "Location": "/"}
				return web.Response(status=302, headers=headers)
		else:
			headers = {"Location": "/"}
			return web.Response(status=302, headers=headers)
		session_id = decoded_token.decode_data['jti']
		access_token = self.webappdb.execute('SELECT access_token FROM session WHERE id = ?', (session_id,)).fetchone()
		self.webappdb.execute('DELETE FROM session WHERE id = ?', (session_id,))
		self.webappdb.commit()
		if access_token is None:
			headers = {"Set-Cookie": f'Authorization=; Max-Age=0;', "Location": "/"}
			return web.Response(status=302, headers=headers)
		data = {
			'token': access_token[0],
			'token_type_hint': 'access_token'
		}
		headers = {'Content-Type': 'application/x-www-form-urlencoded'}
		try:
			async with aiohttp.ClientSession() as s:
				async with s.post("https://discord.com/api/v10/oauth2/token/revoke", headers=headers, data=data, auth=aiohttp.BasicAuth(str(config.applications_id), config.bot_auth_secret)) as r:
					r.raise_for_status()
					headers = {"Location": "/"}
					response = web.Response(status=302, headers=headers)
					response.set_cookie(name="Authorization", value="", path="/", secure=True, httponly=True, samesite="Lax", max_age=0)
					response.set_cookie(name="Refresh", value="", path="/", secure=True, httponly=True, samesite="Lax", max_age=0)
					return response
		except Exception as e:
			logging.error(f"Revoke discord auth token failed!! | Reason: {e}")
			return web.Response(status=500, text='')

	@routes.route(path="/chat/{chatid:.*}", method="GET")
	async def chat_site(self, request:web.Request):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")
		chatid = request.match_info['chatid']
		cursor = self.chatgptdb.execute('SELECT name, uuid FROM list WHERE id = ?', (chatid,)).fetchone()
		if cursor is None or not os.path.isdir(os.path.join(config.dir, 'data', 'chatgpt', chatid)):
			return web.Response(status=404, text="")
		nonce = uuid.uuid4().hex
		headers = {
			'Content-Type': 'text/html',
			'Content-Security-Policy': f"script-src 'self' 'nonce-{nonce}'"
		}
		return web.Response(status=200,text=aiohttp_jinja2.render_string("chat.html", request, {"nonce": nonce}), headers=headers)

	async def chat_op(self, request:web.Request, chatid:str):
		cursor = self.chatgptdb.execute('SELECT name FROM list WHERE id = ?', (chatid,)).fetchone()
		if cursor is None or not os.path.isdir(os.path.join(config.dir, 'data', 'chatgpt', chatid)):
			return web.Response(status=404, text="")

		body = await request.json()
		match body.get("op"):
			case "change_name":
				if not body.get("d"):
					return web.Response(status=400, text="")
				self.chatgptdb.execute("UPDATE list SET name = ? WHERE id = ?", (body["d"], chatid))
				self.chatgptdb.commit()
				return web.Response(status=200, text="")
			case "delete_chat":
				self.chatgptdb.execute("DELETE FROM list WHERE id = ?", (chatid,))
				self.chatgptdb.commit()
				shutil.rmtree(os.path.join(config.dir, 'data', 'chatgpt', chatid))
				return web.Response(status=200, text="")
			case _:
				return web.Response(status=400, text="")

	async def chat_log(self, request:web.Request, chatid:str):
		cursor = self.chatgptdb.execute('SELECT name, uuid FROM list WHERE id = ?', (chatid,)).fetchone()
		if cursor is None or not os.path.isdir(os.path.join(config.dir, 'data', 'chatgpt', chatid)):
			return web.Response(status=404, text="")
		with open(os.path.join(config.dir, 'data', 'chatgpt', str(chatid), "chat.json"), 'r') as d:
			c = d.read()
		return web.Response(status=200,body=c, content_type="application/json")

	async def chat_post(self, request:web.Request, chatid:str):
		if chatid == "new":
			body = await request.json()
			chat_uuid = str(uuid.uuid4()).replace("-", "")
			count = self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("count",)).fetchone()[0]
			count += 1
			sex_cursor = self.chatgptdb.execute('SELECT sex FROM list WHERE id = ?', (int(body["chatid"]),)).fetchone()[0]
			chat_sex = "b" if sex_cursor == "b" else "g"
			self.chatgptdb.execute('UPDATE memo SET value = ? WHERE key = ?', (count, "count"))
			self.chatgptdb.execute('INSERT INTO list VALUES (?, ?, ?, ?, ?, ?, ?)', (count, body["name"], chat_uuid, body["lastid"], int(datetime.datetime.now().timestamp()), None, chat_sex))
			self.chatgptdb.commit()
			os.mkdir(os.path.join(config.dir, 'data', 'chatgpt', str(count)))
			with open(os.path.join(config.dir, 'data', 'chatgpt', str(count), "all.json"), 'w') as f:
				json.dump([], f, ensure_ascii=False, indent=2)
			with open(os.path.join(config.dir, 'data', 'chatgpt', str(count), "chat.json"), 'w') as f:
				json.dump(body["message"], f, ensure_ascii=False, indent=2)
			return web.Response(status=204,text="")
		cursor = self.chatgptdb.execute('SELECT name, uuid FROM list WHERE id = ?', (chatid,)).fetchone()
		if cursor is None or not os.path.isdir(os.path.join(config.dir, 'data', 'chatgpt', chatid)):
			return web.Response(status=404, text="")
		with open(os.path.join(config.dir, 'data', 'chatgpt', str(chatid), "chat.json"), 'w') as d:
			body = await request.json()
			json.dump(body, d, indent=2, ensure_ascii=False)
		return web.Response(status=204,text="")

	@routes.route(path="/chatlist", method="GET")
	async def chat_list(self, request:web.Request):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")
		with open(os.path.join(config.dir, 'html',"chatlist.html"), "r") as d:
			c = d.read()
		return web.Response(status=200,body=c, content_type="text/html")

	async def chatgpt_get_image(self, request:web.Request, image_id:str):
		if not request.query.get("state") or not request.query.get("token") or not request.query.get("time"):
			return web.Response(status=400,text="")
		state_time = int(request.query["time"])
		state = request.query["state"]
		token = request.query["token"]
		if datetime.datetime.now().timestamp() > (state_time + 15):
			return web.Response(status=400,text="")
		new_token = hmac.new(bytes(config.secret, "UTF-8"), bytes(f"{state_time}-{state}", "UTF-8"), hashlib.sha256).hexdigest()
		if hmac.compare_digest(token, new_token):
			if os.path.isfile(os.path.join(config.dir, 'data', 'chatgpt', 'images', f'{image_id}.png')):
				with open(os.path.join(config.dir, 'data', 'chatgpt', 'images', f'{image_id}.png'), 'rb') as f:
					image_data = f.read()
					return web.Response(status=200,body=image_data, content_type="image/png")
			else:
				return web.Response(status=404,text="")
		else:
			return web.Response(status=401,text="")

	async def chat_image(self, request:web.Request, image_id:str):
		if os.path.isfile(os.path.join(config.dir, 'data', 'chatgpt', 'images', f'{image_id}.png')):
			with open(os.path.join(config.dir, 'data', 'chatgpt', 'images', f'{image_id}.png'), 'rb') as f:
				image_data = f.read()
				return web.Response(status=200,body=image_data, content_type="image/png")
		else:
			return web.Response(status=404,text="")

	@routes.route(path="/subscription/{path:.*}", method="GET")
	async def subscription(self, request:web.Request):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")
		with open(os.path.join(config.dir, 'html',"subscription.html"), "r") as d:
			c = d.read()
		return web.Response(status=200,body=c, content_type="text/html")

	@routes.route(path="/subscription", method="GET")
	async def subscription_list(self, request:web.Request):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")
		with open(os.path.join(config.dir, 'html',"subscription_list.html"), "r") as d:
			c = d.read()
		return web.Response(status=200,body=c, content_type="text/html")

	async def ytsubscription(self, request:web.Request, channel_id:str):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")
		with sqlite3.connect(os.path.join(config.dir, 'database', 'youtube.db')) as db:
			secret = db.execute('SELECT secret FROM subscribe WHERE id = ?', (channel_id,)).fetchone()
			if secret is None:
				return web.Response(status=404,text="")
			channel_name = db.execute('SELECT name FROM subscribe WHERE id = ?', (channel_id,)).fetchone()[0]
		async with aiohttp.ClientSession() as s:
			async with s.get(f"https://pubsubhubbub.appspot.com/subscription-details?hub.callback=https%3A%2F%2F{config.domain}%2Fwebhook%2Fyoutube%2F{channel_id}&hub.topic=https%3A%2F%2Fwww.youtube.com%2Fxml%2Ffeeds%2Fvideos.xml%3Fchannel_id%3D{channel_id}&hub.secret={secret[0]}") as r:
				r.raise_for_status()
				html_content = await r.read()
				s = BeautifulSoup(html_content, 'html.parser')
				soup = s.find('body')
				subscription_details = {}
				subscription_details["Name"] = channel_name
				subscription_details["Topic URL"] = soup.find('p', class_='lead').string.strip()
				subscription_dl = soup.find('dl', class_='glue-body glue-body--large')
				if subscription_dl:
					dt_elements = subscription_dl.find_all('dt')
					dd_elements = subscription_dl.find_all('dd')
					for dt, dd in zip(dt_elements, dd_elements):
						subscription_details[dt.string.strip()] = dd.string.strip()

				last_item_dl = soup.find_all('dl', class_='glue-body glue-body--large')[-1]
				subscription_details["Last content received"] = last_item_dl.find('dt', string="Content received").find_next_sibling('dd').string.strip()
				subscription_details["Last content delivered"] = last_item_dl.find('dt', string="Content delivered").find_next_sibling('dd').string.strip()

				for title, subscription_time in subscription_details.items():
					match = re.search(r"([a-zA-Z]+, \d+ [a-zA-Z]+ \d+ \d+:\d+:\d+ \+\d+)", subscription_time)
					if match:
						datetime_format = "%a, %d %b %Y %H:%M:%S %z"
						format_dt = datetime.datetime.strptime(match.group(1), datetime_format).astimezone(tz=tz)
						subscription_details[title] = subscription_time.replace(match.group(1), format_dt.strftime("%Y-%m-%d %H:%M:%S"))
				return web.Response(status=200,body=json.dumps(subscription_details, ensure_ascii=False), content_type="application/json")

	@routes.get("/login")
	@aiohttp_jinja2.template("login.html")
	async def login_page(s, request:web.Request):
		return {"error": None}

	@routes.post("/login")
	async def login_post(s, request:web.Request):
		if request.content_type != "application/x-www-form-urlencoded":
			return web.HTTPUnsupportedMediaType(text="")
		try:
			data = await request.post()
			user = data["user"]
			pwd = data["password"]
		except:
			return web.HTTPUnprocessableEntity(text="")

		try:
			logging.warning(f"A unknown user want login from web\nUser: {user}\nPassword: {pwd}")
			return aiohttp_jinja2.render_template("login.html", request, {"error": "Invalid account or password", "user": user})

		except Exception:
			return aiohttp_jinja2.render_template("login.html", request, {"error": "A server error has occurred. Please try again later.", "user": user})

	async def run(self):
		app = web.Application(middlewares=[self.rdns], client_max_size=8*(1024**2))
		app.add_routes(self.routes)
		aiohttp_jinja2.setup(app=app,loader=jinja2.FileSystemLoader("html"))
		self.runner = web.AppRunner(app,access_log_format='%{X-Real-IP}i "%{X-Method}i" %s %{Content-Length}i "%{User-Agent}i" (%D)',access_log = self.logger)
		await self.runner.setup()
		self.site = web.TCPSite(self.runner, host='localhost',port=3000)
		self.clean_db_task = asyncio.create_task(self.clean_db())
		await self.site.start()

	async def clean_db(self):
		while True:
			now = int(datetime.datetime.now().timestamp())
			self.webappdb.execute('DELETE FROM block WHERE expires < ?', (now,))
			self.webappdb.execute('DELETE FROM session WHERE last_use < ?', (now - 2592000,))
			self.webappdb.execute('DELETE FROM state WHERE expires < ?', (now,))
			self.webappdb.execute('DELETE FROM passkey_challenges WHERE expires_at < ?', (now,))
			self.webappdb.execute('DELETE FROM revoke WHERE expires < ?', (now,))
			self.webappdb.commit()
			repost = self.webappdb.execute('SELECT * FROM repost').fetchall()
			if len(repost) > 0:
				for post in repost:
					post_id, post_type, post_data, repost_time = post
					self.webappdb.execute('DELETE FROM repost WHERE id = ?', (post_id,))
					self.webappdb.commit()
					if repost_time >= 5:
						continue
					logging.info(f"Try repost {post_type} {post_id}")
					if post_type == "youtube":
						asyncio.create_task(yt.youtube(video_id=post_id, youtubedb=self.youtubedb, repost_time=repost_time, channel_id=post_data))
					elif post_type == "twitch":
						postdata = json.loads(post_data)
						if postdata['subscription']['type'] == "stream.online":
							asyncio.create_task(twitch.notification(postdata, post_id, repost_time))
						elif postdata['subscription']['type'] == "stream.offline":
							asyncio.create_task(twitch.offline(postdata, post_id, repost_time))
			premiere = self.webappdb.execute('SELECT * FROM premiere').fetchall()
			if len(premiere) > 0:
				for video in premiere:
					video_id, publish_time, channel_id = video
					if now > publish_time:
						self.webappdb.execute('DELETE FROM premiere WHERE id = ?', (video_id,))
						self.webappdb.commit()
						asyncio.create_task(yt.youtube(video_id=video_id, youtubedb=self.youtubedb, channel_id=channel_id))
			await asyncio.sleep(600)

async def setup(bot):
	global webapp
	webapp = APP(bot)
	await bot.add_cog(webapp)
	webapp.task = asyncio.create_task(webapp.run())

async def teardown(bot):
	global webapp
	if webapp is not None:
		await webapp.site.stop()
		await webapp.runner.shutdown()
		await webapp.runner.cleanup()
		webapp.task.cancel()
		webapp.clean_db_task.cancel()
		webapp.youtubedb.close()
		webapp.iddb.close()
		webapp.webappdb.close()
		for handler in webapp.logger.handlers:
			handler.close()
			webapp.logger.removeHandler(handler)
		webapp = None