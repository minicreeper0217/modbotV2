import discord
from discord.ext import commands
import asyncio
import config
import json
import aiohttp
from datetime import datetime
from time import perf_counter
import timecount
import sends
import os
import logging
import random
import uuid
from PIL import Image as PILImage
from io import BytesIO
import sqlite3
import status_page
import schedule

class Misskey(commands.Cog):
	def __init__(self, bot):
		self.bot:commands.Bot = bot
		self.misskey_lock = asyncio.Lock()
		self.misskey_reconnect = []
		self.misskey_api = True
		self.ws = None
		self.misskey_status_id = status_page.get_component_id("Misskey")
		self.status_page = True
		self.misskeydb = sqlite3.connect(os.path.join(config.dir, 'database', 'misskey.db'), isolation_level=None)
		self.iddb = sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db'), isolation_level=None)
		self.misskeydb.execute('PRAGMA auto_vacuum = FULL')
		self.misskeydb.execute('VACUUM')
		schedule.every(7).days.at("06:00").do(lambda: asyncio.create_task(self.auto_cleanup_db())).tag("cleanup_misskey_db")
		self.tasks = [
			asyncio.create_task(self.auto_misskey()),
		]

	async def misskey_http(self):
		if not self.misskey_api:
			return
		with open(os.path.join(config.dir, 'data', 'misskey', 'restart_id.json'), 'r') as f:
			resid = json.load(f)
			restart_id = resid['user']
			time = resid['time']
		misskey_user_id = self.iddb.execute('SELECT id FROM misskey').fetchall()
		now = int(datetime.now().timestamp()*1000)
		async with aiohttp.ClientSession() as session:
			end_point = 'https://misskey.io/api/antennas/notes'
			params = {
				'antennaId': config.misskey_antenna,
				'limit': 100,
				'sinceDate': time,
				'i': config.misskey
			}
			headers = {
				'Content-Type': 'application/json'
			}
			try:
				async with session.post(end_point, headers=headers, data=json.dumps(params)) as r:
					r.raise_for_status()
					notes:list[dict] = await r.json()
					notes.reverse()
					for note in notes:
						new = note['userId'] not in restart_id
						await self.misskey_send(note=note,newid=new)
			except Exception as e:
				logging.warning(f"Check misskey note Failed!! Reason: {e.__class__.__name__}: {e}")
				return
			else:
				resid['user'] = []
				for userid in misskey_user_id:
					resid['user'].append(userid[0])
				resid['time'] = now
				with open(os.path.join(config.dir, 'data', 'misskey', 'restart_id.json'), 'w') as f:
					json.dump(resid, f, indent=2)

			cursor = self.misskeydb.execute('SELECT * FROM repost').fetchall()
			if len(cursor) > 0:
				for datas in cursor:
					noteid = datas[0]
					data = json.loads(datas[1])
					self.misskeydb.execute('DELETE FROM repost WHERE id = ?', (noteid,))
					self.misskeydb.commit()
					await self.misskey_send(note=data)

	async def misskey_websocket(self):
		re = False
		head = False
		retime = 0
		uid = str(uuid.uuid4())
		async with aiohttp.ClientSession() as session:
			while True:
				if len(self.misskey_reconnect) >= 5:
					reconnect_copy = self.misskey_reconnect
					reconnect_copy.append(datetime.now().timestamp())
					reconnect_all = sum((reconnect_copy[i] - reconnect_copy[i-1] for i in range(2, len(reconnect_copy)))) / (len(reconnect_copy) - 2)
					if reconnect_all < 200:
						retime += random.uniform(450,750)
						logging.warning(f"Misskey.io websocket reconnection limit has been reached. Reconnect in {round(retime,ndigits=2)}s")
						try:
							await status_page.create_incident(
								name = "Misskey websocket reconnection limit has been reached", 
								page_status = status_page.IDENTIFIED, 
								component_id = self.misskey_status_id, 
								component_status = status_page.PARTIAL_OUTAGE, 
								message = f"Misskey.io websocket reconnection limit has been reached. Reconnect in {round(retime,ndigits=2)}s", 
								reminder = False
							)
							self.status_page = False
						except:
							logging.exception("Update misskey status incident failed!!")
						await asyncio.sleep(retime)
						retime = 0
						self.misskey_reconnect = []
						continue
				if head:
					headers = {
						'Content-Type': 'application/json'
					}
					params = {}
					try:
						async with session.post(url="https://misskey.io/api/ping", data=json.dumps(params), headers=headers) as r:
							r.raise_for_status()
					except aiohttp.ClientResponseError as e:
						retime += random.uniform(15,30)
						logging.warning(f"Can't to ping misskey.io api. Reconnect in {round(retime,ndigits=2)}s | Reason: {e}")
						self.misskey_api = False
						await asyncio.sleep(retime)
						continue
					else:
						head = False
						self.misskey_api = True
				try:
					async with session.ws_connect(f"wss://misskey.io/streaming?i={config.misskey}",heartbeat=22.5,timeout=30,autoclose=False,max_msg_size=0) as ws:
						data = {
							"type": "connect",
							"body": {
								"channel": "localTimeline",
								"id": uid,
								"params": {}
							}
						}
						await ws.send_json(data)
						if not re and not ws.closed:
							logging.info(f"Connect to misskey.io websocket successfully (uuid: {uid})")
						elif re and not ws.closed:
							logging.info(f"Reconnect to misskey.io websocket successfully (uuid: {uid})")
						if not self.status_page:
							try:
								incident_data = await status_page.update_incident(
									page_status=status_page.RESOLVED,
									component_id=self.misskey_status_id,
									component_status=status_page.OPERATIONAL,
									message="Reconnect to misskey.io websocket successfully"
								)
								await status_page.delete_incident(component_id=self.misskey_status_id, incident_id=incident_data["id"])
								self.status_page = True
							except:
								logging.exception("Update misskey status incident failed!!")
						re = True
						retime = 0
						times = perf_counter()
						count = 0
						self.ws = ws
						self.misskey_reconnect.append(datetime.now().timestamp())
						if len(self.misskey_reconnect) > 5:
							self.misskey_reconnect = self.misskey_reconnect[-5:]
						async for msg in ws:
							if ws.closed:
								raise Exception("Connection closed!")
							d = msg.json()
							if msg.type == aiohttp.WSMsgType.TEXT and d['body'].get('type') == "note":
								count += 1
							if count >= 10:
								timee = perf_counter()
								timec = timee - times
								asyncio.create_task(timecount.special("misskey", timec))
								times = perf_counter()
								count = 0
							asyncio.create_task(self.misskey_check(msg,uid))
				except aiohttp.WSServerHandshakeError as e:
					retime += random.uniform(1,5)
					logging.warning(f"Can't to connect misskey.io websocket. Reconnect in {round(retime,ndigits=2)}s | Reason: {e.__class__.__name__}: {e}")
					head = True
					await asyncio.sleep(retime)
					continue
				except Exception as e:
					retime += random.uniform(1,5)
					logging.warning(f"misskey.io websocket connection closed! Reconnect in {round(retime,ndigits=2)}s | Reason: {e.__class__.__name__}: {e}")
					await asyncio.sleep(retime)
					continue

	async def misskey_check(self, msg:aiohttp.WSMessage, uid:uuid.UUID):
		data = msg.json()
		if msg.type == aiohttp.WSMsgType.TEXT and data['type'] == 'channel' and data['body']['id'] == uid and data['body']['type'] == "note":
			user = data['body']['body']['user']['id']
			user_cursor = self.iddb.execute('SELECT * FROM misskey WHERE id = ?', (user,)).fetchone()
			if user_cursor is not None:
				note = data['body']['body']
				await self.misskey_send(note=note)
				if user_cursor[1] != note['user']['name']:
					self.iddb.execute('UPDATE misskey SET name = ? WHERE id = ?', (note['user']['name'],user))
					self.iddb.commit()
		elif msg.type == aiohttp.WSMsgType.TEXT and data['type'] == 'announcementCreated':
			asyncio.create_task(self.misskey_announcement(data['body']['announcement']))
		elif not (msg.type == aiohttp.WSMsgType.TEXT and 'emoji' in data.get('type')):
			logging.info(f"Another data recvied: {msg}")

	async def misskey_send(self,note:dict,newid:bool=False):
		async with self.misskey_lock:
			original = note
			filetype = [
				'image/jpeg',
				'image/png',
				'image/gif',
				'image/webp'
			]
			renote = False
			drenote = False
			reply = False
			quote = False
			nsfw = False
			filecount = 0
			avatar_id = -1
			attachments = []
			formdata = aiohttp.FormData()
			name = note['user']['name']
			username = note['user']['username']
			avatar_url = note['user']['avatarUrl']
			userid = note['user']['id']
			ruserid = userid
			note_id = note['id']
			if note.get('reply'):
				if note['reply']['replyId'] is not None:
					return
				else:
					replyid = note['id']
					qlink = f'https://misskey.io/notes/{replyid}'
					qtext = note['text']
					qcw = note['cw']
					qfile = note['files']
					qtime = datetime.strptime(note['createdAt'], "%Y-%m-%dT%H:%M:%S.%f%z")
					note_id = note['id']
					note = note['reply']
					note_link = f'https://misskey.io/notes/{note["id"]}'
					reply = True
			elif note.get('renote'):
				if note['cw'] is not None or note['text'] is not None or len(note['files']) > 0:
					qid = note['id']
					qlink = f'https://misskey.io/notes/{qid}'
					qtext = note['text']
					qcw = note['cw']
					qfile = note['files']
					qtime = datetime.strptime(note['createdAt'], "%Y-%m-%dT%H:%M:%S.%f%z")
					note_id = note['id']
					quote = True
				else:
					renote_id = note['id']
					renote_link = f'https://misskey.io/notes/{renote_id}'
					renote = True
				note = note['renote']
				ruserid = note['user']['id']
				if quote:
					note_link = f'https://misskey.io/notes/{note["id"]}'
				if (note.get('renote') or note.get('reply')) and not quote:
					qid = note['id']
					qlink = f'https://misskey.io/notes/{qid}'
					qtext = note['text']
					qcw = note['cw']
					qfile = note['files']
					qtime = datetime.strptime(note['createdAt'], "%Y-%m-%dT%H:%M:%S.%f%z")
					note_id = note['id']
					name = note['user']['name']
					username = note['user']['username']
					avatar_url = note['user']['avatarUrl']
					userhost = note['user']['host']
					if userhost is not None:
						username = f"{username}@{userhost}"
					note = note.get('renote') or note.get('reply')
					note_link = f'https://misskey.io/notes/{note["id"]}'
					drenote = True
			if not reply and not drenote and not quote:
				note_id = note['id']
				note_link = f'https://misskey.io/notes/{note_id}'
			if self.misskeydb.execute('SELECT * FROM misskey WHERE id = ?', (note_id,)).fetchone() is not None:
				return
			userchannel = self.misskeydb.execute('SELECT channel, renote FROM userchannel WHERE id = ?', (userid,)).fetchone()
			if userchannel is None:
				return
			else:
				user_channel = userchannel[0]
				canrenote = userchannel[1]
			if not canrenote and (drenote or renote or quote) and ruserid != userid:
				return
			if len(note['files']) > 0 and user_channel == 0 or user_channel == 1:
				note_name = note['user']['name']
				note_username = note['user']['username']
				note_avatar_url = note['user']['avatarUrl']
				note_host = note['user']['host']
				created_at = datetime.strptime(note['createdAt'], "%Y-%m-%dT%H:%M:%S.%f%z")
				if note_host is not None:
					note_username = f"{note_username}@{note_host}"
				if note['cw'] is not None and note['text'] is not None:
					text = f"{note['cw']}\n{note['text']}"
				elif note['cw'] is not None:
					text = note['cw']
				elif note['text'] is not None:
					text = note['text']
				else:
					text = ""

				embed_list = []
				embed_color = 0x86B300
				qembed_color = 0x86B300
				has_image = False
				setembedlink = True
				if reply:
					content = f'Replying @{note_username}'
					embed_link = qlink
				elif quote:
					content = f'Quoted @{note_username}'
					embed_link = qlink
				elif drenote or renote:
					n = original['user']['username']
					h = original['user']['host']
					if h is not None:
						n = f"{n}@{h}"
					content = f'Renoted by @{n}'
					embed_link = renote_link
				else:
					content = f'Noted'
					embed_link = note_link

				if reply or quote or drenote:
					if qcw is not None and qtext is not None:
						qutext:str = f"{qcw}\n{qtext}"
					elif qcw is not None:
						qutext = qcw
					elif qtext is not None:
						qutext = qtext
					else:
						qutext = ""

					if setembedlink:
						qlink = embed_link
						setembedlink = False
						qembed = discord.Embed(description=qutext, color=qembed_color,url=qlink, title=content)
						embed_color = 0xA5B084
					else:
						qembed = discord.Embed(description=qutext, color=qembed_color,url=qlink)
						embed_color = 0xB7CC76
					qembed.timestamp = qtime
					try:
						avatar_data = await self.misskey_avatar(avatar_url)
						avatar_name = f'{"".join(random.sample("0123456789abcdef", 6))}_avatar.png'
						avatar_id += 1
						qembed.set_author(name=f'{name} (@{username})', url=f'https://misskey.io/@{username}', icon_url=f'attachment://{avatar_name}')
						formdata.add_field(name=f"files[{avatar_id}]", value=avatar_data, filename=avatar_name, content_type="image/png")
						attachments.append({"id": avatar_id,"filename": avatar_name})
					except:
						qembed.set_author(name=f'{name} (@{username})', url=f'https://misskey.io/@{username}', icon_url=avatar_url)
					if len(qfile) > 0:
						qfilecount = 0
						qimage_count = ""
						qicount = 0
						qvcount = 0
						qncount = 0
						for f in qfile:
							if "image" in f['type']:
								qicount += 1
							elif "video" in f['type']:
								qvcount += 1
							else:
								qncount += 1
						if qicount > 0:
							qimage_count += f'📷{qicount} | '
						if qvcount > 0:
							qimage_count += f'🎬{qvcount} | '
						if qncount > 0:
							qimage_count += f'📁{qncount} | '
						for qf in qfile:
							if qf['isSensitive']:
								nsfw = True
							if qfilecount == 0:
								if qf['type'] in filetype:
									qembed.set_image(url=qf['url'])
									has_image = True
								elif qf['type'] not in filetype and qf.get("thumbnailUrl"):
									qembed.set_image(url=qf['thumbnailUrl'])
									has_image = True
								else:
									pass
								qembed.set_footer(text=f'{qimage_count}發佈時間')
								embed_list.append(qembed.to_dict())
								qfilecount += 1
							else:
								qembed = discord.Embed(url=qlink)
								if qf['type'] in filetype:
									qembed.set_image(url=qf['url'])
									has_image = True
								elif qf['type'] not in filetype and qf.get("thumbnailUrl"):
									qembed.set_image(url=qf['thumbnailUrl'])
									has_image = True
								else:
									continue
								embed_list.append(qembed.to_dict())
								qfilecount += 1
								if qfilecount == 4:
									break
					else:
						qembed.set_footer(text='發佈時間')
						embed_list.append(qembed.to_dict())

				if len(note['files']) > 0:
					image_count = ""
					icount = 0
					vcount = 0
					ncount = 0
					for f in note['files']:
						if "image" in f['type']:
							icount += 1
						elif "video" in f['type']:
							vcount += 1
						else:
							ncount += 1
					if icount > 0:
						image_count += f'📷{icount} | '
					if vcount > 0:
						image_count += f'🎬{vcount} | '
					if ncount > 0:
						image_count += f'📁{ncount} | '
					for file in note['files']:
						if file['isSensitive']:
							nsfw = True
						if filecount == 0:
							if setembedlink:
								note_link = embed_link
								setembedlink = False
								embed = discord.Embed(description=text, color=embed_color, url=note_link, title=content)
							else:
								embed = discord.Embed(description=text, color=embed_color, url=note_link)
							embed.set_footer(text=f'{image_count}發佈時間')
							try:
								note_avatar_data = await self.misskey_avatar(note_avatar_url)
								note_avatar_name = f'{"".join(random.sample("0123456789abcdef", 6))}_avatar.png'
								avatar_id += 1
								embed.set_author(name=f'{note_name} (@{note_username})', url=f'https://misskey.io/@{note_username}', icon_url=f'attachment://{note_avatar_name}')
								formdata.add_field(name=f"files[{avatar_id}]", value=note_avatar_data, filename=note_avatar_name, content_type="image/png")
								attachments.append({"id": avatar_id,"filename": note_avatar_name})
							except:
								embed.set_author(name=f'{note_name} (@{note_username})', url=f'https://misskey.io/@{note_username}', icon_url=note_avatar_url)
							if file['type'] in filetype:
								embed.set_image(url=file['url'])
								has_image = True
							elif file['type'] not in filetype and file.get("thumbnailUrl"):
								embed.set_image(url=file['thumbnailUrl'])
								has_image = True
							else:
								pass
							embed.timestamp = created_at
							embed_list.append(embed.to_dict())
							filecount += 1
						else:
							embed = discord.Embed(url=note_link)
							if file['type'] in filetype:
								embed.set_image(url=file['url'])
								has_image = True
							elif file['type'] not in filetype and file.get("thumbnailUrl"):
								embed.set_image(url=file['thumbnailUrl'])
								has_image = True
							else:
								continue
							embed_list.append(embed.to_dict())
							filecount += 1
							if filecount == 4:
								break
				else:
					if setembedlink:
						note_link = embed_link
						setembedlink = False
						embed = discord.Embed(description=text, color=embed_color, url=note_link, title=content)
					else:
						embed = discord.Embed(description=text, color=embed_color, url=note_link)
					try:
						note_avatar_data = await self.misskey_avatar(note_avatar_url)
						note_avatar_name = f'{"".join(random.sample("0123456789abcdef", 6))}_avatar.png'
						avatar_id += 1
						embed.set_author(name=f'{note_name} (@{note_username})', url=f'https://misskey.io/@{note_username}', icon_url=f'attachment://{note_avatar_name}')
						formdata.add_field(name=f"files[{avatar_id}]", value=note_avatar_data, filename=note_avatar_name, content_type="image/png")
						attachments.append({"id": avatar_id,"filename": note_avatar_name})
					except:
						embed.set_author(name=f'{note_name} (@{note_username})', url=f'https://misskey.io/@{note_username}', icon_url=note_avatar_url)
					embed.set_footer(text='發佈時間')
					embed.timestamp = created_at
					embed_list.append(embed.to_dict())

				if user_channel == 0 and not has_image:
					return
				if user_channel == 0 or nsfw:
					webhook = config.misskey_webhook
				elif user_channel == 1:
					webhook = config.misskeyall_webhook
				if drenote:
					name = original['user']['name']
					avatar_url = original['user']['avatarUrl']
				message_send = {
					'embeds': embed_list,
					'username': name,
					'avatar_url': avatar_url,
					'attachments': attachments,
					"allowed_mentions": {
						"parse": []
					}
				}
				if not newid:
					try:
						formdata.add_field(name="payload_json", value=json.dumps(message_send), content_type="application/json")
						await sends.by_webhook(webhook, formdata)
						self.misskeydb.execute('INSERT INTO misskey VALUES (?, ?)', (note_id, int(created_at.timestamp())))
						self.misskeydb.commit()
						with open(os.path.join(config.dir, 'data', 'misskey', 'statistic.json'), 'r+') as f:
							statistic:dict = json.load(f)
							statistic.setdefault(userid, {"name": name, "times": 0})
							sendtimes = statistic[userid]['times'] + 1
							statistic[userid] = {
								"name": name,
								"times": sendtimes
							}
							f.seek(0)
							json.dump(statistic, f, indent=2, ensure_ascii=False)
							f.truncate()
					except Exception as e:
						if isinstance(e, aiohttp.ClientResponseError) and e.status == 413:
							try:
								self.misskeydb.execute('INSERT INTO misskey VALUES (?, ?)', (note_id, int(created_at.timestamp())))
								self.misskeydb.commit()
							except sqlite3.IntegrityError: pass
							finally: return
						logging.warning(f'Send misskey note failed!\nNote ID: {note_id}')
						try:
							self.misskeydb.execute('INSERT INTO repost VALUES (?, ?)', (note_id,json.dumps(original, ensure_ascii=False)))
							self.misskeydb.commit()
						except sqlite3.IntegrityError: pass
						return
				elif newid:
					try:
						self.misskeydb.execute('INSERT INTO misskey VALUES (?, ?)', (note_id, int(created_at.timestamp())))
						self.misskeydb.commit()
					except sqlite3.IntegrityError:
						pass

	async def misskey_avatar(self, url:str) -> bytes:
		async with aiohttp.ClientSession() as s:
			async with s.get(url)as r:
				r.raise_for_status()
				data = await r.read()
		with PILImage.open(BytesIO(data)) as img:
			frame = img.convert("RGBA").getdata()
			new_img = PILImage.new("RGBA", img.size)
			new_img.putdata(list(frame))
			output_buffer = BytesIO()
			new_img.convert("RGBA").save(output_buffer, format='PNG')
			image_data = output_buffer.getvalue()
		return image_data

	async def misskey_announcement(self, data:dict):
		embed = discord.Embed(title=data['title'], description=data['text'], color=0x86B300)
		embed.set_author(name="お知らせ")
		if data.get('updatedAt'):
			embed.timestamp = datetime.strptime(data['updatedAt'], "%Y-%m-%dT%H:%M:%S.%f%z")
			embed.set_footer(text=f"{data['icon']} | 更新時間")
		else:
			embed.timestamp = datetime.strptime(data['createdAt'], "%Y-%m-%dT%H:%M:%S.%f%z")
			embed.set_footer(text=f"{data['icon']} | 發布時間")
		if data.get('imageUrl') and isinstance(data['imageUrl'], str):
			embed.set_image(url=data['imageUrl'])
		message_send = {
			'embeds': [embed.to_dict()],
			'username': "お知らせ",
			"allowed_mentions": {
				"parse": []
			}
		}
		try:
			await sends.by_webhook(config.misskeyall_webhook, message_send)
		except Exception as e:
			logging.warning(f"Send misskey announcement failed!! | Reason: {e}\nData: {data}")

	async def auto_misskey(self):
		self.tasks.append(asyncio.create_task(self.misskey_websocket()))
		while True:
			task = [asyncio.create_task(self.misskey_http())]
			await asyncio.wait(task)
			if self.ws is None:
				await asyncio.sleep(900)
			elif self.ws.closed:
				await asyncio.sleep(300)
			else:
				await asyncio.sleep(900)

	async def auto_cleanup_db(self):
		limit = 100000
		try:
			self.misskeydb.execute("DELETE FROM misskey WHERE created_at NOT IN (SELECT created_at FROM misskey ORDER BY created_at DESC LIMIT ?)", (limit,))
			self.misskeydb.commit()
		except Exception as e:
			logging.error(f"Auto cleanup misskey DB Failed!! | Reason: {e.__class__.__name__}: {e}")

async def setup(bot):
	global misskeycog
	misskeycog = Misskey(bot)
	await bot.add_cog(misskeycog)

async def teardown(bot):
	global misskeycog
	if misskeycog is not None:
		tasks = misskeycog.tasks
		for task in tasks:
			task.cancel()
		misskeycog.iddb.close()
		schedule.clear("cleanup_misskey_db")
		misskeycog = None