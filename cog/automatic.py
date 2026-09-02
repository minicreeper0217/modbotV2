import discord
from discord.ext import commands
import config
import asyncio
import pytz
import datetime
import timecount
import os
import json
import aiohttp
import hashlib
import hmac
import sqlite3
import logging
import schedule
import certificate
import sends
import secrets
import random

tz = pytz.timezone('Asia/Taipei')
autocog = None

class Automatic(commands.Cog):
	def __init__(self, bot):
		self.bot:commands.Bot = bot
		asyncio.create_task(Automatic.start(self))

	async def start(self):
		await self.bot.wait_until_ready()
		schedule.every(1).day.at("02:00", tz=tz).do(lambda: asyncio.create_task(self.check_log())).tag("automatic")
		schedule.every(1).day.at("06:05", tz=tz).do(lambda: asyncio.create_task(self.update_certificates())).tag("automatic")
		schedule.every(1).day.at("06:00", tz=tz).do(lambda: asyncio.create_task(self.check_ytsubscribe())).tag("automatic")
		schedule.every(1).day.at("08:00", tz=tz).do(lambda: asyncio.create_task(self.cat())).tag("automatic")
		schedule.every(1).day.at("14:00", tz=tz).do(lambda: asyncio.create_task(self.check_ytsubscribe())).tag("automatic")
		schedule.every(1).day.at("22:00", tz=tz).do(lambda: asyncio.create_task(self.check_ytsubscribe())).tag("automatic")
		schedule.every(120).seconds.do(lambda: asyncio.create_task(self.auto_clean_messages())).tag("automatic")
		self.tasks = [
			asyncio.create_task(self.check_schedule()),
		]

	async def check_schedule(self):
		while True:
			schedule.run_pending()
			await asyncio.sleep(1)

	async def command_channel_message(self):
		for channel_id in config.command_channel.keys():
			channel = self.bot.get_channel(channel_id)
			async for message in channel.history():
				now = datetime.datetime.now().timestamp()
				if (now - message.created_at.timestamp()) > 1209000:
					return
				if not message.author.bot and message.channel.id in config.command_channel and channel.id in config.command_channel and config.command_channel[channel.id] != message.type:
					try:
						await message.delete()
						await asyncio.sleep(2)
						continue
					except (discord.errors.HTTPException, discord.errors.DiscordServerError):
						await asyncio.sleep(3)
						continue
					except discord.errors.RateLimited as e:
						await asyncio.sleep(e.retry_after)
						continue
					except:
						continue

	@timecount.timer
	async def auto_clean_messages(self):
		tasks = [
			asyncio.create_task(Automatic.command_channel_message(self))
		]
		await asyncio.wait(tasks)
		return

	async def check_log(self):
		today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
		log_file = os.path.join(config.dir, 'data','logs', 'syslog.txt.2')
		applog_file = os.path.join(config.dir, 'data','logs', 'webapplog.txt.2')
		nginx = '/var/log/nginx/access.log.1'
		if os.path.isfile(nginx):
			with open(os.path.join(config.dir, 'data',"nginx_log.json"),"r") as f:
				data:dict = json.load(f)
			if data['date'] != today:
				hash_sha256 = hashlib.sha256()
				with open(nginx,"rb") as f:
					for chunk in iter(lambda: f.read(4096), b''):
						hash_sha256.update(chunk)
				if hash_sha256.hexdigest() != data['hash']:
					channel = self.bot.get_channel(config.bot_event)
					file = discord.File(nginx,filename=f"nginx-access-log-{data['date']}.txt")
					# await channel.send(content="Here is a nginx access log =>",file=file)
					data['date'] = today
					data['hash'] = hash_sha256.hexdigest()
					with open(os.path.join(config.dir, 'data',"nginx_log.json"),"w") as f:
						json.dump(data, f, indent=2)
		if os.path.isfile(log_file):
			channel = self.bot.get_channel(config.bot_event)
			file = discord.File(log_file,filename=f"syslog ({today}).txt")
			await channel.send(content="Here is a backup log =>",file=file)
			os.remove(log_file)
		if os.path.isfile(applog_file):
			channel = self.bot.get_channel(config.bot_event)
			file = discord.File(applog_file,filename=f"applog ({today}).txt")
			await channel.send(content="Here is a backup log =>",file=file)
			os.remove(applog_file)

	async def check_ytsubscribe(self):
		now = int(datetime.datetime.now().timestamp())
		with sqlite3.connect(os.path.join(config.dir, 'database', 'youtube.db')) as db:
			cursor = db.execute('SELECT id FROM subscribe WHERE time < ?', (now + 100000,)).fetchall()
			if len(cursor) > 0:
				async with aiohttp.ClientSession() as session:
					for idtup in cursor:
						id = idtup[0]
						secret = secrets.token_hex(32)
						verify_token = hmac.new(bytes(config.secret,"utf-8"), bytes(secret,"utf-8"), hashlib.sha256).hexdigest()
						db.execute('UPDATE subscribe SET secret = ? WHERE id = ?', (secret, id))
						db.commit()
						url = f"https://pubsubhubbub.appspot.com/subscribe?hub.callback=https%3A%2F%2F{config.domain}%2Fwebhook%2Fyoutube%2F{id}&hub.topic=https%3A%2F%2Fwww.youtube.com%2Fxml%2Ffeeds%2Fvideos.xml%3Fchannel_id%3D{id}&hub.verify=async&hub.mode=subscribe&hub.verify_token={verify_token}&hub.secret={secret}&hub.lease_numbers=432000"
						try:
							async with session.post(url) as r:
								r.raise_for_status()
						except:
							logging.exception("Update youtube subscribe failed!")
							break
				db.commit()

	async def update_certificates(self):
		domain_list = [
			{
				"domain": config.domain,
				"zone_id": config.cloudflare_zone_id,
				"cert_path": config.nginx_certificate_path
			},
			{
				"domain": config.yuki_domain,
				"zone_id": config.cloudflare_yuki_zone_id,
				"cert_path": config.nginx_yuki_certificate_path
			}
		]
		await self.check_certificate(domain_list=domain_list)
		f = await self.check_ca()
		await self.check_origin_tls_auth(force=f, domain_list=domain_list)
		await self.check_ragdoll(force=f)

	async def check_certificate(self, domain_list:list):
		with sqlite3.connect(os.path.join(config.dir, 'database', 'certificate.db')) as db:
			for domain_data in domain_list:
				certificate_id, expire, fingerprint = db.execute('SELECT id, expire, fingerprint FROM now WHERE domain = ?',(domain_data["domain"],)).fetchone()
				time_now = datetime.datetime.now().timestamp()
				if expire - 2592000 < time_now:
					logging.info(f"Starting update {domain_data["domain"]} Origin CA Certificate...")
					async with aiohttp.ClientSession() as s:
						csr, private_key, public_key, new_fingerprint = certificate.generate_csr()
						try:
							raw_certificate = await certificate.sign_certificate(csr, s, domain_data["domain"])
							new_certificate_id = raw_certificate["result"]["id"]
							new_certificate = raw_certificate["result"]["certificate"]
							new_certificate_expire = int(datetime.datetime.strptime(raw_certificate["result"]["expires_on"].replace(" UTC", ""), "%Y-%m-%d %H:%M:%S %z").timestamp())
						except:
							logging.exception(f"Sign {domain_data["domain"]} certificate with Cloudflare Origin CA Failed!!")
							return

						try:
							certificate.update_nginx(new_certificate, private_key, domain_data["cert_path"])
						except:
							logging.exception("Update nginx certificate Failed!!")
							return

						db.execute('INSERT INTO old VALUES (?, ?, ?, ?)', (certificate_id, expire, fingerprint, domain_data["domain"]))
						db.execute('DELETE FROM now WHERE domain = ?', (domain_data["domain"]))
						db.execute('INSERT INTO now VALUES (?, ?, ?)', (new_certificate_id, new_certificate_expire, new_fingerprint, domain_data["domain"]))
						db.commit()

						try:
							await certificate.revoke_certificate(certificate_id, s)
						except:
							logging.exception(f"Revoke {domain_data["domain"]} certificate with Cloudflare Origin CA Failed!!")
							return

						logging.info(f"Successfully update {domain_data["domain"]} Origin CA Certificate\nID: {new_certificate_id}\nKey Fingerprint: {new_fingerprint}\nExpire: {datetime.datetime.fromtimestamp(new_certificate_expire, tz).strftime('%Y-%m-%d %H:%M:%S')}\nNext Update After: {datetime.datetime.fromtimestamp(new_certificate_expire - 2592000, tz).strftime('%Y-%m-%d %H:%M:%S')}")

	async def check_ca(self) -> bool:
		with sqlite3.connect(os.path.join(config.dir, 'database', 'certificate.db')) as db:
			old_certificate_id, old_expire, old_fingerprint = db.execute('SELECT * FROM ca_now').fetchone()

			if old_expire - 2592000 > datetime.datetime.now().timestamp():
				return False

			logging.info("Starting update Root CA Certificate...")

			new_certificate, new_certificate_id, private_key, new_fingerprint, new_expire = certificate.generate_ca()

			certificate.update_ca(new_certificate, private_key)

			db.execute('INSERT INTO ca_old VALUES (?, ?, ?)', (old_certificate_id, old_expire, old_fingerprint))
			db.execute('DELETE FROM ca_now')
			db.execute('INSERT INTO ca_now VALUES (?, ?, ?)', (str(new_certificate_id), new_expire, new_fingerprint))
			db.commit()

			logging.info(
				f"Successfully update Root CA Certificate\n"
				f"CA Certificate ID: {new_certificate_id}\n"
				f"Fingerprint: {new_fingerprint}\n"
				f"Expire: {datetime.datetime.fromtimestamp(new_expire, tz).strftime('%Y-%m-%d %H:%M:%S')}\n"
				f"Next Update After: {datetime.datetime.fromtimestamp(new_expire - 2592000, tz).strftime('%Y-%m-%d %H:%M:%S')}"
			)
			return True
		
	async def check_origin_tls_auth(self, domain_list:list, force: bool = False):
		with sqlite3.connect(os.path.join(config.dir, 'database', 'certificate.db')) as db:
			for domain_data in domain_list:
				new_cert = False

				cursor = db.execute('SELECT cf_id, id, expire, fingerprint FROM cf_now WHERE domain = ?', (domain_data["domain"],)).fetchone()
				if cursor is None:
					new_cert = True
					cloudflare_id, certificate_id, expire, fingerprint = ("", "", 0, "")
				else:
					cloudflare_id, certificate_id, expire, fingerprint = cursor
		
				if not new_cert and not force and expire - 2592000 > datetime.datetime.now().timestamp():
					return

				logging.info(f"Starting update {domain_data["domain"]} Cloudflare Origin TLS Auth Certificate...")

				certificate_pem, new_certificate_id, private_key, new_fingerprint, new_expire = certificate.generate_cloudflare_origin_tls_auth()

				async with aiohttp.ClientSession() as s:
					try:
						result = await certificate.update_origin_tls_auth(certificate_pem, private_key, s, domain_data["zone_id"])
						new_cloudflare_id = result["result"]["id"]
					except:
						logging.exception(f"Update {domain_data["domain"]} Cloudflare Origin TLS Auth Certificate Failed!!")
						return
					
					while True:
						result = await certificate.get_origin_tls_auth(new_cloudflare_id, s, domain_data["zone_id"])
						if result["result"]["status"] == "active":
							break
						await asyncio.sleep(1)

					if not new_cert:
						db.execute('INSERT INTO cf_old VALUES (?, ?, ?, ?, ?)',(cloudflare_id, certificate_id, expire, fingerprint, domain_data["domain"]))
						db.execute('DELETE FROM cf_now WHERE domain = ?', (domain_data["domain"],))
					db.execute('INSERT INTO cf_now VALUES (?, ?, ?, ?, ?)',(new_cloudflare_id, str(new_certificate_id), new_expire, new_fingerprint, domain_data["domain"]))
					db.commit()

					if not new_cert:
						try:
							await certificate.revoke_origin_tls_auth(cloudflare_id, s, domain_data["zone_id"])
						except:
							logging.exception(f"Revoke {domain_data["domain"]} Cloudflare Origin TLS Auth Certificate Failed!!")
							return

				logging.info(
					f"Successfully update {domain_data["domain"]} Cloudflare Origin TLS Auth Certificate\n"
					f"Certificate ID: {new_certificate_id}\n"
					f"Fingerprint: {new_fingerprint}\n"
					f"Expire: {datetime.datetime.fromtimestamp(new_expire, tz).strftime('%Y-%m-%d %H:%M:%S')}\n"
					f"Next Update After: {datetime.datetime.fromtimestamp(new_expire - 2592000, tz).strftime('%Y-%m-%d %H:%M:%S')}"
				)

	async def check_ragdoll(self, force: bool = False):
		with sqlite3.connect(os.path.join(config.dir, 'database', 'certificate.db')) as db:
			certificate_id, expire, fingerprint = db.execute('SELECT * FROM ragdoll_now').fetchone()

			if not force and expire - 2592000 > datetime.datetime.now().timestamp():
				return

			logging.info("Starting update Ragdoll Certificate...")

			certificate_pem, new_certificate_id, private_key, new_fingerprint, new_expire = certificate.generate_ragdoll()

			try:
				certificate.update_ragdoll(certificate_pem, private_key)
			except:
				logging.exception("Update Ragdoll Certificate Failed!!")
				return

			db.execute('INSERT INTO ragdoll_old VALUES (?, ?, ?)', (certificate_id, expire, fingerprint))
			db.execute('DELETE FROM ragdoll_now')
			db.execute('INSERT INTO ragdoll_now VALUES (?, ?, ?)', (str(new_certificate_id), new_expire, new_fingerprint))
			db.commit()

			logging.info(
				f"Successfully update Ragdoll Certificate\n"
				f"Certificate ID: {new_certificate_id}\n"
				f"Fingerprint: {new_fingerprint}\n"
				f"Expire: {datetime.datetime.fromtimestamp(new_expire, tz).strftime('%Y-%m-%d %H:%M:%S')}\n"
				f"Next Update After: {datetime.datetime.fromtimestamp(new_expire - 2592000, tz).strftime('%Y-%m-%d %H:%M:%S')}"
			)

	async def cat(self):
		cat_channel = 1326798646716661822
		headers = {
			"x-api-key": config.the_cat_api
		}
		try:
			async with aiohttp.ClientSession() as s:
				async with s.get(f"https://api.thecatapi.com/v1/breeds", headers=headers) as r:
					r.raise_for_status()
					breed_data = await r.json()
					breed_today = random.choice(breed_data)
					breed_id = breed_today["id"]
					breed = breed_today["name"]
					breed_des = breed_today["description"]
					temperament = breed_today["temperament"]

				async with s.get(f"https://api.thecatapi.com/v1/images/search?breed_ids={breed_id}", headers=headers) as r:
					r.raise_for_status()
					data = await r.json()
					image = data[0]["url"]
					today = int(datetime.datetime.now().timestamp())
					embed = discord.Embed(description=f"<t:{today}:d>\n\nToday's Cat is `{breed}`\n\n```{breed_des}```", color=0x02FEBF)
					embed.set_image(url=image)
					embed.set_footer(text=f"{breed} | {temperament}")
					embed.set_author(name="The Cat API", icon_url="attachment://logo.png")
					
					payload_json = {
						'attachments':[
							{
								"id": 0,
								"filename": "logo.png"
							}
						],
						"embeds": [embed.to_dict()]
					}
					formdata = aiohttp.FormData()
					formdata.add_field(name="payload_json", value=json.dumps(payload_json), content_type="application/json")
					with open(os.path.join(config.dir, 'data', 'the_cat_api.png'), "rb") as f:
						image_data = f.read()
						formdata.add_field(name="files[0]", value=image_data, filename="logo.png", content_type="image/png")
					await sends.by_bot(channel_id=cat_channel, message=formdata)
		except:
			logging.exception("Can't find a cat!")
			return

async def setup(bot):
	global autocog
	autocog = Automatic(bot)
	await bot.add_cog(autocog)

async def teardown(bot):
	global autocog
	if autocog is not None:
		tasks = autocog.tasks
		for task in tasks:
			task.cancel()
		schedule.clear("automatic")
		autocog = None