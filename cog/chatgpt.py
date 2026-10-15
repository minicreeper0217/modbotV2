import asyncio
import base64
import copy
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
from pathlib import Path
from urllib.parse import urlencode

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
from github import GitHub


tz = pytz.timezone("Asia/Taipei")


class GPTres:
	def __init__(self, chatid, msgid, ctx: discord.Interaction, color,
							 image_url=None, image_id=None, user_msg=None):
		self.chatid = chatid
		self.msgid = msgid
		self.ctx = ctx
		self.color = color
		self.image_url = image_url
		self.usage = None
		self.finish = False
		self.failed = False
		self.model = None
		self.completion_id = None
		self.process_time = 0
		self.assistant_modfoo = None
		self.assistant_msg = ""
		self.assistant_reasoning = ""
		self.response_items = []
		self.deleted_call_ids = set()
		self.embed_list = []
		self.discord_msg = None
		self.file = None
		self.first_token = asyncio.Event()
		self._send_lock = asyncio.Lock()
		self._last_sent = ""

		if user_msg:
			self.user_msg = user_msg
			embed = discord.Embed(
				color=discord.Colour(self.color),
				description=self.user_msg[:1900]
			)
			embed.set_author(
				name=ctx.user.display_name,
				icon_url=ctx.user.display_avatar.url
			)
			embed.set_footer(text=f"ChatID: {self.chatid} | MessageID: {self.msgid}")
			if image_url:
				file_name = f"{uuid.uuid4().hex}.png"
				image_path = os.path.join(config.dir, "data", "chatgpt", "images", f"{image_id}.png")
				self.file = discord.File(image_path, filename=file_name)
				embed.set_image(url=f"attachment://{file_name}")
			self.embed_list.append(embed)

		self._pending_task = asyncio.create_task(self.pending())

	async def pending(self):
		await self.first_token.wait()
		while not self.finish:
			if self.failed:
				break
			if self.assistant_msg and self.assistant_msg != self._last_sent:
				try:
					await self.send()
				except discord.HTTPException as e:
					logging.warning("Discord edit failed: %s", e)
			await asyncio.sleep(1)

	async def fail(self):
		self.failed = True
		self.finish = True
		self.first_token.set()

	async def done(self, process_time: float):
		self.process_time = process_time
		try:
			self.assistant_modfoo = await self.moderation(self.assistant_msg) if self.assistant_msg else ""
		except Exception:
			logging.exception("AI moderation failed")
			self.assistant_modfoo = ""
		self.finish = True
		self.first_token.set()
		await self.send()

	async def moderation(self, text: str) -> str:
		headers = {
			"Content-Type": "application/json",
			"Authorization": f"Bearer {config.chatgpt_moderations}"
		}
		data = {"model": "omni-moderation-latest", "input": text}
		async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
			async with session.post("https://api.openai.com/v1/moderations", json=data, headers=headers) as response:
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
			text = self.assistant_msg or "（沒有文字回覆）"
			# Discord 每個 embed description 最多 4096 字元；為保留 footer，尾段最多一個 embed。
			# 另外保留整個回答於 chat.json；Discord 只是顯示摘要。
			if len(text) > 3900:
				text = text[:3880] + "\n…（內容過長，已截斷）"
			embed = discord.Embed(color=discord.Colour(self.color), description=text)
			embed.set_author(name="ChatGPT")
			if self.finish:
				if self.usage:
					usage_text = (
						f"tokens: {self.usage['total_tokens']}"
						f"({self.usage['prompt_tokens']}/{self.usage['completion_tokens']})"
					)
				else:
					usage_text = "tokens: unknown"
				embed.set_footer(text=(
					f"{self.model} | {self.completion_id} | {usage_text} | "
					f"{self.process_time}s{self.assistant_modfoo or ''}"
				)[:2048])
			embed_list.append(embed)
			if self.discord_msg is None:
				kwargs = {"embeds": embed_list, "wait": True}
				if self.file is not None:
					kwargs["file"] = self.file
				self.discord_msg = await self.ctx.followup.send(**kwargs)
			else:
				self.discord_msg = await self.discord_msg.edit(embeds=embed_list)
			self._last_sent = self.assistant_msg


class ChatGPT(commands.Cog):
	chatgptcom = app_commands.Group(name="chatgpt", description="chat with chatgpt")
	MAX_TOOL_ROUNDS = 5
	MAX_CALLS_PER_ROUND = 3
	MAX_TOOL_OUTPUT_BYTES = 200_000
	MAX_SAVED_FILE_BYTES = 1_000_000
	FILE_TTL_SECONDS = 3 * 24 * 60 * 60

	def __init__(self, bot):
		self.bot: commands.Bot = bot
		intro_dir = Path(config.dir) / "data" / "chatgpt" / "intro"
		c1_intro = (intro_dir / "c1").read_text(encoding="UTF-8")
		c2_intro = (intro_dir / "c2").read_text(encoding="UTF-8")
		# 新的 c3 提示詞檔案必須存在。
		c3_intro = (intro_dir / "c3").read_text(encoding="UTF-8")

		self.route_openai = {"token": config.chatgpt_moderations}
		self.route_girl = {
			"base_url": "scfw7zf2j7me4nvbra5zdy7i.agents.do-ai.run",
			"token": config.chatgpt_girl_token
		}
		self.route_boy = {
			"base_url": "oxlzogl6d4b5dlkgvl275azi.agents.do-ai.run",
			"token": config.chatgpt_boy_token
		}
		self.chat_types = {
			"b": {"color": 0x7DD3FC, "api": "completions", "route": self.route_boy},
			"g": {"color": 0xF9A8D4, "api": "completions", "route": self.route_girl},
			"c1": {"color": 0x86EFAC, "api": "responses", "instructions": c1_intro},
			"c2": {"color": 0xC4B5FD, "api": "responses", "instructions": c2_intro},
			"c3": {
				"color": 0xFBBF24,
				"api": "responses",
				"model": "gpt-6-sol",
				"instructions": c3_intro,
				"tools": True,
				"service_tier": "flex"
			}
		}
		self.model = "gpt-5.6-luna"
		self.model_image = "gpt-image-2.5-flare"
		self.chatgptchannel = config.chatgpt_channel
		self.github = GitHub(token=config.github_token)
		self._chat_locks = {}
		self.chatgptdb = sqlite3.connect(
			os.path.join(config.dir, "database", "chatgpt.db"), isolation_level=None
		)
		self.chatgptdb.execute("""
			CREATE TABLE IF NOT EXISTS files (
				id TEXT PRIMARY KEY,
				filename TEXT NOT NULL,
				created_at INTEGER NOT NULL,
				expires_at INTEGER NOT NULL
			)
		""")
		self.c3_tools = self._github_tool_definitions() + [self._save_file_tool_definition()]

	def _github_tool_definitions(self):
		descriptions = {
			"github_repository": ("Get basic repository information", ["repo"]),
			"github_read_file": ("Read UTF-8 source file in a GitHub repository", ["repo", "path", "ref"]),
			"github_list_directory": ("List files in a repository directory", ["repo", "path", "ref"]),
			"github_branches": ("List repository branches", ["repo", "page", "per_page"]),
			"github_commits": ("List repository commits", ["repo", "ref", "path", "page", "per_page"]),
			"github_commit": ("Get a commit and its changed files", ["repo", "sha"]),
			"github_compare": ("Compare two branches or commits", ["repo", "base", "head"]),
			"github_tree": ("Get file tree for a branch", ["repo", "ref", "recursive"]),
			"github_search_paths": ("Search filenames and paths in a specific branch", ["repo", "ref", "keyword", "limit"]),
			"github_search_code": ("Search code on repository default branch", ["repo", "query", "page", "per_page"])
		}
		optional = {"ref", "path", "page", "per_page", "recursive", "limit"}
		numbers = {"page", "per_page", "limit"}
		booleans = {"recursive"}
		result = []
		for name, (description, fields) in descriptions.items():
			properties = {}
			for field in fields:
				kind = "integer" if field in numbers else "boolean" if field in booleans else "string"
				properties[field] = {"type": [kind, "null"] if field in optional else kind}
			result.append({
				"type": "function", "name": name, "description": description,
				"strict": True,
				"parameters": {
					"type": "object", "properties": properties,
					"required": fields, "additionalProperties": False
				}
			})
		return result

	@staticmethod
	def _save_file_tool_definition():
		return {
			"type": "function",
			"name": "save_file",
			"description": "Save a UTF-8 text file and return a signed download URL valid for 3 days. "
										 "Use for generated Python, HTML, JSON, Markdown and other text files. "
										 "Maximum file size is 1 MB."
										 "After a successful save, the system may replace the historical content argument with (deleted) to reduce token usage."
										 "This is expected and does not mean the saved file is incorrect. Trust the tool's success result.",
			"strict": True,
			"parameters": {
				"type": "object",
				"properties": {
					"filename": {"type": "string", "description": "Download filename, e.g. app.py"},
					"content": {"type": "string", "description": "Complete UTF-8 text content"}
				},
				"required": ["filename", "content"],
				"additionalProperties": False
			}
		}

	async def save_file(self, filename: str, content: str):
		if not isinstance(filename, str) or not isinstance(content, str):
			raise ValueError("filename and content must be strings")
		if "\x00" in filename or "\r" in filename or "\n" in filename:
			raise ValueError("Invalid filename")
		# Both Unix and Windows separators must be treated as path separators.
		safe_name = filename.replace("\\", "/").split("/")[-1]
		if not safe_name or safe_name in (".", "..") or len(safe_name.encode("utf-8")) > 255:
			raise ValueError("Invalid filename")

		file_bytes = content.encode("utf-8")
		if len(file_bytes) > self.MAX_SAVED_FILE_BYTES:
			raise ValueError("File exceeds 1 MB limit")

		file_id = uuid.uuid4().hex
		created_at = int(time.time())
		expires_at = created_at + self.FILE_TTL_SECONDS
		file_dir = Path(config.dir) / "data" / "chatgpt" / "files"
		file_dir.mkdir(parents=True, exist_ok=True)
		file_path = file_dir / file_id
		# Exclusive creation avoids accidental overwrites.
		with file_path.open("xb") as f:
			f.write(file_bytes)

		try:
			self.chatgptdb.execute(
				"INSERT INTO files (id, filename, created_at, expires_at) VALUES (?, ?, ?, ?)",
				(file_id, safe_name, created_at, expires_at)
			)
		except Exception:
			file_path.unlink(missing_ok=True)
			raise

		state = uuid.uuid4().hex
		signature = hmac.new(
			config.secret.encode("UTF-8"),
			f"{file_id}-{expires_at}-{state}".encode("UTF-8"),
			hashlib.sha256
		).hexdigest()
		query = urlencode({"expires": expires_at, "state": state, "token": signature})
		return {
			"success": True,
			"filename": safe_name,
			"url": f"https://{config.domain}/api/chat-files/{file_id}?{query}",
			"expires_at": expires_at
		}

	def cleanup_expired_files(self):
		now = int(time.time())
		rows = self.chatgptdb.execute(
			"SELECT id FROM files WHERE expires_at <= ?", (now,)
		).fetchall()
		file_dir = Path(config.dir) / "data" / "chatgpt" / "files"
		removed = 0
		for (file_id,) in rows:
			(file_dir / file_id).unlink(missing_ok=True)
			self.chatgptdb.execute("DELETE FROM files WHERE id = ?", (file_id,))
			removed += 1
		return removed

	async def execute_tool(self, name: str, arguments: dict):
		if not isinstance(arguments, dict):
			raise ValueError("Tool arguments must be an object")
		# 將 nullable 參數轉成 GitHub 函式的預設值。
		args = {key: value for key, value in arguments.items() if value is not None}
		match name:
			case "save_file":
				return await self.save_file(**args)
			case "github_repository":
				return await self.github.repository(**args)
			case "github_read_file":
				return await self.github.read_file(**args)
			case "github_list_directory":
				return await self.github.list_directory(**args)
			case "github_branches":
				return await self.github.branches(**args)
			case "github_commits":
				return await self.github.commits(**args)
			case "github_commit":
				return await self.github.commit(**args)
			case "github_compare":
				return await self.github.compare(**args)
			case "github_tree":
				return await self.github.tree(**args)
			case "github_search_paths":
				return await self.github.search_paths(**args)
			case "github_search_code":
				return await self.github.search_code(**args)
			case _:
				raise ValueError(f"Unknown tool: {name}")
	@staticmethod
	def _tool_key(call):
		try:
			args = json.loads(call["arguments"])

			if call.get("name") == "save_file":
				return ("save_file", args["filename"])

			encoded = json.dumps(
				args, sort_keys=True, ensure_ascii=False,
				separators=(",", ":")
			)
		except (ValueError, TypeError, KeyError):
			encoded = call.get("arguments", "")

		return (call.get("name"), encoded)

	@classmethod
	def cleanup_tool_outputs(cls, items: list, new_call_ids: set[str] | None = None) -> set[str]:
		outputs = {
			item.get("call_id"): item
			for item in items
			if item.get("type") == "function_call_output"
		}
		calls = [item for item in items if item.get("type") == "function_call"]

		if new_call_ids is None:
			new_call_ids = {call.get("call_id") for call in calls}

		newest_success = {}

		for index, call in enumerate(calls):
			call_id = call.get("call_id")
			output = outputs.get(call_id)

			if call_id not in new_call_ids or not output:
				continue

			if call.get("name") == "save_file":
				try:
					result = json.loads(output["output"])
					if not isinstance(result, dict) or "url" not in result:
						continue
				except (ValueError, TypeError):
					continue
			elif output.get("output") == "(deleted)":
				continue

			newest_success[cls._tool_key(call)] = index

		deleted_ids = set()

		for index, call in enumerate(calls):
			call_id = call.get("call_id")
			output = outputs.get(call_id)
			last_index = newest_success.get(cls._tool_key(call))

			if last_index is None or index >= last_index or not output:
				continue

			if call.get("name") == "save_file":
				try:
					arguments = json.loads(call["arguments"])
				except (ValueError, TypeError, KeyError):
					continue

				if not isinstance(arguments, dict) or arguments.get("content") == "(deleted)":
					continue

				arguments["content"] = "(deleted)"
				call["arguments"] = json.dumps(arguments, ensure_ascii=False)

			else:
				if output.get("output") == "(deleted)":
					continue

				output["output"] = "(deleted)"
				deleted_ids.add(call_id)

		return deleted_ids

	def _chat_path(self, chatid):
		return Path(config.dir) / "data" / "chatgpt" / str(chatid)

	@staticmethod
	def _write_json(path: Path, data):
		temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
		try:
			with temp.open("w", encoding="UTF-8") as f:
				json.dump(data, f, ensure_ascii=False, indent=2)
			os.replace(temp, path)
		finally:
			temp.unlink(missing_ok=True)

	def sync_deleted_outputs(self, chatid, deleted_ids: set[str]):
		if not deleted_ids:
			return
		path = self._chat_path(chatid) / "chat.json"
		with path.open("r", encoding="UTF-8") as f:
			log = json.load(f)
		changed = False
		for entry in log:
			for item in entry.get("response_items", []):
				if item.get("type") == "function_call_output" and item.get("call_id") in deleted_ids:
					if item.get("output") != "(deleted)":
						item["output"] = "(deleted)"
						changed = True
		if changed:
			self._write_json(path, log)

	def num_tokens_from_messages(self, messages: list, model: str):
		try:
			encoding = tiktoken.encoding_for_model(model)
		except KeyError:
			encoding = tiktoken.get_encoding("cl100k_base")
		count = 3
		for message in messages:
			count += 3
			for key, value in message.items():
				if isinstance(value, str):
					count += len(encoding.encode(value))
				elif isinstance(value, list):
					for content in value:
						if content.get("type") == "text":
							count += len(encoding.encode(content["text"]))
						elif content.get("type") == "image_url":
							image_id = content["image_url"]["url"].split("/")[-1].split("?")[0]
							count += self.num_tokens_from_image(image_id)
				if key == "name":
					count += 1
		return count

	async def chat(self, message: list, max_tokens: int, chatid: str | int, base_url: str, token: str, res_cls: GPTres) -> GPTres:
		chat_uuid = self.chatgptdb.execute(
			"SELECT uuid FROM list WHERE id = ?", (int(chatid),)
		).fetchone()[0]
		data = {
			"messages": message,
			"max_tokens": max_tokens,
			"user": chat_uuid,
			"stream": True,
			"stream_options": {"include_usage": True}
		}
		headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
		timeout = aiohttp.ClientTimeout(total=610)
		url = f"https://{base_url}/api/v1/chat/completions"
		start_time = time.perf_counter()
		async with aiohttp.ClientSession(timeout=timeout) as session:
			async with session.post(url, json=data, headers=headers) as response:
				if response.status != 200:
					error = await response.text()
					logging.warning("AI Chat Request failed: %s", error[:1500])
					response.raise_for_status()
				if response.content_type == "text/event-stream":
					while True:
						raw_line = await response.content.readline()
						if not raw_line:
							raise RuntimeError("Chat Completions stream ended unexpectedly")
						line = raw_line.decode("utf-8").strip()
						if not line.startswith("data: "):
							continue
						event_data = line[6:]
						if event_data == "[DONE]":
							await res_cls.done(round(time.perf_counter() - start_time, 2))
							return res_cls
						event = json.loads(event_data)
						if res_cls.completion_id is None:
							res_cls.completion_id = event.get("id")
							res_cls.model = event.get("model")
						if event.get("usage"):
							res_cls.usage = event["usage"]
						if not event.get("choices"):
							continue
						delta = event["choices"][0].get("delta") or {}
						if delta.get("reasoning_content"):
							res_cls.assistant_reasoning += delta["reasoning_content"]
						if delta.get("content"):
							res_cls.assistant_msg += delta["content"]
							res_cls.first_token.set()
				else:
					event = await response.json()
					res_cls.completion_id = event.get("id")
					res_cls.model = event.get("model")
					res_cls.usage = event.get("usage")
					assistant = event["choices"][0]["message"]
					res_cls.assistant_msg = assistant.get("content") or ""
					res_cls.assistant_reasoning = assistant.get("reasoning_content") or ""
					await res_cls.done(round(time.perf_counter() - start_time, 2))
					return res_cls

	async def chat_responses(self, message: list, max_tokens: int, chatid: str | int,
													 instructions: str, res_cls: GPTres,
													 model: str | None = None, tools: list | None = None,
													 persist_history: bool = True, service_tier: str | None = None) -> GPTres:
		chat_uuid = self.chatgptdb.execute(
			"SELECT uuid FROM list WHERE id = ?", (int(chatid),)
		).fetchone()[0]
		inputs = copy.deepcopy(message)
		res_cls.response_items = []
		tools = tools or []
		headers = {
			"Authorization": f"Bearer {config.chatgpt_user}",
			"Content-Type": "application/json"
		}
		timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=610)
		start_time = time.perf_counter()
		total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

		async with aiohttp.ClientSession(timeout=timeout) as session:
			for round_index in range(self.MAX_TOOL_ROUNDS + 1):
				data = {
					"model": model or self.model,
					"instructions": instructions,
					"input": inputs,
					"max_output_tokens": max_tokens,
					"stream": True,
					"store": False,
					"service_tier": service_tier or "default",
					"include": ["reasoning.encrypted_content"],
					"safety_identifier": chat_uuid
				}
				if tools:
					data["tools"] = tools
					data["tool_choice"] = "auto" if round_index < self.MAX_TOOL_ROUNDS else "none"

				result = None
				message_phases = {}
				last_text_item = None
				async with session.post("https://api.openai.com/v1/responses", json=data, headers=headers) as response:
					if response.status != 200:
						body = await response.text()
						raise RuntimeError(f"Responses API HTTP {response.status}: {body[:1500]}")
					while True:
						raw_line = await response.content.readline()
						if not raw_line:
							break
						line = raw_line.decode("utf-8").strip()
						if not line.startswith("data:"):
							continue
						event_text = line[5:].strip()
						if not event_text or event_text == "[DONE]":
							continue
						event = json.loads(event_text)
						match event.get("type"):
							case "response.created":
								current = event["response"]
								res_cls.completion_id = current.get("id")
								res_cls.model = current.get("model")
							case "response.output_item.added":
								item = event.get("item", {})

								if item.get("type") == "message":
									message_phases[event["output_index"]] = item.get("phase")
							case "response.output_text.delta":
								output_index = event.get("output_index")
								current_item = (round_index, output_index)

								if current_item != last_text_item:
									if res_cls.assistant_msg:
										res_cls.assistant_msg += "\n\n"

									last_text_item = current_item

								res_cls.assistant_msg += event["delta"]
								res_cls.first_token.set()
							case "response.reasoning_summary_text.delta":
								res_cls.assistant_reasoning += event["delta"]
							case "response.completed":
								result = event["response"]
							case "response.failed":
								raise RuntimeError(f"Responses API failed: {event['response'].get('error')}")
							case "response.incomplete":
								raise RuntimeError(
									f"Responses API incomplete: {event['response'].get('incomplete_details')}"
								)

				if result is None:
					raise RuntimeError("Responses API stream ended unexpectedly")
				usage = result.get("usage") or {}
				total_usage["prompt_tokens"] += usage.get("input_tokens", 0)
				total_usage["completion_tokens"] += usage.get("output_tokens", 0)
				total_usage["total_tokens"] += usage.get("total_tokens", 0)
				res_cls.usage = total_usage.copy()
				res_cls.completion_id = result.get("id")
				res_cls.model = result.get("model")

				output_items = result.get("output") or []
				# 深複製不是必需，但可明確保證 outputs 不會被意外共用。
				inputs.extend(copy.deepcopy(output_items))
				res_cls.response_items.extend(copy.deepcopy(output_items))
				calls = [item for item in output_items if item.get("type") == "function_call"]
				if not calls:
					await res_cls.done(round(time.perf_counter() - start_time, 2))
					return res_cls

				if round_index >= self.MAX_TOOL_ROUNDS:
					raise RuntimeError("Maximum tool rounds exceeded")
				if len(calls) > self.MAX_CALLS_PER_ROUND:
					raise RuntimeError("Too many tool calls in one round")

				good_ids = set()
				for call in calls:
					call_id = call["call_id"]
					name = call["name"]
					good = False
					try:
						arguments = json.loads(call["arguments"])
						if not isinstance(arguments, dict):
							raise ValueError("Tool arguments must be an object")
						tool_result = await asyncio.wait_for(
							self.execute_tool(name, arguments), timeout=60
						)
						text = json.dumps(tool_result, ensure_ascii=False)
						if len(text.encode("utf-8")) > self.MAX_TOOL_OUTPUT_BYTES:
							text = json.dumps({"error": "Tool result is too large"})
						else:
							good = True
					except Exception as e:
						logging.warning("Tool %s failed: %s", name, e)
						text = json.dumps({"error": f"{type(e).__name__}: {str(e)[:500]}"}, ensure_ascii=False)
					output = {"type": "function_call_output", "call_id": call_id, "output": text}
					inputs.append(copy.deepcopy(output))
					res_cls.response_items.append(copy.deepcopy(output))
					if good:
						good_ids.add(call_id)

				if good_ids:
					# 只清除同工具+同參數的舊結果，且新的呼叫必須成功。
					deleted_ids = self.cleanup_tool_outputs(inputs, good_ids)
					call_arguments = {
						item["call_id"]: item["arguments"]
						for item in inputs
						if item.get("type") == "function_call"
						and item.get("name") == "save_file"
					}

					for item in res_cls.response_items:
						if item.get("type") == "function_call":
							if item.get("call_id") in call_arguments:
								item["arguments"] = call_arguments[item["call_id"]]
					res_cls.deleted_call_ids.update(deleted_ids)
					# inputs 是 deepcopy，所以同一回合的 response_items 也要獨立同步。
					for item in res_cls.response_items:
						if item.get("type") == "function_call_output" and item.get("call_id") in deleted_ids:
							item["output"] = "(deleted)"
					if persist_history:
						self.sync_deleted_outputs(chatid, deleted_ids)

		raise RuntimeError("Tool execution loop ended unexpectedly")

	def ischat(self, chatid: str | int) -> bool:
		cursor = self.chatgptdb.execute("SELECT * FROM list WHERE id = ?", (int(chatid),)).fetchone()
		return cursor is not None and self._chat_path(chatid).is_dir()

	def get_chat_config(self, chatid: str | int) -> dict:
		chat_type = self.chatgptdb.execute(
			"SELECT sex FROM list WHERE id = ?", (int(chatid),)
		).fetchone()[0]
		return self.chat_types.get(chat_type, self.chat_types["g"])

	def token_set(self, tokens_usage: dict, token_punish: int) -> int:
		tokens = self.chatgptdb.execute("SELECT value FROM memo WHERE key = ?", ("token_limit",)).fetchone()[0]
		total_tokens = tokens_usage["prompt_tokens"] + tokens_usage["completion_tokens"] * 3
		remain = max(tokens - total_tokens * token_punish, 0)
		self.chatgptdb.execute("UPDATE memo SET value = ? WHERE key = ?", (remain, "token_limit"))
		self.chatgptdb.commit()
		return remain

	def _user_input(self, text: str, image_id: str | None, api: str):
		user_message = {"role": "user", "content": text}
		if image_id:
			state = uuid.uuid4().hex
			timestamp = int(datetime.now().timestamp())
			signature = hmac.new(
				config.secret.encode("UTF-8"),
				f"{timestamp}-{state}".encode("UTF-8"), hashlib.sha256
			).hexdigest()
			image_url = (
				f"https://{config.domain}/api/chatgpt/images/{image_id}"
				f"?time={timestamp}&state={state}&token={signature}"
			)
			if api == "responses":
				user_message["content"] = [
					{"type": "input_text", "text": text},
					{"type": "input_image", "image_url": image_url}
				]
			else:
				user_message["content"] = [
					{"type": "text", "text": text},
					{"type": "image_url", "image_url": {"url": image_url, "detail": "high"}}
				]
		return user_message

	def get_chat_data(self, chatid: str | int, message_id: int | None = None,
										api: str = "completions", exclude_last_assistant: bool = False) -> tuple[list, int]:
		msgid = self.chatgptdb.execute(
			"SELECT msgid FROM list WHERE id = ?", (int(chatid),)
		).fetchone()[0]
		with (self._chat_path(chatid) / "chat.json").open("r", encoding="UTF-8") as f:
			chat_log = json.load(f)
		chat_data = []
		last_id = chat_log[-1]["id"]
		for entry in chat_log:
			if entry["id"] == 0:
				system_content = (
					"Region: Japan\n"
					f"Time Now: {datetime.now(tz=pytz.timezone('Asia/Tokyo')).isoformat(timespec='seconds')}\n"
					"Note: The time will be updated each time it is sent.\n\n"
					f"{entry['system']}"
				)
				chat_data.append({
					"role": "system" if api == "responses" else "user",
					"content": system_content
				})
				continue
			chat_data.append(self._user_input(entry["user"], entry.get("image_url"), api))
			is_target = entry["id"] == (message_id if message_id is not None else last_id)
			if exclude_last_assistant and is_target:
				return chat_data, msgid
			if api == "responses" and "response_items" in entry:
				response_items = copy.deepcopy(entry["response_items"])
				chat_data.extend(copy.deepcopy(entry["response_items"]))
			else:
				chat_data.append({"role": "assistant", "content": entry["assistant"]})
			if message_id is not None and is_target:
				return chat_data, msgid
		return chat_data, msgid

	@chatgptcom.command(name="model")
	async def GPT_model(self, ctx: discord.Interaction, model: int):
		await ctx.response.defer(ephemeral=True)
		pass

	@chatgptcom.command(name="tokens")
	async def tokens(self, ctx: discord.Interaction):
		await ctx.response.defer(ephemeral=True)
		pass

	async def _respond(self, chatid, chat_config, chat_data, max_tokens, res_cls):
		if chat_config["api"] == "responses":
			return await self.chat_responses(
				message=chat_data, max_tokens=max_tokens, chatid=chatid,
				instructions=chat_config["instructions"], res_cls=res_cls,
				model=chat_config.get("model"),
				tools=self.c3_tools if chat_config.get("tools") else None,
				persist_history=False,
				service_tier=chat_config.get("service_tier")
			)
		return await self.chat(
			message=chat_data, max_tokens=max_tokens, chatid=chatid,
			res_cls=res_cls, **chat_config["route"]
		)

	@staticmethod
	def _apply_deleted_ids(chat_log, deleted_ids):
		for entry in chat_log:
			for item in entry.get("response_items", []):
				if item.get("type") == "function_call_output" and item.get("call_id") in deleted_ids:
					item["output"] = "(deleted)"

	@chatgptcom.command(name="generate")
	async def generate(self, ctx: discord.Interaction, text: str, chatid: int = None,
										 max_tokens: int = 8192, attachment: discord.Attachment = None):
		await ctx.response.defer(ephemeral=True)
		if ctx.channel.id != self.chatgptchannel:
			await ctx.followup.send("You can't use this command at this channel!", ephemeral=True)
			return
		if max_tokens % 4 != 0 or max_tokens <= 0:
			await ctx.followup.send("Invalid max tokens number", ephemeral=True)
			return
		if chatid is None:
			chatid = self.chatgptdb.execute(
				"SELECT value FROM memo WHERE key = ?", ("last",)
			).fetchone()[0]
		if not self.ischat(chatid):
			await ctx.followup.send("Chat not found!", ephemeral=True)
			return
		lock = self._chat_locks.setdefault(int(chatid), asyncio.Lock())
		async with lock:
			await self._generate_locked(ctx, text, chatid, max_tokens, attachment)

	async def _generate_locked(self, ctx, text, chatid, max_tokens, attachment):
		chat_config = self.get_chat_config(chatid)

		if attachment and chat_config["api"] != "responses":
			await ctx.followup.send("此聊天類型不支援附件", ephemeral=True)
			return

		image_id = None
		image_url = None

		if attachment:
			if attachment.content_type not in ("image/png", "image/jpeg", "image/webp"):
				await ctx.followup.send("目前只支援 PNG、JPEG、WEBP 圖片", ephemeral=True)
				return

			if attachment.size > 20 * 1024 * 1024:
				await ctx.followup.send("圖片大小不能超過 20 MB", ephemeral=True)
				return

			try:
				image_url = attachment.url
				image_id = await self.image_pend(image_url)
			except Exception as e:
				await ctx.followup.send(str(e), ephemeral=True)
				return

		await ctx.followup.send("Please wait...")
		chat_data, msgid = self.get_chat_data(chatid, api=chat_config["api"])
		chat_data.append(self._user_input(text, image_id, chat_config["api"]))
		msgid += 1
		res_cls = GPTres(
			chatid=chatid, msgid=msgid, ctx=ctx, user_msg=text,
			image_id=image_id, image_url=image_url, color=chat_config["color"]
		)
		try:
			result = await self._respond(chatid, chat_config, chat_data, max_tokens, res_cls)
		except Exception as e:
			await res_cls.fail()
			logging.exception("AI Chat Request failed | ChatID: %s", chatid)
			await ctx.followup.send(f"Request failed! {type(e).__name__}: {e}", ephemeral=True)
			return

		timestamp = int(datetime.now().timestamp())
		chat_path = self._chat_path(chatid)
		with (chat_path / "chat.json").open("r", encoding="UTF-8") as f:
			chat_log = json.load(f)
		self._apply_deleted_ids(chat_log, getattr(result, "deleted_call_ids", set()))
		item = {
			"id": msgid, "user": text,
			"assistant": result.assistant_msg,
			"reasoning": result.assistant_reasoning,
			"model": result.model
		}
		if image_id:
			item["image_url"] = image_id
		if chat_config["api"] == "responses":
			item["response_items"] = result.response_items
		chat_log.append(item)
		self._write_json(chat_path / "chat.json", chat_log)

		with (chat_path / "all.json").open("r", encoding="UTF-8") as f:
			all_log = json.load(f)
		all_log.append({"role": "user", "content": text})
		all_log.append({
			"role": "assistant", "content": result.assistant_msg,
			"reasoning_content": result.assistant_reasoning
		})
		self._write_json(chat_path / "all.json", all_log)

		self.chatgptdb.execute("UPDATE memo SET value = ? WHERE key = ?", (chatid, "last"))
		self.chatgptdb.execute("UPDATE list SET msgid = ? WHERE id = ?", (msgid, int(chatid)))
		self.chatgptdb.execute("UPDATE list SET last_time = ? WHERE id = ?", (timestamp, int(chatid)))
		self.chatgptdb.execute("UPDATE list SET last_msg = ? WHERE id = ?", (text, int(chatid)))
		self.chatgptdb.commit()

	@chatgptcom.command(name="regenerate")
	async def regenerate(self, ctx: discord.Interaction, chatid: int = None,
											 max_tokens: int = 8192, message_id: int = None):
		await ctx.response.defer(ephemeral=True)
		if ctx.channel.id != self.chatgptchannel:
			await ctx.followup.send("You can't use this command at this channel!", ephemeral=True)
			return
		if max_tokens % 4 != 0 or max_tokens <= 0:
			await ctx.followup.send("Invalid max tokens number", ephemeral=True)
			return
		if chatid is None:
			chatid = self.chatgptdb.execute(
				"SELECT value FROM memo WHERE key = ?", ("last",)
			).fetchone()[0]
		if not self.ischat(chatid):
			await ctx.followup.send("Chat not found!", ephemeral=True)
			return
		lock = self._chat_locks.setdefault(int(chatid), asyncio.Lock())
		async with lock:
			await self._regenerate_locked(ctx, chatid, max_tokens, message_id)

	async def _regenerate_locked(self, ctx, chatid, max_tokens, message_id):
		chat_config = self.get_chat_config(chatid)
		chat_path = self._chat_path(chatid)
		with (chat_path / "chat.json").open("r", encoding="UTF-8") as f:
			chat_log = json.load(f)
		entries = [entry for entry in chat_log if entry["id"] != 0]
		if not entries:
			await ctx.followup.send("No messages to regenerate", ephemeral=True)
			return
		target_id = message_id if message_id is not None else entries[-1]["id"]
		target = next((entry for entry in entries if entry["id"] == target_id), None)
		if target is None:
			await ctx.followup.send("Message not found", ephemeral=True)
			return

		chat_data, _ = self.get_chat_data(
			chatid, message_id=target_id, api=chat_config["api"],
			exclude_last_assistant=True
		)
		if len(chat_data) <= 1:
			await ctx.followup.send("Cut chat log failed", ephemeral=True)
			return
		await ctx.followup.send("Please wait...")
		res_cls = GPTres(chatid=chatid, msgid=target_id, ctx=ctx, color=chat_config["color"])
		try:
			result = await self._respond(chatid, chat_config, chat_data, max_tokens, res_cls)
		except Exception as e:
			await res_cls.fail()
			logging.exception("AI Regenerate failed | ChatID: %s", chatid)
			await ctx.followup.send(f"Request failed! {type(e).__name__}: {e}", ephemeral=True)
			return

		# 只改動選定的訊息；如是舊訊息，其後對話保持原樣。
		with (chat_path / "chat.json").open("r", encoding="UTF-8") as f:
			chat_log = json.load(f)
		self._apply_deleted_ids(chat_log, getattr(result, "deleted_call_ids", set()))
		target = next((entry for entry in chat_log if entry["id"] == target_id), None)
		if target is None:
			await ctx.followup.send("Message not found", ephemeral=True)
			return
		target["assistant"] = result.assistant_msg
		target["reasoning"] = result.assistant_reasoning
		target["model"] = result.model
		if chat_config["api"] == "responses":
			target["response_items"] = result.response_items
		else:
			target.pop("response_items", None)
		self._write_json(chat_path / "chat.json", chat_log)

		with (chat_path / "all.json").open("r", encoding="UTF-8") as f:
			all_log = json.load(f)
		# all.json 目前是供匯出的時間紀錄，維持原本追加方式。
		all_log.append({
			"role": "assistant", "content": result.assistant_msg,
			"reasoning_content": result.assistant_reasoning
		})
		self._write_json(chat_path / "all.json", all_log)
		self.chatgptdb.execute("UPDATE memo SET value = ? WHERE key = ?", (chatid, "last"))
		self.chatgptdb.execute(
			"UPDATE list SET last_time = ? WHERE id = ?",
			(int(datetime.now().timestamp()), int(chatid))
		)
		if message_id is None:
			self.chatgptdb.execute(
				"UPDATE list SET last_msg = ? WHERE id = ?", (target["user"], int(chatid))
			)
		self.chatgptdb.commit()

	@chatgptcom.command(name="create")
	async def create(self, ctx: discord.Interaction, name: str, sex: str, system: str = ""):
		await ctx.response.defer(ephemeral=True)
		if sex not in self.chat_types:
			await ctx.followup.send("Invalid chat type", ephemeral=True)
			return
		chat_uuid = uuid.uuid4().hex
		count = self.chatgptdb.execute(
			"SELECT value FROM memo WHERE key = ?", ("count",)
		).fetchone()[0] + 1
		self.chatgptdb.execute("UPDATE memo SET value = ? WHERE key = ?", (count, "count"))
		self.chatgptdb.execute("UPDATE memo SET value = ? WHERE key = ?", (count, "last"))
		self.chatgptdb.execute(
			"INSERT INTO list VALUES (?, ?, ?, ?, ?, ?, ?)",
			(count, name, chat_uuid, 0, int(datetime.now().timestamp()), None, sex)
		)
		self.chatgptdb.commit()
		chat_path = self._chat_path(count)
		chat_path.mkdir()
		self._write_json(chat_path / "all.json", [])
		self._write_json(chat_path / "chat.json", [{"id": 0, "system": system}])
		await ctx.followup.send(f"successfully | ChatID: {count}")

	@chatgptcom.command(name="export")
	async def export(self, ctx: discord.Interaction, chatid: int, type: int):
		await ctx.response.defer(ephemeral=True)
		if not self.ischat(chatid):
			await ctx.followup.send("Chat not found!")
			return
		chat_name, chat_uuid = self.chatgptdb.execute(
			"SELECT name, uuid FROM list WHERE id = ?", (chatid,)
		).fetchone()
		if type == 0:
			file = discord.File(
				fp=self._chat_path(chatid) / "all.json", filename=f"{chat_name}.json"
			)
			await ctx.followup.send(file=file)
		elif type == 1:
			with (self._chat_path(chatid) / "all.json").open("r", encoding="UTF-8") as f:
				data = json.load(f)
			a = 0
			chat = io.BytesIO()
			chat.write(f"{chat_name} ({chat_uuid})\n\n".encode("UTF-8"))
			for msg in data:
				if msg["role"] == "user":
					a += 1
					chat.write(f"{a} ------------------\n\n[user]\n{msg['content']}\n\n".encode("UTF-8"))
				elif msg["role"] == "assistant":
					chat.write(f"[assistant]\n{msg['content']}\n\n".encode("UTF-8"))
			chat.seek(0)
			await ctx.followup.send(file=discord.File(fp=chat, filename=f"{chat_uuid}.txt"))

	@chatgptcom.command(name="rename")
	async def rename(self, ctx: discord.Interaction, chatid: int, name: str):
		await ctx.response.defer(ephemeral=True)
		pass

	@chatgptcom.command(name="delete")
	async def delete(self, ctx: discord.Interaction, chatid: int):
		await ctx.response.defer(ephemeral=True)
		pass

	async def image_pend(self, image_url: str):
		try:
			if image_url.startswith("http"):
				async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
					async with session.get(image_url) as response:
						response.raise_for_status()
						image_data = await response.read()
			else:
				image_data = base64.b64decode(image_url.split(",", 1)[1])
			image = Image.open(BytesIO(image_data))
			image_id = uuid.uuid4().hex
			image_dir = self._chat_path("images")
			image_dir.mkdir(parents=True, exist_ok=True)
			image_path = self._chat_path("images") / f"{image_id}.png"
			image.save(image_path, format="PNG")
			return image_id
		except Exception as e:
			raise ValueError(f"無法讀取圖片: {e}") from e

	def num_tokens_from_image(self, image_id: str):
		with (self._chat_path("images") / f"{image_id}.png").open("rb") as f:
			image = Image.open(BytesIO(f.read()))
		max_dimension = max(image.size)
		min_dimension = min(image.size)
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
		return width_blocks * height_blocks * 170 + 85

	@chatgptcom.command(name="image")
	async def image_generate(self, ctx: discord.Interaction, prompt: str,
													 size: str = "1024x1024", quality: str = "standard"):
		await ctx.response.defer(ephemeral=True)
		if ctx.channel.id != config.chatgpt_image_channel:
			await ctx.followup.send("You can't use this command at this channel!", ephemeral=True)
			return
		await ctx.followup.send("Please wait...")
		headers = {
			"Content-Type": "application/json",
			"Authorization": f"Bearer {config.chatgpt_moderations}"
		}
		data = {
			"model": self.model_image,
			"prompt": (
				"I NEED to test how the tool works with extremely simple prompts. "
				f"DO NOT add any detail, just use it AS-IS: {prompt}"
			),
			"size": size, "quality": quality, "n": 1
		}
		try:
			async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
				async with session.post(
					"https://api.openai.com/v1/images/generations", json=data, headers=headers
				) as response:
					response.raise_for_status()
					result = await response.json()
					revised_prompt = result["data"][0]["revised_prompt"]
					image_url = result["data"][0]["url"]
					created = datetime.fromtimestamp(result["created"], tz=tz)
					processing_ms = response.headers.get("openai-processing-ms", "?")
				async with session.get(image_url) as response:
					response.raise_for_status()
					image_raw = await response.read()
					image_name = yarl.URL(image_url).name
					image_pil = Image.open(BytesIO(image_raw)).convert("RGB")
					image_info = {
						"prompt": prompt, "revised": revised_prompt, "size": size,
						"quality": quality, "model": self.model_image
					}
					image_with_info = stepic.encode(
						image_pil, json.dumps(image_info, ensure_ascii=False).encode()
					)
					image_buffer = BytesIO()
					image_with_info.save(image_buffer, format="PNG")
		except Exception as e:
			await ctx.followup.send(f"Request failed! | {e}", ephemeral=True)
			return
		embed_list = []
		embed = discord.Embed(color=discord.Colour(196287), description=prompt[:4096])
		embed.set_author(name=ctx.user.display_name, icon_url=ctx.user.display_avatar.url)
		embed_list.append(embed)
		embed = discord.Embed(color=discord.Colour(196193), description=revised_prompt[:4096])
		embed.set_author(name="OpenAI")
		embed.set_footer(text=f"{self.model_image} | {size} | {quality} | {processing_ms}ms")
		embed.timestamp = created
		image_buffer.seek(0)
		file = discord.File(fp=image_buffer, filename=image_name)
		embed.set_image(url=f"attachment://{image_name}")
		embed_list.append(embed)
		await ctx.followup.send(embeds=embed_list, file=file)


async def setup(bot):
	chatgptcog = ChatGPT(bot)
	await bot.add_cog(chatgptcog)
