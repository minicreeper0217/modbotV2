import asyncio
import base64
import hashlib
import hmac
import io
import json
import logging
import math
import os
import sqlite3
import time
import uuid

from datetime import datetime
from io import BytesIO

import aiohttp
import discord
import pytz
import stepic
import tiktoken
import yarl

from discord import app_commands
from discord.ext import commands
from PIL import Image

import config

tz = pytz.timezone("Asia/Taipei")

class GPTres:
	def __init__(
		self,
		chatid,
		msgid,
		ctx: discord.Interaction,
		color,
		image_url=None,
		image_id=None,
		user_msg=None
	):
		self.chatid = chatid
		self.msgid = msgid
		self.ctx = ctx
		self.color = color
		self.image_url = image_url

		self.usage = None
		self.finish = False
		self.model = None
		self.completion_id = None
		self.process_time = 0
		self.assistant_modfoo = None

		self.assistant_msg = ""
		self.assistant_reasoning = ""

		self.embed_list = []
		self.discord_msg = None

		self.first_token = asyncio.Event()
		self._send_lock = asyncio.Lock()
		self._last_sent = ""

		if user_msg:
			self.user_msg = user_msg

			embed = discord.Embed(
				color=discord.Colour(0x86EFAC),
				description=self.user_msg
			)
			embed.set_author(
				name=ctx.user.display_name,
				icon_url=ctx.user.avatar.url
			)
			embed.set_footer(
				text=f"ChatID: {self.chatid} | MessageID: {self.msgid}"
			)

			if image_url:
				file_name = f"{uuid.uuid4().hex}.png"
				image_path = os.path.join(
					config.dir, "data", "chatgpt", "images", f"{image_id}.png"
				)

				self.file = discord.File(image_path, filename=file_name)
				embed.set_image(url=f"attachment://{file_name}.png")
			else:
				self.file = None

			self.embed_list.append(embed)

		asyncio.create_task(self.pending())

	async def pending(self):
		await self.first_token.wait()

		while not self.finish:
			if self.assistant_msg and self.assistant_msg != self._last_sent:
				try:
					await self.send()
				except discord.HTTPException as e:
					logging.warning("Discord edit failed: %s", e)

			await asyncio.sleep(1)

	async def done(self, process_time: float):
		self.process_time = process_time
		self.assistant_modfoo = await self.moderation(self.assistant_msg)
		self.finish = True

		if not self.first_token.is_set():
			self.first_token.set()

		await self.send()

	async def moderation(self, text: str) -> str:
		headers = {
			"Content-Type": "application/json",
			"Authorization": f"Bearer {config.chatgpt_moderations}"
		}
		data = {
			"model": "omni-moderation-latest",
			"input": text
		}

		async with aiohttp.ClientSession() as session:
			async with session.post(
				"https://api.openai.com/v1/moderations",
				json=data,
				headers=headers
			) as response:
				if response.status == 429:
					return ""

				response.raise_for_status()
				result = await response.json()

		modfoo = ""

		for category, flagged in result["results"][0]["categories"].items():
			if flagged:
				score = result["results"][0]["category_scores"][category]
				modfoo += f" | {category}: {round(score, 4)}"

		return modfoo

	async def send(self):
		async with self._send_lock:
			embed_list = self.embed_list.copy()

			embed = discord.Embed(
				color=discord.Colour(self.color),
				description=self.assistant_msg.replace("`", r"\`")
			)
			embed.set_author(name="ChatGPT")

			if self.finish:
				usage = self.usage
				embed.set_footer(
					text=(
						f"{self.model} | {self.completion_id} | "
						f"tokens: {usage['total_tokens']}"
						f"({usage['prompt_tokens']}/{usage['completion_tokens']}) | "
						f"{self.process_time}s{self.assistant_modfoo}"
					)
				)

			embed_list.append(embed)
			self._last_sent = self.assistant_msg

			if self.discord_msg is None:
				if self.image_url:
					self.discord_msg = await self.ctx.followup.send(
						embeds=embed_list,
						file=self.file,
						wait=True
					)
				else:
					self.discord_msg = await self.ctx.followup.send(
						embeds=embed_list,
						wait=True
					)
			else:
				if self.image_url:
					self.discord_msg = await self.discord_msg.edit(
						embeds=embed_list,
						attachments=self.file
					)
				else:
					self.discord_msg = await self.discord_msg.edit(
						embeds=embed_list
					)

class ChatGPT(commands.Cog):
	chatgptcom = app_commands.Group(
		name="chatgpt",
		description="chat with chatgpt"
	)

	def __init__(self, bot):
		self.bot: commands.Bot = bot

		self.route_openai = {
  		"token": config.chatgpt_moderations
		}

		self.route_girl = {
			"base_url": "scfw7zf2j7me4nvbra5zdy7i.agents.do-ai.run",
			"token": config.chatgpt_girl_token
		}
		self.route_boy = {
			"base_url": "oxlzogl6d4b5dlkgvl275azi.agents.do-ai.run",
			"token": config.chatgpt_boy_token
		}

		self.chat_types = {
			"b": {
				"color": 0x7DD3FC,
				"api": "completions",
				"route": self.route_boy
			},
			"g": {
				"color": 0xF9A8D4,
				"api": "completions",
				"route": self.route_girl
			},
			"c1": {
				"color": 0x86EFAC,
				"api": "responses",
				"instructions": config.chatgpt_c1_introduction
			},
			"c2": {
				"color": 0xC4B5FD,
				"api": "responses",
				"instructions": config.chatgpt_c2_introduction
			}
		}

		self.model = "gpt-6-luna"
		self.model_image = "gpt-image-2.5-flare"
		self.chatgptchannel = config.chatgpt_channel

		database_path = os.path.join(
			config.dir, "database", "chatgpt.db"
		)
		self.chatgptdb = sqlite3.connect(
			database_path,
			isolation_level=None
		)

	def num_tokens_from_messages(self, messages:list, model:str):
		"""Return the number of tokens used by a list of messages."""
		try:
			encoding = tiktoken.encoding_for_model(model)
		except KeyError:
			encoding = tiktoken.get_encoding("cl100k_base")
		num_tokens = 0
		for message in messages:
			num_tokens += 3
			for key, value in message.items():
				if isinstance(value, str):
					num_tokens += len(encoding.encode(value))
				elif isinstance(value, list):
					for content in value:
						if content["type"] == "text":
							num_tokens += len(encoding.encode(content["text"]))
						elif content["type"] == "image_url":
							num_tokens += self.num_tokens_from_image(content["image_url"]["url"].split("/")[-1].split("?")[0])
				if key == "name":
					num_tokens += 1
		num_tokens += 3
		return num_tokens

	async def chat(
		self,
		message: list,
		max_tokens: int,
		chatid: str | int,
		base_url: str,
		token: str,
		res_cls: GPTres
	) -> GPTres:
		chat_uuid = self.chatgptdb.execute(
			"SELECT uuid FROM list WHERE id = ?",
			(int(chatid),)
		).fetchone()[0]

		data = {
			"messages": message,
			"max_tokens": max_tokens,
			"user": chat_uuid,
			"stream": True,
			"stream_options": {
				"include_usage": True
			}
		}
		headers = {
			"Content-Type": "application/json",
			"Authorization": f"Bearer {token}"
		}

		timeout = aiohttp.ClientTimeout(total=610)
		url = f"https://{base_url}/api/v1/chat/completions"

		async with aiohttp.ClientSession(timeout=timeout) as session:
			async with session.post(
				url,
				json=data,
				headers=headers
			) as response:
				if response.content_type != "text/event-stream" and response.status != 200:
					error = await response.json()
					logging.warning("AI Chat Request failed! | Data: %s", error)
					response.raise_for_status()

				if response.content_type == "text/event-stream":
					start_time = time.perf_counter()

					while True:
						raw_line = await response.content.readline()
						line = raw_line.decode().strip()

						if not line.startswith("data: "):
							continue

						event_data = line[6:]

						if event_data == "[DONE]":
							process_time = round(time.perf_counter() - start_time, 2)

							try:
								await res_cls.done(process_time=process_time)
							except Exception as e:
								logging.error(
									"AI Chat Error at Done! | %s: %s",
									e.__class__.__name__,
									e
								)
								await res_cls.ctx.followup.send(
									content=f"AI Chat Error at Done! | {e.__class__.__name__}: {e}",
									ephemeral=True
								)

							return res_cls

						event = json.loads(event_data)

						if res_cls.completion_id is None:
							res_cls.completion_id = event["id"]
							res_cls.model = event["model"]

						if "usage" in event:
							res_cls.usage = event["usage"]
							continue

						if not event.get("choices"):
							continue

						delta = event["choices"][0]["delta"]

						if delta.get("reasoning_content"):
							res_cls.assistant_reasoning += delta["reasoning_content"]

						if delta.get("content"):
							res_cls.assistant_msg += delta["content"]

							if not res_cls.first_token.is_set():
								res_cls.first_token.set()

				else:
					event = await response.json()

					res_cls.completion_id = event["id"]
					res_cls.model = event["model"]
					res_cls.usage = event["usage"]

					assistant = event["choices"][0]["message"]
					res_cls.assistant_msg = assistant["content"]
					res_cls.assistant_reasoning = assistant["reasoning_content"]

					process_time = round(
						float(response.headers.get("x-process-time")),
						2
					)
					await res_cls.done(process_time=process_time)

					return res_cls

	async def chat_responses(
		self,
		message: list,
		max_tokens: int,
		chatid: str | int,
		instructions: str,
		model: str,
		res_cls: GPTres
	) -> GPTres:
		chat_uuid = self.chatgptdb.execute(
			"SELECT uuid FROM list WHERE id = ?",
			(int(chatid),)
		).fetchone()[0]

		data = {
			"model": model,
			"instructions": instructions,
			"input": message,
			"max_output_tokens": max_tokens,
			"stream": True,
			"store": False,
			"safety_identifier": chat_uuid
		}
		headers = {
			"Authorization": f"Bearer {config.chatgpt_user}",
			"Content-Type": "application/json"
		}

		timeout = aiohttp.ClientTimeout(total=610)
		start_time = time.perf_counter()

		async with aiohttp.ClientSession(timeout=timeout) as session:
			async with session.post(
				"https://api.openai.com/v1/responses",
				json=data,
				headers=headers
			) as response:
				response.raise_for_status()

				async for raw_line in response.content:
					line = raw_line.decode("utf-8").strip()

					if not line.startswith("data: "):
						continue

					event = json.loads(line[6:])

					match event.get("type"):
						case "response.created":
							result = event["response"]
							res_cls.completion_id = result["id"]
							res_cls.model = result["model"]

						case "response.output_text.delta":
							res_cls.assistant_msg += event["delta"]
							res_cls.first_token.set()

						case "response.reasoning_summary_text.delta":
							res_cls.assistant_reasoning += event["delta"]

						case "response.completed":
							result = event["response"]
							usage = result["usage"]

							res_cls.completion_id = result["id"]
							res_cls.model = result["model"]

							res_cls.usage = {
								"prompt_tokens": usage["input_tokens"],
								"completion_tokens": usage["output_tokens"],
								"total_tokens": usage["total_tokens"]
							}

							process_time = round(
								time.perf_counter() - start_time, 2
							)
							await res_cls.done(process_time)
							return res_cls

						case "response.failed":
							error = event["response"].get("error") or {}
							raise RuntimeError(
								f"Responses API failed: {error}"
							)

						case "response.incomplete":
							details = event["response"].get("incomplete_details")
							raise RuntimeError(
								f"Responses API incomplete: {details}"
							)

		raise RuntimeError("Responses API stream ended unexpectedly")

	def ischat(self, chatid:str | int) -> bool:
		cursor = self.chatgptdb.execute('SELECT * FROM list WHERE id = ?', (int(chatid),)).fetchone()
		if cursor is None:
			return False
		elif not os.path.isdir(os.path.join(config.dir, 'data', 'chatgpt', str(chatid))):
			return False
		else:
			return True

	def get_chat_config(self, chatid: str | int) -> dict:
		chat_type = self.chatgptdb.execute(
			"SELECT sex FROM list WHERE id = ?",
			(int(chatid),)
		).fetchone()[0]

		return self.chat_types.get(chat_type, self.chat_types["g"])

	def token_set(self, tokens_usage:dict, token_punish:int) -> int:
		tokens = self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("token_limit",)).fetchone()[0]
		total_tokens = tokens_usage["prompt_tokens"] + (tokens_usage["completion_tokens"] * 3)
		remain = tokens - (total_tokens * token_punish) if tokens - (total_tokens * token_punish) > 0 else 0
		self.chatgptdb.execute('UPDATE memo SET value = ? WHERE key = ?', (remain, "token_limit"))
		self.chatgptdb.commit()
		return remain

	def get_chat_data(
		self,
		chatid: str | int,
		message_id: int | None = None,
		api: str = "completions"
	) -> tuple[list, int]:
		msgid = self.chatgptdb.execute(
			"SELECT msgid FROM list WHERE id = ?",
			(int(chatid),)
		).fetchone()[0]

		chat_path = os.path.join(
			config.dir, "data", "chatgpt", str(chatid), "chat.json"
		)

		with open(chat_path, "r", encoding="UTF-8") as f:
			chat_log = json.load(f)

		chat_data = []

		for message in chat_log:
			if message["id"] == 0:
				system_content = (
					"Region: Japan\n"
					f"Time Now: {datetime.now(tz=pytz.timezone('Asia/Tokyo')).isoformat(timespec='seconds')}\n"
					"Note: The time will be updated each time it is sent.\n\n"
					f"{message['system']}"
				)

				chat_data.append({
					"role": "system" if api == "responses" else "user",
					"content": system_content
				})
				continue

			user_message = {
				"role": "user",
				"content": message["user"]
			}

			if message.get("image_url"):
				state = uuid.uuid4().hex
				timestamp = int(datetime.now().timestamp())

				signature = hmac.new(
					config.secret.encode("UTF-8"),
					f"{timestamp}-{state}".encode("UTF-8"),
					hashlib.sha256
				).hexdigest()

				image_url = (
					f"https://{config.domain}/api/chatgpt/images/{message['image_url']}"
					f"?time={timestamp}&state={state}&token={signature}"
				)

				if api == "responses":
					user_message["content"] = [
						{
							"type": "input_text",
							"text": message["user"]
						},
						{
							"type": "input_image",
							"image_url": image_url
						}
					]
				else:
					user_message["content"] = [
						{
							"type": "text",
							"text": message["user"]
						},
						{
							"type": "image_url",
							"image_url": {
								"url": image_url,
								"detail": "high"
							}
						}
					]

			chat_data.append(user_message)
			chat_data.append({
				"role": "assistant",
				"content": message["assistant"]
			})

			if message_id is not None and message_id == message["id"]:
				return chat_data, msgid

		return chat_data, msgid

	@chatgptcom.command(name="model")
	async def GPT_model(self, ctx:discord.Interaction, model:int):
		await ctx.response.defer(ephemeral=True)
		pass

	@chatgptcom.command(name="tokens")
	async def tokens(self, ctx:discord.Interaction):
		await ctx.response.defer(ephemeral=True)
		pass

	@chatgptcom.command(name="generate")
	async def generate(
		self,
		ctx: discord.Interaction,
		text: str,
		chatid: int = None,
		max_tokens: int = 8192,
		image_url: str = None
	):
		await ctx.response.defer(ephemeral=True)

		if ctx.channel.id != self.chatgptchannel:
			await ctx.followup.send(
				"You can't use this command at this channel!",
				ephemeral=True
			)
			return

		if max_tokens % 4 != 0:
			await ctx.followup.send(
				"Invalid max tokens number",
				ephemeral=True
			)
			return

		if chatid is None:
			chatid = self.chatgptdb.execute(
				"SELECT value FROM memo WHERE key = ?",
				("last",)
			).fetchone()[0]

		if not self.ischat(chatid):
			await ctx.followup.send("Chat not found!", ephemeral=True)
			return

		# 處理使用者提供的圖片
		if image_url:
			try:
				image_id = await self.image_pend(image_url)
			except Exception as e:
				await ctx.followup.send(str(e), ephemeral=True)
				return
		else:
			image_id = None

		await ctx.followup.send("Please wait...")

		# 取得對話紀錄
		chat_data, msgid = self.get_chat_data(
			chatid,
			api=chat_config["api"]
		)

		user_message = {
			"role": "user",
			"content": text
		}

		if image_url:
			state = uuid.uuid4().hex
			state_time = int(datetime.now().timestamp())
			state_hmac = hmac.new(
				bytes(config.secret, "UTF-8"),
				bytes(f"{state_time}-{state}", "UTF-8"),
				hashlib.sha256
			).hexdigest()

			image_message = {
				"type": "image_url",
				"image_url": {
					"url": (
						f"https://{config.domain}/api/chatgpt/images/{image_id}"
						f"?time={state_time}&state={state}&token={state_hmac}"
					),
					"detail": "high"
				}
			}

			user_message["content"].append(image_message)

		chat_data.append(user_message)

		# 呼叫 AI
		msgid += 1
		chat_config = self.get_chat_config(chatid)

		res_cls = GPTres(
			chatid=chatid,
			msgid=msgid,
			ctx=ctx,
			user_msg=text,
			image_id=image_id,
			image_url=image_url,
			color=chat_config["color"],
		)

		try:
			if chat_config["api"] == "responses":
				new_res_cls = await self.chat_responses(
					message=chat_data,
					max_tokens=max_tokens,
					chatid=chatid,
					instructions=chat_config["instructions"],
					res_cls=res_cls
				)
			else:
				new_res_cls = await self.chat(
					message=chat_data,
					max_tokens=max_tokens,
					chatid=chatid,
					res_cls=res_cls,
					**chat_config["route"]
				)

			# 更新資料庫
			timestamp = int(datetime.now().timestamp())

			self.chatgptdb.execute(
				"UPDATE memo SET value = ? WHERE key = ?",
				(chatid, "last")
			)
			self.chatgptdb.execute(
				"UPDATE list SET msgid = ? WHERE id = ?",
				(msgid, int(chatid))
			)
			self.chatgptdb.execute(
				"UPDATE list SET last_time = ? WHERE id = ?",
				(timestamp, int(chatid))
			)
			self.chatgptdb.execute(
				"UPDATE list SET last_msg = ? WHERE id = ?",
				(text, int(chatid))
			)
			self.chatgptdb.commit()

		except aiohttp.ClientResponseError as e:
			await ctx.followup.send(
				f"Request failed! {e.status}",
				ephemeral=True
			)
			return

		except asyncio.TimeoutError:
			await ctx.followup.send(
				"Request failed! Connection time out",
				ephemeral=True
			)
			return

		finally:
			if not res_cls.first_token.is_set():
				res_cls.first_token.set()

		# 儲存 chat.json
		chat_path = os.path.join(
			config.dir, "data", "chatgpt", str(chatid)
		)

		with open(os.path.join(chat_path, "chat.json"), "r+") as f:
			chat_log = json.load(f)

			message = {
				"id": msgid,
				"user": text,
				"assistant": new_res_cls.assistant_msg,
				"reasoning": new_res_cls.assistant_reasoning,
				"model": new_res_cls.model
			}

			if image_url:
				message["image_url"] = image_id

			chat_log.append(message)

			f.seek(0)
			json.dump(chat_log, f, ensure_ascii=False, indent=2)
			f.truncate()

		# 儲存 all.json
		with open(os.path.join(chat_path, "all.json"), "r+") as f:
			all_log = json.load(f)

			all_log.append({
				"role": "user",
				"content": text
			})
			all_log.append({
				"role": "assistant",
				"content": new_res_cls.assistant_msg,
				"reasoning_content": new_res_cls.assistant_reasoning
			})

			f.seek(0)
			json.dump(all_log, f, ensure_ascii=False, indent=2)
			f.truncate()

	@chatgptcom.command(name="regenerate")
	async def regenerate(
		self,
		ctx: discord.Interaction,
		chatid: int = None,
		max_tokens: int = 8192,
		message_id: int = None
	):
		await ctx.response.defer(ephemeral=True)

		if ctx.channel.id != self.chatgptchannel:
			await ctx.followup.send(
				"You can't use this command at this channel!",
				ephemeral=True
			)
			return

		if max_tokens % 4 != 0:
			await ctx.followup.send(
				"Invalid max tokens number",
				ephemeral=True
			)
			return

		if chatid is None:
			chatid = self.chatgptdb.execute(
				"SELECT value FROM memo WHERE key = ?",
				("last",)
			).fetchone()[0]

		if not self.ischat(chatid):
			await ctx.followup.send("Chat not found!", ephemeral=True)
			return

		await ctx.followup.send("Please wait...")

		# 取得並截斷對話紀錄
		chat_data, msgid = self.get_chat_data(
			chatid,
			message_id=message_id,
			api=chat_config["api"]
		)

		log_len = len(chat_data)

		if log_len == 3:
			chat_data = chat_data[:2]
		elif log_len > 3:
			chat_data = chat_data[:log_len - 1]
		else:
			await ctx.followup.send(
				"Cut chat log failed",
				ephemeral=True
			)
			return

		last_msg = chat_data[-1]["content"]

		# 呼叫 AI
		chat_config = self.get_chat_config(chatid)

		res_cls = GPTres(
			chatid=chatid,
			msgid=msgid,
			ctx=ctx,
			 color=chat_config["color"],
		)

		try:
			if chat_config["api"] == "responses":
				new_res_cls = await self.chat_responses(
					message=chat_data,
					max_tokens=max_tokens,
					chatid=chatid,
					instructions=chat_config["instructions"],
					res_cls=res_cls
				)
			else:
				new_res_cls = await self.chat(
					message=chat_data,
					max_tokens=max_tokens,
					chatid=chatid,
					res_cls=res_cls,
					**chat_config["route"]
				)

			# 更新資料庫
			self.chatgptdb.execute(
				"UPDATE memo SET value = ? WHERE key = ?",
				(chatid, "last")
			)
			self.chatgptdb.execute(
				"UPDATE list SET last_time = ? WHERE id = ?",
				(int(datetime.now().timestamp()), int(chatid))
			)

			if message_id is None:
				self.chatgptdb.execute(
					"UPDATE list SET last_msg = ? WHERE id = ?",
					(last_msg, int(chatid))
				)

			self.chatgptdb.commit()

		except aiohttp.ClientResponseError as e:
			await ctx.followup.send(
				f"Request failed! {e.status}",
				ephemeral=True
			)
			return

		except asyncio.TimeoutError:
			await ctx.followup.send(
				"Request failed! Connection time out",
				ephemeral=True
			)
			return

		finally:
			if not res_cls.first_token.is_set():
				res_cls.first_token.set()

		chat_path = os.path.join(
			config.dir, "data", "chatgpt", str(chatid)
		)

		# 更新 chat.json
		with open(os.path.join(chat_path, "chat.json"), "r+") as f:
			chat_log = json.load(f)

			if message_id is not None:
				for message in chat_log:
					if message["id"] == message_id:
						message["assistant"] = new_res_cls.assistant_msg
						message["reasoning"] = new_res_cls.assistant_reasoning
						break
			else:
				chat_log[-1]["assistant"] = new_res_cls.assistant_msg
				chat_log[-1]["reasoning"] = new_res_cls.assistant_reasoning

			f.seek(0)
			json.dump(chat_log, f, ensure_ascii=False, indent=2)
			f.truncate()

		# 更新 all.json
		with open(os.path.join(chat_path, "all.json"), "r+") as f:
			all_log = json.load(f)

			all_log.append({
				"role": "assistant",
				"content": new_res_cls.assistant_msg,
				"reasoning_content": new_res_cls.assistant_reasoning
			})

			f.seek(0)
			json.dump(all_log, f, ensure_ascii=False, indent=2)
			f.truncate()

	@chatgptcom.command(name="create")
	async def create(self, ctx:discord.Interaction, name:str, sex:str, system:str = ""):
		await ctx.response.defer(ephemeral=True)
		chat_uuid = str(uuid.uuid4()).replace("-", "")
		count = self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("count",)).fetchone()[0]
		count += 1
		self.chatgptdb.execute('UPDATE memo SET value = ? WHERE key = ?', (count, "count"))
		self.chatgptdb.execute('UPDATE memo SET value = ? WHERE key = ?', (count, "last"))
		self.chatgptdb.execute('INSERT INTO list VALUES (?, ?, ?, ?, ?, ?, ?)', (count, name, chat_uuid, 0, int(datetime.now().timestamp()), None, sex))
		self.chatgptdb.commit()
		os.mkdir(os.path.join(config.dir, 'data', 'chatgpt', str(count)))
		with open(os.path.join(config.dir, 'data', 'chatgpt', str(count), "all.json"), 'w') as f:
			json.dump([], f, ensure_ascii=False, indent=2)
		with open(os.path.join(config.dir, 'data', 'chatgpt', str(count), "chat.json"), 'w') as f:
			json.dump([{"id": 0, "system": system}], f, ensure_ascii=False, indent=2)
		await ctx.followup.send(f"successfully | ChatID: {count}")

	@chatgptcom.command(name="export")
	async def export(self, ctx:discord.Interaction, chatid:int, type:int):
		await ctx.response.defer(ephemeral=True)
		# json = 0, txt = 1
		if not self.ischat(chatid):
			await ctx.followup.send("Chat not found!")
			return
		chat_name, chat_uuid = self.chatgptdb.execute('SELECT name, uuid FROM list WHERE id = ?', (chatid,)).fetchone()
		if type == 0:
			file = discord.File(fp=os.path.join(config.dir, 'data', 'chatgpt', str(chatid), 'all.json'), filename=f"{chat_name}.json")
			await ctx.followup.send(file=file)
		elif type == 1:
			with open(os.path.join(config.dir, 'data', 'chatgpt', str(chatid), 'all.json'), 'r') as f:
				data = json.load(f)
			a = 0
			chat = io.BytesIO()
			chat.write(bytes(f"{chat_name} ({chat_uuid})\n\n", "UTF-8"))
			for msg in data:
				if msg["role"] == "user":
					a += 1
					chat.write(bytes(f"{a} ------------------\n\n", "UTF-8"))
					chat.write(bytes("[user]\n", "UTF-8"))
					chat.write(bytes(f"{msg['content']}\n\n", "UTF-8"))
				elif msg["role"] == "assistant":
					chat.write(bytes("[assistant]\n", "UTF-8"))
					chat.write(bytes(f"{msg['content']}\n\n", "UTF-8"))
			chat.seek(0)
			file = discord.File(fp=chat, filename=f"{chat_uuid}.txt")
			await ctx.followup.send(file=file)

	@chatgptcom.command(name="rename")
	async def rename(self, ctx:discord.Interaction, chatid:int, name:str):
		await ctx.response.defer(ephemeral=True)
		pass

	@chatgptcom.command(name="delete")
	async def delete(self, ctx:discord.Interaction, chatid:int):
		await ctx.response.defer(ephemeral=True)
		pass

	async def image_pend(self, image_url: str):
		try:
			if image_url.startswith("http"):
				async with aiohttp.ClientSession() as session:
					async with session.get(image_url) as response:
						response.raise_for_status()
						image_data = await response.read()
			else:
				image_data = base64.b64decode(image_url.split(",")[1])

			image = Image.open(BytesIO(image_data))
			image_id = uuid.uuid4().hex

			image_path = os.path.join(
				config.dir, "data", "chatgpt", "images", f"{image_id}.png"
			)
			image.save(image_path, format="PNG")

			return image_id

		except Exception as e:
			raise ValueError("無法讀取圖片: ", e)

	def num_tokens_from_image(self, image_id:str):
		with open(os.path.join(config.dir, 'data', 'chatgpt', 'images', f'{image_id}.png'), 'rb') as f:
			image_data = f.read()
		image = Image.open(BytesIO(image_data))
		image_size = image.size
		max_dimension = max(image_size)
		min_dimension = min(image_size)

		if max_dimension > 2048:
			ratio = 2048 / max_dimension
			max_dimension = 2048
			min_dimension *= ratio

		if min_dimension > 768:
			ratio = 768 / min_dimension
			min_dimension = 768
			max_dimension *= ratio

		width_blocks = math.ceil(max_dimension / 512)
		height_blocks = math.ceil(min_dimension / 512)
		return ((width_blocks * height_blocks) * 170) + 85

	@chatgptcom.command(name="image")
	async def image_generate(self, ctx:discord.Interaction, prompt:str, size:str = "1024x1024", quality:str = "standard"):
		await ctx.response.defer(ephemeral=True)
		if ctx.channel.id != config.chatgpt_image_channel:
			await ctx.followup.send("You can't use this command at this channel!", ephemeral=True)
		await ctx.followup.send("Please wait...")
		headers = {
			"Content-Type": "application/json",
			"Authorization": f"Bearer {config.chatgpt_moderations}"
		}
		data = {
			"model": self.model_image,
			"prompt": f"I NEED to test how the tool works with extremely simple prompts. DO NOT add any detail, just use it AS-IS: {prompt}",
			"size": size,
			"quality": quality,
			"n": 1
		}
		try:
			async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as s:
				async with s.post("https://api.openai.com/v1/images/generations", data=json.dumps(data), headers=headers) as r:
					r.raise_for_status()
					response = await r.json()
					revised_prompt = response["data"][0]["revised_prompt"]
					image_url = response["data"][0]["url"]
					created = datetime.fromtimestamp(response["created"], tz=tz)
					processing_ms = r.headers["openai-processing-ms"]

				async with s.get(image_url) as r:
					r.raise_for_status()
					image_raw = await r.read()
					image_name = yarl.URL(image_url).name
					image_pil = Image.open(BytesIO(image_raw)).convert("RGB")
					image_info = {
						"prompt": prompt,
						"revised": revised_prompt,
						"size": size,
						"quality": quality,
						"model": self.model_image
					}
					image_with_info = stepic.encode(image_pil, json.dumps(image_info, ensure_ascii=False).encode())
					image_buffer = BytesIO()
					image_with_info.save(image_buffer, format="PNG")
		except Exception as e:
			await ctx.followup.send(f"Request failed! |  {e}", ephemeral=True)
			return

		embed_list = []
		embed = discord.Embed(color=discord.Colour(196287), description=prompt)
		embed.set_author(name=ctx.user.display_name, icon_url=ctx.user.avatar.url)
		embed_list.append(embed)

		embed = discord.Embed(color=discord.Colour(196193), description=revised_prompt)
		embed.set_author(name="OpenAI")
		embed.set_footer(text=f'{self.model_image} | {size} | {quality} | {processing_ms}ms')
		embed.timestamp = created
		image_buffer.seek(0)
		file = discord.File(fp=image_buffer, filename=image_name)
		embed.set_image(url=f"attachment://{image_name}")
		embed_list.append(embed)
		await ctx.followup.send(embeds=embed_list, file=file)

async def setup(bot):
	chatgptcog = ChatGPT(bot)
	await bot.add_cog(chatgptcog)