import discord
import json
import aiohttp
import config
import datetime
import os
import random
import sends
import logging
import asyncio
import yarl
import sqlite3

lock = asyncio.Lock()

async def new_token(session:aiohttp.ClientSession) -> str:
	url = "https://id.twitch.tv/oauth2/token"
	headers = {
		'Content-Type': 'application/x-www-form-urlencoded'
	}
	data = f"client_id={config.twitch_id}&client_secret={config.twitch_secret}&grant_type=client_credentials"
	async with session.post(url=url, headers=headers, data=data) as r:
		r.raise_for_status
		js = await r.json()
		token = js['access_token']
		expires = int(datetime.datetime.now().timestamp()) + js['expires_in']
		d = {
			'token': token,
			'expires': expires
		}
		with open(os.path.join(config.dir, 'youtube', 'twitch', 'token.json'), 'w') as f:
			json.dump(d, f, indent=2)
		return token

async def token_validate(session:aiohttp.ClientSession) -> str:
	with open(os.path.join(config.dir, 'youtube', 'twitch', 'token.json'), 'r') as f:
		d = json.load(f)
		token = d['token']
	url = "https://id.twitch.tv/oauth2/validate"
	headers = {
		'Authorization': f'OAuth {token}'
	}
	async with session.get(url=url, headers=headers) as r:
		if r.status == 200:
			return token
		elif r.status == 401:
			token = await new_token(session)
			return token

async def notification(data:dict, msgid:str, repost_time:int | None=None):
	global lock
	async with lock:
		with open(os.path.join(config.dir, 'youtube', 'twitch', 'last_msg.json'), 'r') as f:
			last_id = json.load(f)
		if msgid in last_id:
			return
		userid = data['subscription']['condition']['broadcaster_user_id']
		async with aiohttp.ClientSession() as session:
			token = await token_validate(session)
			url = f"https://api.twitch.tv/helix/streams?user_id={userid}&type=live"
			headers = {
				'Authorization': f'Bearer {token}',
				'Client-Id': config.twitch_id
			}
			try:
				async with session.get(url=url, headers=headers) as r:
					r.raise_for_status()
					js = await r.json()
					game_name = f"{js['data'][0]['game_name']} | " if js['data'][0].get('game_name') else ""
					title = js['data'][0]['title']
					started_at = js['data'][0]['started_at']
					thumbnail_url = js['data'][0]['thumbnail_url'].replace("{width}x{height}", "1280x720")
			except Exception as e:
				logging.warning(f"Check twitch stream failed!! | Reason: {e}\nData: {js}\nMessage ID:{msgid}")
				repost(msgid, data, repost_time)
				return
			url = f'https://api.twitch.tv/helix/users?id={userid}'
			try:
				async with session.get(url, headers=headers) as r:
					r.raise_for_status()
					js = await r.json()
					name = js['data'][0]['display_name']
					avatar = js['data'][0]['profile_image_url']
					curl = f"https://www.twitch.tv/{js['data'][0]['login']}"
			except Exception as e:
				logging.warning(f"Check twitch user info failed!! | Reason: {e}\nData: {js}\nMessage ID:{msgid}")
				return
			try:
				async with session.get(url=thumbnail_url) as r:
					r.raise_for_status()
					thumbnail = await r.read()
			except Exception as e:
				logging.warning(f"Get twitch stream thumbnail failed!! | Reason: {e}\nMessage ID:{msgid}")
				repost(msgid, data, repost_time)
				return

			image_random = "".join(random.sample("0123456789abcdef", 6))
			embed = discord.Embed(title=title, url=curl, color=0x9147FF)
			embed.set_author(name=name, icon_url=avatar)
			embed.set_image(url=f"attachment://{image_random}_thumbnail.jpg")
			embed.set_footer(text=f"{game_name}直播開始時間")
			embed.timestamp = datetime.datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%S%z")
			msg = {
				'embeds': [embed.to_dict()],
				'username': name,
				'avatar_url': avatar,
				'attachments':[
					{
						"id": 0,
						"filename": f"{image_random}_thumbnail.jpg"
					}
				],
				"allowed_mentions": {
    			"parse": []
				}
			}
			formdata = aiohttp.FormData()
			formdata.add_field(name="payload_json", value=json.dumps(msg), content_type="application/json")
			formdata.add_field(name="files[0]", value=thumbnail, filename=f"{image_random}_thumbnail.jpg", content_type="image/jpeg")
			try:
				discord_msgid = await sends.by_webhook(config.twitch_webhook, formdata)
			except:
				repost(msgid, data, repost_time)
				return
			with open(os.path.join(config.dir, 'youtube', 'twitch', 'last_msg.json'), 'w') as f:
				last_id.append(msgid)
				if len(last_id) > 250:
					last_id = last_id[-250:]
				json.dump(last_id, f, indent=2)
			with sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db')) as iddb:
				iddb.execute('UPDATE twitch SET msgid = ? WHERE userid = ?', (discord_msgid, userid))
				iddb.commit()

async def offline(data:dict, msgid:str, repost_time:int | None=None):
	global lock
	async with lock:
		with open(os.path.join(config.dir, 'youtube', 'twitch', 'last_msg.json'), 'r') as f:
			last_id = json.load(f)
		if msgid in last_id:
			return
		userid = data['subscription']['condition']['broadcaster_user_id']
		with sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db')) as iddb:
			discord_msgid = iddb.execute('SELECT msgid FROM twitch WHERE userid = ?', (userid,)).fetchone()
			if discord_msgid is None:
				return
			else:
				discord_msgid = discord_msgid[0]
		message = await sends.get_message(f"{config.twitch_webhook}/messages/{discord_msgid}", authorization=False)
		if message is None:
			return
		old_embed = discord.Embed().from_dict(message['embeds'][0])
		if "已關台" in old_embed.footer.text:
			return
		embed_image = yarl.URL(old_embed.image.url).path
		image_name = embed_image.split("/")[-1]
		image_id = embed_image.split("/")[-2]

		embed = discord.Embed(title=old_embed.title, url=old_embed.url, color=0x9147FF)
		embed.set_author(name=old_embed.author.name, icon_url=old_embed.author.icon_url)
		embed.set_image(url=f"attachment://{image_name}")
		embed.set_footer(text=f'已關台 | {old_embed.footer.text}')
		embed.timestamp = old_embed.timestamp

		message_send = {
			'embeds': [embed.to_dict()],
			'attachments':[
				{
					"id": image_id,
					"filename": image_name
				}
			],
			"allowed_mentions": {
				"parse": []
			}
		}
		try:
			await sends.update_bywebhook(config.twitch_webhook, discord_msgid, message_send)
		except:
			repost(msgid, data, repost_time)
			return
		with open(os.path.join(config.dir, 'youtube', 'twitch', 'last_msg.json'), 'w') as f:
			last_id.append(msgid)
			if len(last_id) > 250:
				last_id = last_id[-250:]
			json.dump(last_id, f, indent=2)

async def revocation(data:dict):
	with sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db')) as iddb:
		userid = data['subscription']['condition']['broadcaster_user_id']
		name = iddb.execute('SELECT * FROM twitch WHERE userid = ?', (userid,))
		reason = data['subscription']['status']
		embed = discord.Embed(title="Twitch subscription was revoked!", description=f'User: {name}\nReason: {reason.replace("_", " ")}', color=0xFF5733)
		msg = {"embeds": [embed.to_dict()]}
		await sends.by_bot(config.server_info, msg)
		iddb.execute('DELETE FROM twitch WHERE userid = ?', (userid,))
		iddb.commit()

def repost(id:str, data:dict, repost_time:int | None):
	if repost_time is None:
		repost_time = 1
	else:
		repost_time += 1
	with sqlite3.connect(os.path.join(config.dir, 'database', 'webapp.db')) as db:
		try:
			db.execute('INSERT INTO repost VALUES (?, ?, ?, ?)', (id, "twitch", json.dumps(data, ensure_ascii=False), repost_time))
			db.commit()
		except sqlite3.IntegrityError:
			pass