import discord
from discord.ext import commands
import asyncio
import config
import json
from datetime import datetime
import timecount
import sends
import os
import pytz
import logging
from bs4 import BeautifulSoup
import random
import sqlite3
import aiohttp

tz = pytz.timezone('Asia/Taipei')
rsscog = None

class RSS(commands.Cog):
	def __init__(self, bot):
		self.bot:commands.Bot = bot
		self.fanbotiadb = sqlite3.connect(os.path.join(config.dir, 'database', 'fanbotia.db'), isolation_level=None)
		self.iddb = sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db'), isolation_level=None)
		self.fanbotiadb.execute('PRAGMA auto_vacuum = FULL')
		self.fanbotiadb.execute('VACUUM')
		self.tasks = [
			asyncio.create_task(RSS.auto_fanbotia(self)),
		]

	@timecount.timer
	async def fantia(self):

		async def fantia_send(name:str, avatar:str, fanclublink:str, postlink:str, title:str, text:str, image:str, time:str, field:list[dict]):
			embed = discord.Embed(title=title, description=text, url=postlink,color=0xEA4C89)
			embed.set_author(name=name, url=fanclublink, icon_url=avatar)
			embed.set_footer(text='發佈時間')
			embed.timestamp = datetime.strptime(time,"%a, %d %b %Y %H:%M:%S %z")
			for f in field:
				embed.add_field(**f)
			if image:
				embed.set_image(url=image)
			message_send = {
				'embeds': [embed.to_dict()],
				'username': name,
				'avatar_url': avatar,
				"allowed_mentions": {
						"parse": []
				}
			}
			try:
				await sends.by_webhook(config.fantia_webhook, message_send)
				return True
			except:
				return False

		clubid = self.iddb.execute('SELECT id FROM fantia').fetchall()
		count = 0
		async with aiohttp.ClientSession() as s:
			headers = {
				"Accept-Language": "ja-JP,ja;q=0.9",
				"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
			}
			for fanclubid in clubid:
				fanclubid = fanclubid[0]
				try:
					async with s.get(f"https://fantia.jp/api/v1/fanclubs/{fanclubid}", headers=headers)as r:
						r.raise_for_status()
						payload = await r.json()
						data = payload["fanclub"]
						name = data["fanclub_name_with_creator_name"]
						avatar = data["icon"]["original"]
						fanclublink = f"https://fantia.jp{data["uri"]["posts"]}"
				except:
					logging.exception("Check fantia posts Failed")
				
				new = False
				if self.fanbotiadb.execute('SELECT * FROM fantia_restart WHERE id = ?', (fanclubid,)).fetchone() is None:
					new = True

				for p in data["recent_posts"]:
					post_id = p["id"]
					postlink = f"https://fantia.jp{p["uri"]["show"]}"
					post_title = p["title"]
					post_text = p["comment"]
					post_image = p["thumb_micro"]
					post_time = p["posted_at"]
					if post_image:
						post_image = post_image.replace("/micro_", "/")
					try:
						if not new and self.fanbotiadb.execute('SELECT * FROM fantia_post WHERE id = ?', (post_id,)).fetchone() is None:
							async with s.get(f"https://fantia.jp/posts/{post_id}", headers=headers) as r:
								r.raise_for_status()
								html = await r.read()
								soup = BeautifulSoup(html, 'html.parser')
								meta = soup.find('meta', attrs={'name': 'csrf-token'})
								csrf = meta['content']
							
							aheaders = {
  							"Accept-Language": "ja-JP,ja;q=0.9",
  							"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
          			"x-csrf-token": csrf,
          			"x-requested-with": "XMLHttpRequest"
							}
							async with s.get(f"https://fantia.jp/api/v1/posts/{post_id}", headers=aheaders) as r:
								r.raise_for_status()
								post_data = await r.json()
							
							post_field = []
							for content in post_data["post"]["post_contents"]:
								if content.get("plan"):
									content_plan = f"{content["plan"]["name"]}({content["plan"]["price"]}{content["currency_unit"]})以上限定"
								else:
									content_plan = "一般公開"
								content_title = content.get("title") or "タイトルなし"
								post_field.append({
									"name": "",
									"value": f"`{content_plan}`\n**{content_title}**",
									"inline": False
								})

							a = await fantia_send(name, avatar, fanclublink, postlink, post_title, post_text, post_image, post_time, post_field)
							if not a:
								continue
							self.fanbotiadb.execute('INSERT INTO fantia_post VALUES (?)', (post_id,))
						elif new:
							try:
								self.fanbotiadb.execute('INSERT INTO fantia_post VALUES (?)', (post_id,))
							except sqlite3.IntegrityError:
								pass
					except:
							logging.exception(f"Pending fantia post failed!! | {fanclubid}")
							return

				count += 1
				if count < len(clubid):
					rd = random.uniform(4,7)
					await asyncio.sleep(rd)

		self.fanbotiadb.execute('DELETE FROM fantia_restart')
		for fanclubid in clubid:
			self.fanbotiadb.execute('INSERT INTO fantia_restart VALUES (?)', (fanclubid[0],))
		self.fanbotiadb.commit()

	async def fanbox(self):
		ids = self.iddb.execute('SELECT id FROM fanbox').fetchall()
		with open(os.path.join(config.dir, "data", "fanbox.json"), "r") as f:
			c = json.load(f)
		async with aiohttp.ClientSession() as s:
			for id in ids:
				id = id[0]
				headers = {
					"Accept": "application/json, text/plain, */*",
					"Accept-Language": "ja-JP,ja;q=0.5",
					"Sec-Fetch-Dest": "empty",
					"Sec-Fetch-Mode": "cors",
					"Sec-Fetch-Site": "same-site",
					"TE": "trailers",
					"Referer": "https://www.fanbox.cc/",
					"Origin": "https://www.fanbox.cc",
					"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
					"Cookies": c["Cookies"]
				}
				try:
					async with s.get(f"https://api.fanbox.cc/post.listCreator?creatorId={id}&limit=10", headers=headers) as r:
						r.raise_for_status()
						payload = await r.json()
				except:
					logging.exception(f"Check Fanbox posts Failed!! | {id}")
					return

				for post in payload["body"]:
					post_id = post["id"]
					if self.fanbotiadb.execute('SELECT * FROM fanbox_post WHERE id = ?', (post_id,)).fetchone() is None:
						title = post["title"]
						price = post["feeRequired"]
						description = post["excerpt"]
						name = post["user"]["name"]
						icon = post["user"]["iconUrl"]
						post_time = post["publishedDatetime"]
						image = post["cover"]["url"] if post.get("cover") else None

						embed = discord.Embed(title=title, description=description, url=f"https://www.fanbox.cc/@{id}/posts/{post_id}", color=0xFAF18A)
						embed.set_author(name=name, icon_url=icon, url=f"https://www.fanbox.cc/@{id}")
						embed.set_footer(text=f"￥{price} | 發布時間")
						embed.timestamp = datetime.strptime(post_time, "%Y-%m-%dT%H:%M:%S%z")
						if image is not None:
							embed.set_image(url=image)
						message_send = {
							'embeds': [embed.to_dict()],
							'username': name,
							'avatar_url': icon,
							"allowed_mentions": {
								"parse": []
							}
						}
						if self.fanbotiadb.execute('SELECT * FROM fanbox_restart WHERE id = ?', (id,)).fetchone() is not None:
							try:
								await sends.by_webhook(config.fantia_webhook, message_send)
								self.fanbotiadb.execute('INSERT INTO fanbox_post VALUES (?)', (post_id,))
							except:
								continue
						else:
							try:
								self.fanbotiadb.execute('INSERT INTO fanbox_post VALUES (?)', (post_id,))
							except sqlite3.IntegrityError:
								pass
				await asyncio.sleep(random.uniform(180, 360))

		self.fanbotiadb.execute('DELETE FROM fanbox_restart')
		for id in ids:
			self.fanbotiadb.execute('INSERT INTO fanbox_restart VALUES (?)', (id[0],))
		self.fanbotiadb.commit()

	async def auto_fanbotia(self):
		await self.bot.wait_until_ready()
		while True:
			self.sec_tasks = [asyncio.create_task(self.fantia())]
			await asyncio.wait(self.sec_tasks)
			# await asyncio.sleep(10)
			# self.sec_tasks = [asyncio.create_task(self.fanbox())]
			# await asyncio.wait(self.sec_tasks)		
			rd = random.uniform(43200, 64800)
			await asyncio.sleep(rd)

async def setup(bot):
	global rsscog
	rsscog = RSS(bot)
	await bot.add_cog(rsscog)

async def teardown(bot):
	global rsscog
	if rsscog is not None:
		for task in rsscog.tasks:
			task.cancel()
		for task in rsscog.sec_tasks:
			task.cancel()
		rsscog.fanbotiadb.close()
		rsscog.iddb.close()
		rsscog = None