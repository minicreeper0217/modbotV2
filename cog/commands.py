import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import json
import config
import sends
import datetime
import pytz
import os
import aiohttp
from discord.ui import Button
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import youtube.twitch as twitch
import hashlib
import re
import hmac
import logging
import sqlite3
import secrets

tz = pytz.timezone('Asia/Taipei')

class Command(commands.Cog):
	def __init__(self, bot):
		self.bot:commands.Bot = bot

	async def checkcd(interaction:discord.Interaction):
		if interaction.user.id == config.ownerid:
			return None
		else:
			match interaction.command.name:
				case "embed_message":
					return app_commands.Cooldown(rate=1, per=600)
				case "verify":
					return app_commands.Cooldown(rate=3, per=600)
				case _:
					return app_commands.Cooldown(rate=1, per=10)

	@app_commands.command(name='ping')
	@app_commands.checks.dynamic_cooldown(checkcd,key=lambda interaction: interaction.user.id)
	async def ping(self, ctx:discord.Interaction):
		await ctx.response.defer(ephemeral=True)
		headers = {
			'Content-Type': 'application/json'
		}
		async with aiohttp.ClientSession() as s:
			n = datetime.datetime.now().timestamp()
			try:
				async with s.post("https://misskey.io/api/ping",data="{}", headers=headers) as r:
					r.raise_for_status()
					d = await r.json()
					misskey = f"{d['pong'] - int(n*1000)} ms"
			except:
				misskey = "Connection Failed!"
		embed = discord.Embed(title="Pong! :ping_pong:", color=0x02FEBF)
		embed.add_field(name="Discord", value=f"```json\n{round(self.bot.latency * 1000, 3)} ms\n```", inline=False)
		embed.add_field(name="Misskey", value=f"```json\n{misskey}\n```", inline=False)
		await ctx.followup.send(embed=embed)

	@app_commands.command(name="luck")
	@app_commands.checks.dynamic_cooldown(checkcd,key=lambda interaction: interaction.user.id)
	async def luck(self, ctx:discord.Interaction):
		await ctx.response.defer(ephemeral=True)
		num = random.randint(1, 7)
		match num:
			case 1: await ctx.followup.send(content="大吉 :thumbsup:", ephemeral=True)
			case 2: await ctx.followup.send(content="中吉 :smiley:", ephemeral=True)
			case 3: await ctx.followup.send(content="末吉 :slight_smile:",ephemeral=True)
			case 4: await ctx.followup.send(content="平 :neutral_face:", ephemeral=True)
			case 5: await ctx.followup.send(content="小凶 :frowning2:", ephemeral=True)
			case 6: await ctx.followup.send(content="大凶 :skull:", ephemeral=True)
			case _:
				num2 = random.randint(1, 100)
				if num2 == 98: await ctx.followup.send(content="什...什麼 程式好像出問題了 :skull_crossbones: ", ephemeral=True)
				else: await ctx.followup.send(content="中凶 :tired_face:",ephemeral=True)

	@app_commands.command(name="rock_paper_scissors")
	@app_commands.checks.dynamic_cooldown(checkcd,key=lambda interaction: interaction.user.id)
	async def rock_paper_scissors(self, ctx:discord.Interaction, choice: str):
		await ctx.response.defer(ephemeral=True)
		bot_choice = random.choice(['石頭', '布', '剪刀'])
		if choice == bot_choice:
			await ctx.followup.send(content="平手! :cat:", ephemeral=True)
		elif (choice == '石頭' and bot_choice == '剪刀') or (choice == '布' and bot_choice == '石頭') or (choice == '剪刀' and bot_choice == '布'):
			await ctx.followup.send(content=f"我輸了 我出{bot_choice} :skull_crossbones:", ephemeral=True)
		else:
			await ctx.followup.send(content=f"我贏了 我出{bot_choice} :v:",ephemeral=True)

	@app_commands.command(name='clear')
	async def clear(self, *args:discord.Interaction):
		await args[0].response.defer(ephemeral=True)
		for option in args[0].data['options']:
			match option['name']:
				case 'channel':
					channel = self.bot.get_channel(int(option['value']))
					channel_id = int(option['value'])
				case 'member':
					member = option['value']
				case 'amount':
					amount = int(option['value'])

		messages = []
		a = False
		async for message in channel.history():
			message_created_at = message.created_at.timestamp()
			current_time = datetime.datetime.now(tz).timestamp()
			time_diff = current_time - message_created_at
			if message.author.id == int(member) and time_diff < 1209000:
				messages.append(message)
				if len(messages) == amount: break
			elif time_diff >= 1209600: break

		if channel_id not in config.do_not_delete_channel:
			try:
				await channel.delete_messages(messages)
				await args[0].followup.send(f"<@{member}> 's {len(messages)} messages has been deleted.", ephemeral=True)
				a = True
			except Exception as e:
				await args[0].followup.send(content=f"messages delete Failed. {e}")
		else:
			await args[0].followup.send("NO!! you can't do that!!!", ephemeral=True)

		if a == True:
			embed = discord.Embed(title=f'{args[0].user.display_name} used clear command', description=f'Channel: <#{channel_id}>\nMember: <@{member}>\nAmount: {len(messages)}', color=0xFF0000)
			avatar_url = args[0].user.display_avatar.url
			embed.set_author(name=args[0].user.display_name, icon_url=avatar_url)
			embed.set_footer(text='訊息刪除時間')
			embed.timestamp = datetime.datetime.now(tz)

			message_send = {
				'embeds': [embed.to_dict()],
			}
			try: await sends.by_bot(config.server_info, message_send)
			except: pass

	id_ = app_commands.Group(name='id',description="Add or Remove ids")

	@id_.command(name="show")
	async def id_show(self, ctx:discord.Interaction, type:str):
		await ctx.response.defer(ephemeral=True)
		with sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db')) as db:
			datas = db.execute(f'SELECT id, name FROM {type}').fetchall()
			ids = ''
			count = 0
			for data in datas:
				count += 1
				if count == len(datas):
					ids += f"- {data[1]} ({data[0]})"
				else:
					ids += f"- {data[1]} ({data[0]})\n"
			embed = discord.Embed(title="Here you are -->" ,description=ids, color=discord.Colour(196287))
			await ctx.followup.send(embed=embed, ephemeral=True)

	@id_.command(name="youtube")
	async def youtube(self, ctx:discord.Interaction, mode:str, handle:str, channel:int | None = None):
		await ctx.response.defer(ephemeral=True)
		with sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db')) as iddb, sqlite3.connect(os.path.join(config.dir, 'database', 'youtube.db')) as youtubedb:
			curl = f"https://youtube.googleapis.com/youtube/v3/channels?part=snippet&forHandle={handle}&maxResults=1&key={config.youtube_api}"
			try:
				async with aiohttp.ClientSession() as session:
					async with session.get(curl) as r:
						r.raise_for_status()
						data = await r.json()
						id = data['items'][0]['id']
						name = data['items'][0]['snippet']['title']
			except (aiohttp.ClientResponseError, KeyError, IndexError):
				await ctx.followup.send(f"Invalid channel id", ephemeral=True)
				return
			indatabase = iddb.execute('SELECT * FROM youtube WHERE id = ?',(id,)).fetchone()
			if not indatabase and mode == "unsubscribe":
				await ctx.followup.send(f"This channel is not subscribed", ephemeral=True)
				return
			elif indatabase and mode == "subscribe":
				await ctx.followup.send(f"This channel is already subscribed", ephemeral=True)
				return
			if mode == "subscribe":
				if channel is None:
					await ctx.followup.send(f"NO! You are not set a channel", ephemeral=True)
					return
				secret = secrets.token_hex(32)
				youtubedb.execute('INSERT INTO subscribe VALUES (?, ?, ?, ?, ?)', (id, name, int(datetime.datetime.now().timestamp()), secret, channel))
				youtubedb.commit()
			elif mode == "unsubscribe":
				secret = youtubedb.execute('SELECT secret FROM subscribe WHERE id = ?',(id,)).fetchone()[0]
			verify_token = hmac.new(bytes(config.secret,"utf-8"), bytes(secret,"utf-8"), hashlib.sha256).hexdigest()
			url = f"https://pubsubhubbub.appspot.com/subscribe?hub.callback=https%3A%2F%2F{config.domain}%2Fwebhook%2Fyoutube%2F{id}&hub.topic=https%3A%2F%2Fwww.youtube.com%2Fxml%2Ffeeds%2Fvideos.xml%3Fchannel_id%3D{id}&hub.verify=async&hub.mode={mode}&hub.verify_token={verify_token}&hub.secret={secret}&hub.lease_numbers=432000"
			try:
				async with aiohttp.ClientSession() as session:
					async with session.post(url) as r:
						r.raise_for_status()
						if mode == "subscribe":
							iddb.execute('INSERT INTO youtube VALUES (?, ?)', (id, name))
						elif mode == "unsubscribe":
							name = iddb.execute('SELECT name FROM youtube WHERE id = ?',(id,)).fetchone()[0]
							iddb.execute('DELETE FROM youtube WHERE id = ?',(id,))
						youtubedb.commit()
						iddb.commit()
						await ctx.followup.send(f"{name}\nsuccessfully", ephemeral=True)
			except Exception as e:
				await ctx.followup.send(f"Failed! Reason: {e}", ephemeral=True)

	@id_.command(name="misskey")
	async def misskey(self, ctx:discord.Interaction, mode:int, id:str, channel:int=None, renote:int=1):
		await ctx.response.defer(ephemeral=True)
		if mode == 1 and channel is None:
			await ctx.followup.send(f"NO! You are not set a channel", ephemeral=True)
			return
		with sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db')) as iddb, sqlite3.connect(os.path.join(config.dir, 'database', 'misskey.db')) as misskeydb:
			indatabase = iddb.execute('SELECT * FROM misskey WHERE id = ?',(id,)).fetchone() is not None
			if not indatabase and mode == 0:
				await ctx.followup.send(f"This user is not followed", ephemeral=True)
				return
			elif indatabase and mode == 1:
				await ctx.followup.send(f"This user is already followed", ephemeral=True)
				return
			end_point = 'https://misskey.io/api/users/show'
			headers = {
				'Content-Type': 'application/json'
			}
			params = {
				'userId': id,
				'i': config.misskey
			}
			async with aiohttp.ClientSession() as session:
				try:
					async with session.post(end_point,headers=headers ,data=json.dumps(params)) as r:
						r.raise_for_status()
						data = await r.json()
						name = data['name']
						username = data['username']
						if data['host'] is not None:
							user = f"@{username}@{data['host']}"
						else:
							user = f"@{username}"
				except (aiohttp.ClientResponseError, KeyError):
					await ctx.followup.send(f"Invalid misskey user id", ephemeral=True)
					iddb.close()
					return
				params = {
					'antennaId': config.misskey_antenna,
					'i': config.misskey
				}
				try:
					end_point = "https://misskey.io/api/antennas/show"
					async with session.post(end_point, headers=headers, data=json.dumps(params)) as r:
						r.raise_for_status()
						antenna = await r.json()
						del antenna['createdAt']
						del antenna['id']
						del antenna['hasUnreadNote']
						del antenna['isActive']
						antenna['antennaId'] = config.misskey_antenna
						antenna['i'] = config.misskey
					if mode == 1:
						antenna['users'].append(user)
						antenna['users'].sort(key=str.lower)
					elif mode == 0:
						antenna['users'].remove(user)
					end_point = "https://misskey.io/api/antennas/update"
					async with session.post(end_point, headers=headers, data=json.dumps(antenna)) as r:
						r.raise_for_status()
				except Exception as e:
					await ctx.followup.send(f"Update antenna failed! | Reason:{e}", ephemeral=True)
					return
				else:
					if mode == 1:
						iddb.execute('INSERT INTO misskey VALUES (?, ?)', (id, name))
						misskeydb.execute('INSERT INTO userchannel VALUES (?, ?, ?)', (id, channel, renote))
					elif mode == 0:
						iddb.execute('DELETE FROM misskey WHERE id = ?', (id,))
						misskeydb.execute('DELETE FROM userchannel WHERE id = ?', (id,))
					misskeydb.commit()
					iddb.commit()
			await ctx.followup.send(f"{name} ({user})\nsuccessfully", ephemeral=True)

	@id_.command(name="fantia")
	async def fantia(self, ctx:discord.Interaction, mode:int, id:str):
		await ctx.response.defer(ephemeral=True)
		with sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db')) as iddb:
			indatabase = iddb.execute('SELECT * FROM fantia WHERE id = ?',(id,)).fetchone() is not None
			if not indatabase and mode == 0:
				await ctx.followup.send(f"This fanclub is not followed", ephemeral=True)
				return
			elif indatabase and mode == 1:
				await ctx.followup.send(f"This fanclub is already followed", ephemeral=True)
				return
			if mode == 1:
				end_point = f"https://fantia.jp/fanclubs/{id}"
				headers = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"}
				try:
					async with aiohttp.ClientSession() as session:
						async with session.get(end_point,headers=headers) as r:
							r.raise_for_status()
							dt = await r.text()
							soup = BeautifulSoup(dt, 'html.parser')
							fanclub = soup.find('div',class_="module fanclub fanclub-sm")
							name = (fanclub.find('source'))['alt']
							iddb.execute('INSERT INTO fantia VALUES (?, ?)', (id, name))
				except (aiohttp.ClientResponseError, AttributeError):
					await ctx.followup.send(f"Invalid fanclub id", ephemeral=True)
					return
			elif mode == 0:
				name = iddb.execute('SELECT name FROM fantia WHERE id = ?',(id,)).fetchone()[0]
				iddb.execute('DELETE FROM fantia WHERE id = ?', (id,))
			iddb.commit()
			await ctx.followup.send(f"{name}\nsuccessfully", ephemeral=True)

	@id_.command(name="twitch")
	async def twitch(self, ctx:discord.Interaction, mode:int, id:str):
		await ctx.response.defer(ephemeral=True)
		async with aiohttp.ClientSession() as session:
			token = await twitch.token_validate(session)
			url = f'https://api.twitch.tv/helix/users?login={id}'
			headers = {
				'Authorization': f'Bearer {token}',
				'Client-Id': config.twitch_id
			}
			with sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db')) as iddb:
				try:
					async with session.get(url, headers=headers) as r:
						r.raise_for_status()
						js = await r.json()
						name = js['data'][0]['display_name']
						userid = js['data'][0]['id']
					indatabase = iddb.execute('SELECT * FROM twitch WHERE userid = ?',(userid,)).fetchone() is not None
					if not indatabase and mode == 0:
						await ctx.followup.send(f"This user is not followed", ephemeral=True)
						return
					elif indatabase and mode == 1:
						await ctx.followup.send(f"This user is already followed", ephemeral=True)
						return
				except (aiohttp.ClientResponseError, IndexError, KeyError):
					await ctx.followup.send(f"Invalid twitch user id", ephemeral=True)
					return
				if mode == 1:
					url = "https://api.twitch.tv/helix/eventsub/subscriptions"
					headers = {
						'Authorization': f'Bearer {token}',
						'Client-Id': config.twitch_id,
						'Content-Type': 'application/json'
					}
					data = {
						"type": "stream.online",
						"version": "1",
						"condition": {
							"broadcaster_user_id": str(userid)
						},
						"transport": {
							"method": "webhook",
							"callback": f"https://{config.domain}/webhook/twitch/{userid}",
							"secret": config.secret
						}
					}
					try:
						async with session.post(url, headers=headers, data=json.dumps(data)) as r:
							r.raise_for_status()
							j = await r.json()
							subscriptionid_online = j['data'][0]['id']
					except Exception as e:
						await ctx.followup.send(f"Failed!! | Reason {e}", ephemeral=True)
						return
					data['type'] = "stream.offline"
					try:
						async with session.post(url, headers=headers, data=json.dumps(data)) as r:
							r.raise_for_status()
							j = await r.json()
							subscriptionid_offline = j['data'][0]['id']
					except Exception as e:
						del headers['Content-Type']
						async with session.delete(f"https://api.twitch.tv/helix/eventsub/subscriptions?id={subscriptionid_online}", headers=headers):
							pass
						await ctx.followup.send(f"Failed!! | Reason {e}", ephemeral=True)
						return
					iddb.execute('INSERT INTO twitch VALUES (?, ?, ?, ?, ?, ?)', (id,name,userid,subscriptionid_online,subscriptionid_offline, None))
					iddb.commit()
					await ctx.followup.send(f"{name}\nsuccessfully", ephemeral=True)
				elif mode == 0:
					subids = iddb.execute('SELECT online, offline FROM twitch WHERE userid = ?',(userid,)).fetchone()
					online_id = subids[0]
					offline_id = subids[1]
					url = f"https://api.twitch.tv/helix/eventsub/subscriptions?id={online_id}"
					try:
						async with session.delete(url, headers=headers) as r:
							r.raise_for_status()
					except Exception as e:
						await ctx.followup.send(f"Failed!! | Reason {e}", ephemeral=True)
						return
					url = f"https://api.twitch.tv/helix/eventsub/subscriptions?id={offline_id}"
					try:
						async with session.delete(url, headers=headers) as r:
							r.raise_for_status()
					except Exception as e:
						await ctx.followup.send(f"Failed!! | Reason {e}", ephemeral=True)
						return
					iddb.execute('DELETE FROM twitch WHERE userid = ?', (userid,))
					iddb.commit()
					await ctx.followup.send("successfully", ephemeral=True)

	@id_.command(name="fanbox")
	async def fanbox(self, ctx:discord.Interaction, mode:int, id:str):
		await ctx.response.defer(ephemeral=True)
		with sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db')) as iddb:
			indatabase = iddb.execute('SELECT * FROM fanbox WHERE id = ?',(id,)).fetchone() is not None
			if not indatabase and mode == 0:
				await ctx.followup.send(f"This fanbox user is not followed", ephemeral=True)
				return
			elif indatabase and mode == 1:
				await ctx.followup.send(f"This fanbox user is already followed", ephemeral=True)
				return
			if mode == 1:
				async with aiohttp.ClientSession() as session:
					async with session.get(f"https://www.fanbox.cc/@{id}/posts") as r:
						tx = await r.text()
						soup = BeautifulSoup(tx, 'html.parser')
						title = soup.find('title').string
						pattern = r"｜([^｜]*)｜"
						match = re.search(pattern, title)
						if match:
							name = match.group(1)
							iddb.execute('INSERT INTO fanbox VALUES (?, ?)', (id, name))
						else:
							iddb.execute('INSERT INTO fanbox VALUES (?, ?)', (id, id))
							name = id
			elif mode == 0:
				name = iddb.execute('SELECT name FROM fanbox WHERE id = ?',(id,)).fetchone()[0]
				iddb.execute('DELETE FROM fanbox WHERE id = ?', (id,))
			iddb.commit()
			await ctx.followup.send(f"{name}\nsuccessfully", ephemeral=True)

	@id_.command(name="bluesky")
	async def bluesky(self, ctx:discord.Interaction, mode:int, id:str, filter:str=None):
		await ctx.response.defer(ephemeral=True)
		if mode == 1 and filter is None:
			await ctx.followup.send(f"NO! You are not set a filter", ephemeral=True)
			return
		p = r"^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$"
		if re.match(p, id) is None:
			await ctx.followup.send(f"Invalid user handle", ephemeral=True)
			return
		with sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db')) as iddb, sqlite3.connect(os.path.join(config.dir, 'database', 'bluesky.db')) as blueskydb:
			indatabase = iddb.execute('SELECT * FROM bluesky WHERE id = ?',(f'@{id}',)).fetchone() is not None
			if not indatabase and mode == 0:
				await ctx.followup.send(f"This user is not followed", ephemeral=True)
				return
			elif indatabase and mode == 1:
				await ctx.followup.send(f"This user is already followed", ephemeral=True)
				return
			if mode == 1:
				async with aiohttp.ClientSession() as s:
					at = self.bot.get_cog('ATproto')
					if at is None:
						await ctx.followup.send(f"Error! Please try again later", ephemeral=True)
						return
					accesstoken = await at.getsession(session=s)
					headers = {
						'Authorization': f'Bearer {accesstoken}'
					}
					try:
						async with s.get(f"https://bsky.social/xrpc/app.bsky.actor.getProfile?actor={id}", headers=headers) as r:
							js = await r.json()
							if r.status == 400:
								if js.get("error") == "InvalidRequest":
									await ctx.followup.send(f"Invalid user handle", ephemeral=True)
									return
								elif js.get("error") == "ExpiredToken":
									await ctx.followup.send(f"Error! Please try again later", ephemeral=True)
									return
							r.raise_for_status()
							did = js['did']
							handle = js['handle']
							name = js.get('displayName') or handle
							h = f"@{handle}"
							iddb.execute('INSERT INTO bluesky VALUES (?, ?)', (h, name))
							blueskydb.execute('INSERT INTO user VALUES (?, ?, ?, ?, ?)', (did, handle, name, filter, None))
							iddb.commit()
							blueskydb.commit()
							await ctx.followup.send(f"{name}\nsuccessfully", ephemeral=True)
					except Exception as e:
						await ctx.followup.send(f"Failed!! | Reason {e}", ephemeral=True)
			elif mode == 0:
				h = f"@{id}"
				blueskydb.execute('DELETE FROM user WHERE handle = ?', (id,))
				iddb.execute('DELETE FROM bluesky WHERE id = ?', (h,))
				iddb.commit()
				blueskydb.commit()
				await ctx.followup.send(f"successfully", ephemeral=True)

	@app_commands.command(name="logs")
	async def logs(self, ctx:discord.Interaction, type:int):
		if not await self.bot.is_owner(ctx.user):
			return
		await ctx.response.defer(ephemeral=True)
		if type in [0,1,2]:
			if type == 0:
				log = "timer.json"
			elif type == 1:
				log = "socketlog.json"
			elif type == 2:
				log = "statistic.json"
			with open(os.path.join(config.dir, 'data', 'logs', log), 'r') as f:
				data = json.load(f)
			with open(os.path.join(config.dir, 'data', log), 'w') as f:
				json.dump(data, f, indent=2)
			with open(os.path.join(config.dir, 'data', log), 'rb') as f:
				file = discord.File(f)
				f.close()
			await ctx.followup.send("Here you are ->",file=file, ephemeral=True)
			os.remove(f'{config.dir}/data/{log}')
		elif type == 3:
			log = "syslog.txt"
			with open(os.path.join(config.dir, 'data', 'logs', log), 'rb') as f:
				file = discord.File(f)
			await ctx.followup.send("Here you are ->",file=file, ephemeral=True)
		elif type == 4:
			log = "misskeylog.txt"
			with open(os.path.join(config.dir, 'data', 'logs', log), 'rb') as f:
				file = discord.File(f)
			await ctx.followup.send("Here you are ->",file=file, ephemeral=True)

	@app_commands.command(name="verify")
	@app_commands.checks.dynamic_cooldown(checkcd,key=lambda interaction: interaction.user.id)
	async def verify(self, ctx:discord.Interaction, code:str=None):
		await ctx.response.defer(ephemeral=True)
		with open(os.path.join(config.dir, 'data', 'verify_code.json'), 'r') as f:
			codes:dict = json.load(f)
		time = datetime.datetime.now().timestamp()
		user_id = str(ctx.user.id)
		if code is None:
			binid = bin(int(user_id))[2:]
			snowflake = str(binid).zfill(64)
			bintime = snowflake[:42]
			unixtime = (int(bintime, 2) + 1420070400000)/1000
			if time - unixtime < 2592000:
				await ctx.followup.send('你的帳號建立日期未達30日 無法進行驗證')
				return
			b = random.randbytes(1024)
			hb = hashlib.sha1(b).hexdigest()
			embed = discord.Embed(title=f"歡迎來到 {ctx.guild.name}", description=f'你的驗證碼如下:\n`{hb}`\n請在指定的頻道中使用 /verify code:<驗證碼> 即可完成驗證', color=0x02FEBF)
			embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)
			try:
				await ctx.user.send(content=None, embed=embed)
			except discord.errors.Forbidden:
				await ctx.followup.send('糟糕 在傳送驗證碼時發生錯誤 :skull_crossbones: 請確認已開啟"允許伺服器成員私訊"')
				return
			else:
				if user_id in codes:
					del codes[user_id]
				codes.setdefault(user_id, {"code": hb, "expire": time})
				with open(os.path.join(config.dir, 'data', 'verify_code.json'), 'w') as f:
					json.dump(codes, f, indent=2)
				await ctx.followup.send('已傳送驗證碼 請到私訊確認')
		else:
			if user_id not in codes:
				await ctx.followup.send('你尚未取得驗證碼 請使用 /verify 取得驗證碼')
				return
			d = codes[user_id]
			if time - d['expire'] > 600:
				await ctx.followup.send('你的驗證碼已過期 請使用 /verify 重新取得驗證碼')
				return
			elif code != d['code']:
				await ctx.followup.send('你輸入的驗證碼錯誤 請重新輸入')
				return
			elif code == d['code']:
				snow = discord.abc.Snowflake
				snow.id = config.member_role
				await ctx.user.add_roles(snow)
				del codes[user_id]
				with open(os.path.join(config.dir, 'data', 'verify_code.json'), 'w') as f:
					json.dump(codes, f, indent=2)
				await ctx.followup.send('已成功完成驗證 感謝你的配合')
				return

	@app_commands.command(name="status")
	async def status(self, ctx:discord.Interaction):
		await ctx.response.defer(ephemeral=True)
		now = int(datetime.datetime.now().timestamp() * 1000)
		embed = discord.Embed(title="Bot Status", color=0x02FEBF)
		# rss
		rss = self.bot.get_cog('Misskey')
		if rss is not None:
			rssup = True
			misskeyws = not rss.ws.closed
			with open(os.path.join(config.dir, 'data', 'misskey', 'restart_id.json'), 'r') as f:
				resid = json.load(f)
				lastmskytime = int(now - resid['time'])
		else:
			rssup = False
		at = self.bot.get_cog('ATproto')
		if at is not None:
			atup = True
			bluesky = not all(t.done() for t in at.tasks)
		else:
			atup = False
		if rssup:
			if misskeyws:
				embed.add_field(name="Misskey websocket", value="```\n✅ UP\n```\n")
			else:
				embed.add_field(name="Misskey websocket", value="```\n❌ DOWN\n```")
			lastmskytime = lastmskytime//1000
			if lastmskytime <= 1200:
				mskytime = f"```json\n{lastmskytime}s ✅ OK\n```"
			else:
				mskytime = f"```json\n{lastmskytime}s ❌ BAD\n```"
			embed.add_field(name="Misskey api", value=mskytime)
		else:
			embed.add_field(name="Misskey", value="```\n❌ DOWN\n```")
		if atup:
			if bluesky:
				embed.add_field(name="Bluesky", value="```\n✅ UP\n```")
			else:
				embed.add_field(name="Bluesky", value="```\n❌ DOWN\n```")
		else:
			embed.add_field(name="Bluesky", value="```\n❌ DOWN\n```")
		# web
		async with aiohttp.ClientSession() as s:
			nowtime = datetime.datetime.now().timestamp()
			headers = {
				"Authorization": config.secret,
				"User-Agent": config.bot_agent
			}
			try:
				async with s.get(f"https://{config.domain}/status", headers=headers, timeout=5) as r:
					r.raise_for_status()
					if r.status != 204 or not r.headers.get("X-Time") or not r.headers.get("X-Signature") or float(r.headers["X-Time"]) < nowtime:
						raise Exception(f"Data Error!\n{dict(r.headers)}")
					signature = hmac.new(bytes(config.token,"utf-8"), bytes(r.headers["X-Time"],"utf-8"), hashlib.sha256).hexdigest()
					if not hmac.compare_digest(r.headers["X-Signature"], signature):
						raise Exception(f"Worng signature!\n{dict(r.headers)}")
					embed.add_field(name="Web application", value="```\n✅ UP\n```")
			except (aiohttp.ClientResponseError, asyncio.TimeoutError):
				embed.add_field(name="Web application", value="```\n❌ DOWN\n```")
			except:
				embed.add_field(name="Web application", value="```\n❌ BAD\n```")
				logging.exception(f"Status command Error!")
		await ctx.followup.send(embed=embed)

	@app_commands.command(name="raid")
	async def raid(self, ctx:discord.Interaction, mode:bool):
		await ctx.response.defer(ephemeral=True)
		with open(os.path.join(config.dir, "data", "atproto", "raid.json"), "r+") as f:
			d = json.load(f)
			d['raidlock'] = mode
			f.seek(0)
			json.dump(d, f, indent=2)
			f.truncate()
		await ctx.followup.send(f"successfully", ephemeral=True)

	class setembed_text(discord.ui.Modal):
		title = "設定嵌入訊息內容"
		e_title = discord.ui.TextInput(
			label = "標題",
			placeholder = "輸入標題...",
			required = False,
			max_length = 256,
			row = 0
		)
		description = discord.ui.TextInput(
			label = "內容",
			placeholder = "輸入內容...",
			required = False,
			max_length = 4000,
			style = discord.TextStyle.long,
			row = 1
		)
		footer = discord.ui.TextInput(
			label = "頁尾",
			placeholder = "輸入頁尾...",
			required = False,
			max_length = 4000,
			row = 2
		)
		color = discord.ui.TextInput(
			label = "顏色",
			placeholder = "設定顏色 格式為16進位RGB色碼",
			required = True,
			min_length = 6,
			max_length = 6,
			row = 3
		)
		def __init__(self, user_id:str):
			self.e_title.default = None
			self.footer.default = None
			self.description.default = None
			self.color.default = "02FEBF"
			self.user_id = str(user_id)
			with open(os.path.join(config.dir, 'data', 'embed.json'), 'r') as f:
				self.data = json.load(f)
				self.channel = self.data[self.user_id]['channel']
			if self.data[self.user_id].get("embed_text"):
				self.e_title.default = self.data[self.user_id]['embed_text']['title']
				self.footer.default = self.data[self.user_id]['embed_text']['footer']
				self.description.default = self.data[self.user_id]['embed_text']['description']
				self.color.default = self.data[self.user_id]['embed_text']['color']
			super().__init__()

		async def on_submit(self, interaction: discord.Interaction):
			await interaction.response.defer(ephemeral=True)
			embed_text = {
				"title": self.e_title.value,
				"description": self.description.value,
				"footer": self.footer.value,
				"color": self.color.value
			}
			with open(os.path.join(config.dir, 'data', 'embed.json'), 'r') as f:
				self.data = json.load(f)
			if self.e_title.value == "" and self.description.value == "" and self.footer.value == "" and self.color.value.lower() == "02febf" and not self.data[str(interaction.user.id)].get('embed_text'):
				await interaction.edit_original_response(content=f'你似乎沒有加入任何內容 無法提交 :jack_o_lantern:',embed=None)
				return
			elif self.e_title.value == "" and self.description.value == "" and self.footer.value == "" and self.color.value.lower() == "02febf" and self.data[str(interaction.user.id)].get('embed_text'):
				del self.data[str(interaction.user.id)]['embed_text']
				self.data[str(interaction.user.id)]['last'] = datetime.datetime.now(tz).timestamp()
			else:
				self.data[str(interaction.user.id)].setdefault('embed_text', {})
				self.data[str(interaction.user.id)]['embed_text'] = embed_text
				self.data[str(interaction.user.id)]['last'] = datetime.datetime.now(tz).timestamp()
			with open(os.path.join(config.dir, 'data', 'embed.json'), 'w') as f:
				json.dump(self.data, f, indent=2)
			await interaction.edit_original_response(content=f'文字內容設定完成!你可以繼續設定其他內容或按下"傳送"來發送訊息到 <#{self.channel}>',embed=None)

	class setembed_image(discord.ui.Modal):
		title = "設定嵌入訊息圖片"
		thumbnail = discord.ui.TextInput(
			label = "封面圖片",
			placeholder = "輸入圖片網址...",
			required = False,
			row = 0
		)
		image = discord.ui.TextInput(
			label = "嵌入圖片",
			placeholder = "輸入圖片網址...",
			required = False,
			row = 1
		)

		def __init__(self, user_id):
			super().__init__()
			self.thumbnail.default = None
			self.image.default = None
			self.user_id = str(user_id)
			with open(os.path.join(config.dir, 'data', 'embed.json'), 'r') as f:
				self.data = json.load(f)
				self.channel = self.data[self.user_id]['channel']
			if self.data[self.user_id].get("embed_image"):
				self.thumbnail.default = self.data[self.user_id]['embed_image']['thumbnail']
				self.image.default = self.data[self.user_id]['embed_image']['image']


		async def on_submit(self, interaction: discord.Interaction):
			await interaction.response.defer(ephemeral=True)
			embed_image = {
				"thumbnail": self.thumbnail.value,
				"image": self.image.value,
			}
			with open(os.path.join(config.dir, 'data', 'embed.json'), 'r') as f:
				self.data = json.load(f)
			checkt = urlparse(str(self.thumbnail.value))
			checki = urlparse(str(self.image.value))
			if self.thumbnail.value == "" and self.image.value == "" and not self.data[str(interaction.user.id)].get('embed_image'):
				await interaction.edit_original_response(content=f'你似乎沒有加入任何圖片 無法提交 :jack_o_lantern:',embed=None)
				return
			elif self.thumbnail.value == "" and self.image.value == "" and self.data[str(interaction.user.id)].get('embed_image'):
				del self.data[str(interaction.user.id)]['embed_image']
				self.data[str(interaction.user.id)]['last'] = datetime.datetime.now(tz).timestamp()
			elif not (checkt.scheme and checkt.netloc) and self.thumbnail.value or not (checki.scheme and checki.netloc) and self.image.value:
				await interaction.edit_original_response(content=f'你似乎輸入了一個無效的網址 無法提交 :dolphin:',embed=None)
				return
			else:
				self.data[str(interaction.user.id)].setdefault('embed_image', {})
				self.data[str(interaction.user.id)]['embed_image'] = embed_image
				self.data[str(interaction.user.id)]['last'] = datetime.datetime.now(tz).timestamp()
			with open(os.path.join(config.dir, 'data', 'embed.json'), 'w') as f:
				json.dump(self.data, f, indent=2)
			await interaction.edit_original_response(content=f'圖片設定完成!你可以繼續設定其他內容或按下"傳送"來發送訊息到 <#{self.channel}>',embed=None)

	class setembed_field(discord.ui.Modal):
		title = "新增嵌入訊息欄位"
		ti = discord.ui.TextInput(
			label = "標題",
			placeholder = "輸入標題...",
			required = False,
			row = 0,
			max_length = 256
		)
		des = discord.ui.TextInput(
			label = "內容",
			placeholder = "輸入內容...",
			required = False,
			style = discord.TextStyle.paragraph,
			row = 1,
			max_length = 1024
		)
		mode = discord.ui.TextInput(
			label = "顯示方式",
			placeholder = "0: 逐行, 1: 並排",
			required = True,
			row = 2,
			min_length = 1,
			max_length = 1
		)

		def __init__(self, user_id):
			super().__init__()
			self.ti.default = None
			self.des.default = None
			self.mode.default = "0"
			self.user_id = str(user_id)
			with open(os.path.join(config.dir, 'data', 'embed.json'), 'r') as f:
				self.data = json.load(f)
				self.channel = self.data[self.user_id]['channel']

		async def on_submit(self, interaction: discord.Interaction):
			await interaction.response.defer(ephemeral=True)
			if self.ti.value == "" and self.des.value == "":
				await interaction.edit_original_response(content=f'你不能增加一個空欄位 :jack_o_lantern: 請幫欄位增加一點東西',embed=None)
				return
			embed_field = {
				"title": self.ti.value,
				"description": self.des.value,
				"mode": self.mode.value
			}
			with open(os.path.join(config.dir, 'data', 'embed.json'), 'r') as f:
				self.data = json.load(f)
			self.data[str(interaction.user.id)].setdefault('embed_field', [])
			self.data[str(interaction.user.id)]['embed_field'].append(embed_field)
			self.data[str(interaction.user.id)]['last'] = datetime.datetime.now(tz).timestamp()
			with open(os.path.join(config.dir, 'data', 'embed.json'), 'w') as f:
				json.dump(self.data, f, indent=2)
			await interaction.edit_original_response(content=f'已加入欄位!目前欄位總數:{len(self.data[str(interaction.user.id)]["embed_field"])} 你可以繼續設定其他內容或按下"傳送"來發送訊息到 <#{self.channel}>',embed=None)

	@app_commands.command(name="embed_message")
	@app_commands.checks.dynamic_cooldown(checkcd,key=lambda interaction: interaction.user.id)
	async def embed_message(self, *args:discord.Interaction):
		await args[0].response.defer(ephemeral=True)
		user_id = str(args[0].user.id)
		now = datetime.datetime.now(tz).timestamp()
		for option in args[0].data['options']:
			if option['name'] == 'channel':
				channel_id = int(option['value'])
		if channel_id in config.notification_channel or channel_id == config.rules and not (args[0].user._permissions & 1 << 3):
			await args[0].followup.send(f"不行!! 你不能傳送訊息到 <#{channel_id}>", ephemeral=True)
			return
		with open(os.path.join(config.dir, 'data', 'embed.json'), 'r') as f:
			data = json.load(f)
		data.setdefault(user_id, {})
		if data[user_id].get("last"):
			diff = now - data[user_id]['last']
			if diff > 600:
				data[user_id] = {}
		data[user_id].setdefault("channel", channel_id)
		data[user_id].setdefault("last", datetime.datetime.now(tz).timestamp())
		data[user_id]['channel'] = channel_id
		data[user_id]['last'] = datetime.datetime.now(tz).timestamp()
		with open(os.path.join(config.dir, 'data', 'embed.json'), 'w') as f:
			json.dump(data, f, indent=2)

		async def setting(interaction: discord.Interaction, mode: int):
			match mode:
				case 1:
					await interaction.response.send_modal(self.setembed_text(str(interaction.user.id)))
				case 2:
					await interaction.response.send_modal(self.setembed_image(str(interaction.user.id)))
				case 3:
					with open(os.path.join(config.dir, 'data', 'embed.json'), 'r') as f:
						data = json.load(f)
					if data[str(interaction.user.id)].get('embed_field') and len(data[str(interaction.user.id)]['embed_field']) == 25:
						await interaction.response.edit_message(content=f'糟糕 已經達到上限了 :robot: 你最多只能增加25個欄位',embed=None)
					else:
						await interaction.response.send_modal(self.setembed_field(str(interaction.user.id)))
				case 4:
					with open(os.path.join(config.dir, 'data', 'embed.json'), 'r') as f:
						data = json.load(f)
					if data[str(interaction.user.id)].get('embed_field') and len(data[str(interaction.user.id)]['embed_field']) > 0:
						del data[str(interaction.user.id)]['embed_field']
						with open(os.path.join(config.dir, 'data', 'embed.json'), 'w') as f:
							json.dump(data, f, indent=2)
						await interaction.response.edit_message(content=f'已成功刪除所有欄位 :hamster:',embed=None)
					else:
						await interaction.response.edit_message(content=f'糟糕 好像沒有東西可以刪除 :cat:',embed=None)

		async def embed_send(interaction: discord.Interaction, mode: int):
			await interaction.response.defer(ephemeral=True)
			with open(os.path.join(config.dir, 'data', 'embed.json'), 'r') as f:
				data = json.load(f)
				user_id = str(interaction.user.id)

			if not data[user_id].get('embed_text') and not data[user_id].get('embed_image') and not data[user_id].get('embed_field') or data[user_id].get('embed_text') and not data[user_id].get('embed_image') and not data[user_id].get('embed_field') and data[user_id]['embed_text']['title'] == "" and data[user_id]['embed_text']['description'] == "" and data[user_id]['embed_text']['footer'] == "":
				if mode == 1:
					await interaction.edit_original_response(content=f'看起來你似乎沒有設定任何訊息內容 :neutral_face: 因此取消傳送', view=None,embed=None)
					data[user_id] = {}
					with open(os.path.join(config.dir, 'data', 'embed.json'), 'w') as f:
						json.dump(data, f, indent=2)
					return
				elif mode == 2:
					await interaction.edit_original_response(content=f'看起來你似乎沒有設定任何訊息內容 :neutral_face: 無法顯示預覽',embed=None)
					return

			if data[user_id].get('embed_text'):
				try:
					color = int(data[user_id]['embed_text']['color'], 16)
				except ValueError:
					color = int("196287")
				embed = discord.Embed(title=data[user_id]['embed_text']['title'], description=data[user_id]['embed_text']['description'], color=discord.Colour(color))
				embed.set_footer(text=data[user_id]['embed_text']['footer'])
			else:
				embed = discord.Embed(color=discord.Colour(196287))
			if data[user_id].get('embed_image'):
					embed.set_thumbnail(url=data[user_id]['embed_image']['thumbnail'])
					embed.set_image(url=data[user_id]['embed_image']['image'])
			if data[user_id].get('embed_field') and len(data[user_id]['embed_field']) > 0:
				for field in data[user_id]['embed_field']:
					if field['mode'] == "1":
						inline = True
					else:
						inline = False
					embed.add_field(name=field['title'], value=field['description'],inline=inline)
			avatar_url = interaction.user.display_avatar.url
			embed.set_author(name=interaction.user.display_name, icon_url=avatar_url)
			embed.timestamp = datetime.datetime.now(tz)

			if len(embed) <= 6000:
				if mode == 1:
					message_send = {
						'embeds': [embed.to_dict()]
					}
					try:
						await sends.by_bot(channel_id, message_send)
						data[user_id] = {}
						with open(os.path.join(config.dir, 'data', 'embed.json'), 'w') as f:
							json.dump(data, f, indent=2)
						await interaction.edit_original_response(content='傳送嵌入訊息完成!', view=None,embed=None)
					except Exception as e:
						await interaction.edit_original_response(content=f'哎呀 好像發生問題了 :skull: reason: {e}', view=None,embed=None)
				elif mode == 2:
					await interaction.edit_original_response(content=f'這是你的訊息預覽 按下"傳送"來將這則訊息發送到 <#{channel_id}>', embed=embed)
			else:
				if mode == 1:
					await interaction.edit_original_response(content='糟糕 好像超出字數上限了 :skull_crossbones: 請把總字數縮減到6000字以下',embed=None)
				elif mode == 2:
					await interaction.edit_original_response(content='糟糕 好像超出字數上限了 :skull_crossbones: 無法顯示預覽',embed=None)

		view = discord.ui.View(timeout=600)
		button_text = Button(style=discord.ButtonStyle.success, label="設定文字")
		button_image = Button(style=discord.ButtonStyle.primary, label="設定圖片")
		button_field = Button(style=discord.ButtonStyle.secondary, label="加入欄位")
		button_deletefield = Button(style=discord.ButtonStyle.danger, label="刪除所有欄位")
		button_preview = Button(style=discord.ButtonStyle.primary, label="預覽訊息")
		button_Send = Button(style=discord.ButtonStyle.success, label="傳送" ,emoji="📩")
		button_text.callback = lambda self: asyncio.create_task(setting(self, 1))
		button_image.callback = lambda self: asyncio.create_task(setting(self, 2))
		button_field.callback = lambda self: asyncio.create_task(setting(self, 3))
		button_deletefield.callback = lambda self: asyncio.create_task(setting(self, 4))
		button_preview.callback = lambda self: asyncio.create_task(embed_send(self, 2))
		button_Send.callback = lambda self: asyncio.create_task(embed_send(self, 1))
		view.add_item(button_text)
		view.add_item(button_image)
		view.add_item(button_field)
		view.add_item(button_deletefield)
		view.add_item(button_preview)
		view.add_item(button_Send)
		await args[0].followup.send(f"請設定訊息內容, 訊息將傳送至 <#{channel_id}>",view=view, ephemeral=True)

async def setup(bot):
	await bot.add_cog(Command(bot))