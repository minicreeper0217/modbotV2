import discord
from discord.ext import commands
import asyncio
import aiohttp
import config
import os
import json
import logging
from datetime import datetime
import sends
import random
import pytz
import sqlite3

atprotocog = None

class ATproto(commands.Cog):
	handle:str = ...
	apppassword:str = ...
	accesstoken:str = ...
	refreshtoken:str = ...
	def __init__(self, bot:commands.Bot):
		self.bot = bot
		self.network = config.bluesky_bot_pds # Personal Data Server
		self.blueskydb = sqlite3.connect(os.path.join(config.dir, 'database', 'bluesky.db'), isolation_level=None)
		self.blueskydb.execute('PRAGMA auto_vacuum = FULL')
		self.blueskydb.execute('VACUUM')
		with open(os.path.join(config.dir, "data", "atproto", "token.json"), "r") as f:
			d = json.load(f)
			self.handle = d['handle']
			self.apppassword = d['apppassword']
			self.accesstoken = d["accesstoken"]
			self.refreshtoken = d['refreshtoken']
		self.tasks = [asyncio.create_task(self.feed())]

	async def login(self, session:aiohttp.ClientSession, *, handle:str=None, apppassword:str=None) -> str | dict:
		own = False
		if handle is None or apppassword is None:
			handle = self.handle
			apppassword = self.apppassword
			own = True
		data = {
			"identifier": handle,
			"password": apppassword
		}
		headers = {
			'Content-Type': 'application/json'
		}
		async with session.post(f"https://bsky.social/xrpc/com.atproto.server.createSession", headers=headers, data=json.dumps(data)) as r:
			r.raise_for_status()
			js = await r.json()
			if own:
				self.accesstoken = js['accessJwt']
				self.refreshtoken = js['refreshJwt']
				with open(os.path.join(config.dir, "data", "atproto", "token.json"), "r+") as f:
					d = json.load(f)
					d['did'] = js['did']
					d['accesstoken'] = js['accessJwt']
					d['refreshtoken'] = js['refreshJwt']
					f.seek(0)
					json.dump(d, f, indent=2)
					f.truncate()
				logging.info("Login to bluesky successfully")
				return js['accessJwt']
			else:
				return js

	async def getsession(self, session:aiohttp.ClientSession, *, accesstoken:str=None, refreshtoken:str=None, **kwargs) -> str | dict:
		own = False
		if accesstoken is None or refreshtoken is None:
			accesstoken = self.accesstoken
			refreshtoken = self.refreshtoken
			own = True
		headers = {
			'Authorization': f'Bearer {accesstoken}'
		}
		async with session.get(f"https://bsky.social/xrpc/com.atproto.server.getSession", headers=headers) as r:
			if r.status == 401:
				token = await self.login(session=session, **kwargs)
				return token
			if r.status == 400:
				js = await r.json()
				if js.get("error") == "ExpiredToken":
					headers = {
						'Authorization': f'Bearer {refreshtoken}'
					}
					async with session.post(f"https://bsky.social/xrpc/com.atproto.server.refreshSession", headers=headers) as re:
						if re.status >= 400:
							token = await self.login(session=session, **kwargs)
							return token
						j = await re.json()
						if own:
							self.accesstoken = j['accessJwt']
							self.refreshtoken = j['refreshJwt']
							with open(os.path.join(config.dir, "data", "atproto", "token.json"), "r+") as f:
								d = json.load(f)
								d['accesstoken'] = j['accessJwt']
								d['refreshtoken'] = j['refreshJwt']
								f.seek(0)
								json.dump(d, f, indent=2)
								f.truncate()
							logging.info("Refresh bluesky token successfully")
							return j['accessJwt']
						else:
							return j
			r.raise_for_status()
			return accesstoken

	async def feed(self):
		token = self.accesstoken
		retry = False
		async with aiohttp.ClientSession() as s:
				while True:
					count = 0
					dids = self.blueskydb.execute('SELECT did, filter, etag FROM user').fetchall()
					for didata in dids:
						did = didata[0]
						filter = didata[1]
						headers = {
							'Authorization': f'Bearer {token}',
						}
						try:
							async with s.get(f"https://{self.network}/xrpc/app.bsky.feed.getAuthorFeed?actor={did}&limit=30&filter={filter}", headers=headers) as r:
								if r.status == 400 or r.status == 403:
									token = await self.getsession(session=s)
									break
								elif r.status >= 500:
									break
								r.raise_for_status()
								new = didata[2] is None
								json_data = await r.json()
								await self.feedsend(json_data['feed'], new)
						except Exception as e:
							if retry:
								logging.exception(f"Bluesky feed is closed! | {did}")
								return
							else:
								logging.warning(f"Bluesky feed error! | Reason: {e.__class__.__name__}: {e} | {did}")
								retry = True
								count = 0
								await asyncio.sleep(60)
								break
						else:
							count += 1
							await asyncio.sleep(1)
					if count == len(dids):
						self.blueskydb.commit()
						retry = False
						await asyncio.sleep(random.uniform(30,60))

	async def feedsend(self, posts:list[dict], newid:bool=False):
		default_avatar = "https://cdn.discordapp.com/embed/avatars/0.png"
		posts.reverse()
		for post in posts:
			if post['post']['author']['did'] == "did:plc:4hqjfn7m6n5hno3doamuhgef" and all(x in post['post']['record']['text'] for x in ['start', 'raid']) and not any(x in post['post']['record']['text'] for x in ['raid-start', 'status', 'battle', 'server']):
				await self.raid(post)
			if post['post']['record'].get("reply") and post['post']['author']['did'] == "did:plc:4hqjfn7m6n5hno3doamuhgef":
				continue
			id = post['post']['uri'].split("/")[-1]
			cursor = self.blueskydb.execute('SELECT * FROM postid WHERE id = ?', (id,)).fetchone()
			if cursor is not None:
				continue
			repost = False
			reply = False
			quote = False
			post_images = None
			post_video = None
			post_text = post['post']['record']['text']
			post_at = post['post']['indexedAt']
			if post['post']['record'].get('facets'):
				old_text = bytes(post_text, encoding="utf-8")
				for facets in post['post']['record']['facets']:
					linkuri = None
					for features in facets['features']:
						if features['$type'] == "app.bsky.richtext.facet#link":
							linkuri = features['uri']
					if not linkuri:
						continue
					shortlink = old_text[facets['index']['byteStart']:facets['index']['byteEnd']]
					post_text = post_text.replace(shortlink.decode(encoding="utf-8"), linkuri, 1)
			if post.get("reason"):
				handle = post['reason']['by']['handle']
				name = post['reason']['by'].get('displayName') or handle
				avatar = post['reason']['by'].get('avatar') or default_avatar
				if post['reason']['$type'] == "app.bsky.feed.defs#reasonRepost":
					repost = True
			else:
				handle = post['post']['author']['handle']
				name = post['post']['author'].get('displayName') or handle
				avatar = post['post']['author'].get('avatar') or default_avatar
			post_author_handle = post['post']['author']['handle']
			post_author_name = post['post']['author'].get('displayName') or post_author_handle
			post_author_avatar = post['post']['author'].get('avatar') or default_avatar
			embed = post['post'].get("embed")
			if post['post']['record'].get("reply"):
				reply = True
				if post.get("reply") and post['reply']['parent']['$type'] == "app.bsky.feed.defs#postView":
					quote_author_handle = post['reply']['parent']['author']['handle']
					quote_author_name = post['reply']['parent']['author'].get('displayName') or quote_author_handle
					quote_author_avatar = post['reply']['parent']['author'].get('avatar') or default_avatar
					quote_text = post['reply']['parent']['record']['text']
					quote_at = post['reply']['parent']['indexedAt']
					quote_id = post['reply']['parent']['uri'].split("/")[-1]
					quote_image = None
					if post['reply']['parent'].get("embed"):
						if post['reply']['parent']["embed"]['$type'] == "app.bsky.embed.recordWithMedia#view" and post['reply']['parent']["embed"]['media']['$type'] == "app.bsky.embed.images#view":
							quote_image = post['reply']['parent']["embed"]['media']['images']
						elif post['reply']['parent']["embed"]['$type'] == "app.bsky.embed.images#view":
							quote_image = post['reply']['parent']["embed"]['images']
					if post['reply']['parent']['record'].get('facets'):
						old_text = bytes(quote_text, encoding="utf-8")
						for facets in post['reply']['parent']['record']['facets']:
							linkuri = None
							for features in facets['features']:
								if features['$type'] == "app.bsky.richtext.facet#link":
									linkuri = features['uri']
							if not linkuri:
								continue
							shortlink = old_text[facets['index']['byteStart']:facets['index']['byteEnd']]
							quote_text = quote_text.replace(shortlink.decode(encoding="utf-8"), linkuri, 1)
					quote = True
			if embed is not None:
				if embed['$type'] == "app.bsky.embed.record#view" and embed['record']['$type'] == "app.bsky.embed.record#viewRecord":
					quote_author_handle = embed['record']['author']['handle']
					quote_author_name = embed['record']['author'].get('displayName') or quote_author_handle
					quote_author_avatar = embed['record']['author'].get('avatar') or default_avatar
					quote_text = embed['record']['value']['text']
					quote_at = embed['record']['indexedAt']
					quote_id = embed['record']['uri'].split("/")[-1]
					quote_image = None
					if embed['record'].get("embeds"):
						for em in embed['record']["embeds"]:
							if em['$type'] == "app.bsky.embed.images#view":
								quote_image = em['images']
					if embed['record'].get('facets'):
						old_text = bytes(quote_text, encoding="utf-8")
						for facets in  embed['record']['value']['facets']:
							linkuri = None
							for features in facets['features']:
								if features['$type'] == "app.bsky.richtext.facet#link":
									linkuri = features['uri']
							if not linkuri:
								continue
							shortlink = old_text[facets['index']['byteStart']:facets['index']['byteEnd']]
							quote_text = quote_text.replace(shortlink.decode(encoding="utf-8"), linkuri, 1)
					quote = True
				elif embed['$type'] == "app.bsky.embed.recordWithMedia#view" and embed['record']['record']['$type'] == "app.bsky.embed.record#viewRecord":
					quote_author_handle = embed['record']['record']['author']['handle']
					quote_author_name = embed['record']['record']['author'].get('displayName') or quote_author_handle
					quote_author_avatar = embed['record']['record']['author'].get('avatar') or default_avatar
					quote_text = embed['record']['record']['value']['text']
					quote_at = embed['record']['record']['indexedAt']
					quote_id = embed['record']['record']['uri'].split("/")[-1]
					quote = True
					if embed['media']['$type'] == "app.bsky.embed.images#view":
						post_images = embed['media']['images']
					quote_image = None
					if embed['record']['record'].get("embeds"):
						for em in embed['record']['record']["embeds"]:
							if em['$type'] == "app.bsky.embed.images#view":
								quote_image = em['images']
					if embed['record']['record']['value'].get('facets'):
						old_text = bytes(quote_text, encoding="utf-8")
						for facets in  embed['record']['record']['value']['facets']:
							linkuri = None
							for features in facets['features']:
								if features['$type'] == "app.bsky.richtext.facet#link":
									linkuri = features['uri']
							if not linkuri:
								continue
							shortlink = old_text[facets['index']['byteStart']:facets['index']['byteEnd']]
							quote_text = quote_text.replace(shortlink.decode(encoding="utf-8"), linkuri, 1)
				elif embed['$type'] == "app.bsky.embed.images#view":
						post_images = embed['images']
				elif embed['$type'] == "app.bsky.embed.video#view":
						post_video = embed['thumbnail']
				else:
					if all(embed['$type'] != a for a in ["app.bsky.embed.external#view", "app.bsky.embed.record#viewNotFound"]):
						logging.info(f"Another type found\n{embed}\n{post['post']['uri']}")

			embed_list = []
			e = discord.Embed(description=post_text, url=f"https://bsky.app/profile/{post_author_handle}/post/{id}", color=0x3AFFFE)
			e.set_author(name=f"{post_author_name} (@{post_author_handle})", icon_url=post_author_avatar, url=f"https://bsky.app/profile/{post_author_handle}")
			e.timestamp = datetime.strptime(post_at, "%Y-%m-%dT%H:%M:%S.%f%z")
			if post_images is not None:
				image_count = 0
				for image in post_images:
					if image_count == 0:
						e.set_image(url=image['fullsize'])
						e.set_footer(text=f"📷{len(post_images)} | 發布時間")
						embed_list.append(e.to_dict())
						image_count += 1
					else:
						e = discord.Embed(url=f"https://bsky.app/profile/{post_author_handle}/post/{id}")
						e.set_image(url=image['fullsize'])
						embed_list.append(e.to_dict())
						image_count += 1
						if image_count == 4:
							break
			elif post_video is not None:
				e.set_image(url=post_video)
				e.set_footer(text="🎬1 | 發布時間")
				embed_list.append(e.to_dict())
			else:
				e.set_footer(text="發布時間")
				embed_list.append(e.to_dict())

			if quote:
				qe = discord.Embed(description=quote_text, url=f"https://bsky.app/profile/{quote_author_handle}/post/{quote_id}", color=0xA3CCCC)
				qe.set_author(name=f"{quote_author_name} (@{quote_author_handle})",icon_url=quote_author_avatar , url=f"https://bsky.app/profile/{quote_author_handle}")
				qe.timestamp = datetime.strptime(quote_at, "%Y-%m-%dT%H:%M:%S.%f%z")
				if quote_image is not None:
					image_count = 0
					for image in quote_image:
						if image_count == 0:
							qe.set_image(url=image['fullsize'])
							qe.set_footer(text=f"📷{len(quote_image)} | 發布時間")
							embed_list.append(qe.to_dict())
							image_count += 1
						else:
							qe = discord.Embed(url=f"https://bsky.app/profile/{quote_author_handle}/post/{quote_id}")
							qe.set_image(url=image['fullsize'])
							embed_list.append(qe.to_dict())
							image_count += 1
							if image_count == 4:
								break
				else:
					qe.set_footer(text=f"發布時間")
					embed_list.append(qe.to_dict())

			for e in embed_list:
				if e.get('description') and any(kw in e['description'] for kw in ["card.syui.ai", "@yui.syui.ai"]):
					return

			if reply and not repost:
				if quote:
					content = f'[Replying]({f"https://bsky.app/profile/{post_author_handle}/post/{id}"}) [@{quote_author_handle}]({f"https://bsky.app/profile/{quote_author_handle}/post/{quote_id}"})'
				else:
					content = f'[Replying]({f"https://bsky.app/profile/{post_author_handle}/post/{id}"})'
			elif quote and repost:
				content = f'[Reposted @{post_author_handle}]({f"https://bsky.app/profile/{post_author_handle}/post/{id}"}) [@{quote_author_handle}]({f"https://bsky.app/profile/{quote_author_handle}/post/{quote_id}"})'
			elif quote:
				content = f'[Quoted]({f"https://bsky.app/profile/{post_author_handle}/post/{id}"}) [@{quote_author_handle}]({f"https://bsky.app/profile/{quote_author_handle}/post/{quote_id}"})'
			elif repost:
				content = f'[Reposted @{post_author_handle}]({f"https://bsky.app/profile/{post_author_handle}/post/{id}"})'
			else:
				content = f'[Posted]({f"https://bsky.app/profile/{post_author_handle}/post/{id}"})'
			message_send = {
				'content': content,
				'embeds': embed_list,
				'username': name,
				'avatar_url': avatar,
				"allowed_mentions": {
    			"parse": []
				}
			}
			if not newid:
				try:
					await sends.by_webhook(config.bluesky_webhook, message_send)
					self.blueskydb.execute('INSERT INTO postid VALUES (?)', (id,))
					self.blueskydb.execute('UPDATE user SET etag = ? WHERE handle = ?', (id, post_author_handle))
					self.blueskydb.commit()
				except:
					logging.warning(f'Send bluesky post failed!\n{json.dumps(message_send, indent=2, ensure_ascii=False)}')
					continue
			elif newid:
				try:
					self.blueskydb.execute('INSERT INTO postid VALUES (?)', (id,))
					self.blueskydb.execute('UPDATE user SET etag = ? WHERE handle = ?', (id, post_author_handle))
					self.blueskydb.commit()
				except sqlite3.IntegrityError:
					pass

	async def raid(self, post:dict):
		with open(os.path.join(config.dir, "data", "atproto", "raid.json"), "r") as f:
			d = json.load(f)
			handle = d['handle']
			did = d['did']
			apppassword = d['apppassword']
			accesstoken = d["accesstoken"]
			refreshtoken = d['refreshtoken']
			lastraid:list = d['lastraid']
			raidtime = d['raidtime']
			raidlock = d['raidlock']
		id = post['post']['uri'].split("/")[-1]
		t = datetime.now(pytz.timezone('Asia/Tokyo')).strftime("%Y-%m-%d")
		if not raidlock or id in lastraid or raidtime == t:
			return
		try:
			async with aiohttp.ClientSession() as s:
				tokens = await self.getsession(session=s, accesstoken=accesstoken, refreshtoken=refreshtoken, handle=handle, apppassword=apppassword)
				if isinstance(tokens, dict):
					accesstoken = tokens['accessJwt']
					refreshtoken = tokens['refreshJwt']
				headers = {
					'Authorization': f'Bearer {accesstoken}'
				}
				async with s.get(f"https://{config.bluesky_admin_pds}/xrpc/app.bsky.notification.listNotifications?limit=30", headers=headers) as r:
					r.raise_for_status()
					js = await r.json()
					cid = None
					uri = None
					for notification in js['notifications']:
						if notification['reason'] == "reply" and notification['author']['did'] == "did:plc:4hqjfn7m6n5hno3doamuhgef":
							cid = notification['cid']
							uri = notification['uri']
							break
					if cid is None or uri is None:
						logging.warning("No post can reply for card raid!!")
						return
					now = datetime.now(pytz.UTC)
					data = {
  					"repo": did,
  					"collection": "app.bsky.feed.post",
  					"record": {
    					"text": "/card r",
							"$type": "app.bsky.feed.post",
    					"createdAt": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    					"reply": {
      					"root": {
        					"cid": cid,
        					"uri": uri
      					},
      					"parent": {
        					"cid": cid,
        					"uri": uri
      					}
    					}
  					}
					}
					headers['Content-Type'] = "application/json"
					async with s.post(f"https://{config.bluesky_admin_pds}/xrpc/com.atproto.repo.createRecord", headers=headers, data=json.dumps(data)) as r:
						r.raise_for_status()
						lastraid.append(id)
						if len(lastraid) > 50:
							lastraid = lastraid[-50:]
		except:
			logging.exception("Play with card raid Failed!!")
			return
		finally:
			with open(os.path.join(config.dir, "data", "atproto", "raid.json"), "w") as f:
				d["accesstoken"] = accesstoken
				d['refreshtoken'] = refreshtoken
				d['lastraid'] = lastraid
				d['raidtime'] = t
				json.dump(d, f, indent=2)

async def setup(bot):
	global atprotocog
	atprotocog = ATproto(bot)
	await bot.add_cog(atprotocog)

async def teardown(bot):
	global atprotocog
	if atprotocog is not None:
		tasks = atprotocog.tasks
		for task in tasks:
			task.cancel()
		atprotocog.blueskydb.close()
		atprotocog = None