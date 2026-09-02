import aiohttp
import asyncio
import config
import json
import logging

async def by_bot(channel_id:int, message:dict | aiohttp.FormData) -> dict:
	url = f'https://discord.com/api/v10/channels/{channel_id}/messages'
	headers = {
		'Authorization': f'Bot {config.token}',
		"User-Agent": config.bot_agent
	}
	if isinstance(message, dict):
		headers["Content-Type"] = "application/json"
		formdata = False
		data = json.dumps(message)
	elif isinstance(message, aiohttp.FormData):
		formdata = True
		data = message
	else:
		raise TypeError(f"Message must be dict or aiohttp.FormData, Not {message.__class__.__name__}")
	retries = 0
	async with aiohttp.ClientSession() as session:
		while True:
			try:
				async with session.post(url, headers=headers, data=data) as r:
					r.raise_for_status()
					js = await r.json()
					return js
			except:
				retries += 1
				ratelimit = float(r.headers['retry-after']) if r.headers.get('retry-after') else float(r.headers['x-ratelimit-reset-after']) if r.headers.get('x-ratelimit-reset-after') else float(1)
				e = f"{r.status} {r.reason}"
				if retries == 2 or formdata:
					logging.warning(f"Send a message Failed!! Reason: {e.__class__.__name__}: {e}")
					raise
				await asyncio.sleep(ratelimit)

async def by_webhook(url:str, message:dict | aiohttp.FormData) -> str:
	headers = {
		"User-Agent": config.bot_agent
	}
	if isinstance(message, dict):
		headers['Content-Type'] = 'application/json'
		data = json.dumps(message)
		formdata = False
	elif isinstance(message, aiohttp.FormData):
		data = message
		formdata = True
	else:
		raise TypeError(f"Message must be dict or aiohttp.FormData, Not {message.__class__.__name__}")
	retries = 0
	js = None
	async with aiohttp.ClientSession() as session:
		while True:
			try:
				async with session.post(url=f"{url}?wait=true", headers=headers, data=data) as r:
					js = await r.json()
					r.raise_for_status()
					return js['id']
			except:
				retries += 1
				ratelimit = float(r.headers['retry-after']) if r.headers.get('retry-after') else float(r.headers['x-ratelimit-reset-after']) if r.headers.get('x-ratelimit-reset-after') else float(1)
				e = f"{r.status} {r.reason}"
				if retries == 2 or formdata:
					logging.warning(f"Send a message Failed!! Reason: {e.__class__.__name__}: {e} | {js}")
					raise
				await asyncio.sleep(ratelimit)

async def update_bywebhook(url:str, id:str, message:dict | aiohttp.FormData) -> None:
	url = f"{url}/messages/{id}"
	fetch = await get_message(url=url,authorization=False)
	if fetch is None:
		return
	headers = {
		"User-Agent": config.bot_agent
	}
	if isinstance(message, dict):
		headers['Content-Type'] = 'application/json'
		data = json.dumps(message)
		formdata = False
	elif isinstance(message, aiohttp.FormData):
		data = message
		formdata = True
	else:
		raise TypeError(f"Message must be one of dict or aiohttp.FormData, Not {message.__class__.__name__}")
	retries = 0
	js = None
	async with aiohttp.ClientSession() as session:
		while True:
			try:
				async with session.patch(url=url, headers=headers, data=data) as r:
					js = await r.json()
					r.raise_for_status()
					return
			except:
				retries += 1
				ratelimit = float(r.headers['retry-after']) if r.headers.get('retry-after') else float(r.headers['x-ratelimit-reset-after']) if r.headers.get('x-ratelimit-reset-after') else float(1)
				e = f"{r.status} {r.reason}"
				if retries == 2 or formdata:
					logging.warning(f"Edit a message Failed!! Reason: {e.__class__.__name__}: {e} | {js}")
					raise
				await asyncio.sleep(ratelimit)

async def delete_bywebhook(url:str, id:str) -> None:
	url = f"{url}/messages/{id}"
	fetch = await get_message(url=url,authorization=False)
	if fetch is None:
		return
	retries = 0
	headers = {
		"User-Agent": config.bot_agent
	}
	async with aiohttp.ClientSession() as session:
		while True:
			try:
				async with session.delete(url=url, headers=headers) as r:
					r.raise_for_status()
					return
			except:
				retries += 1
				ratelimit = float(r.headers['retry-after']) if r.headers.get('retry-after') else float(r.headers['x-ratelimit-reset-after']) if r.headers.get('x-ratelimit-reset-after') else float(1)
				e = f"{r.status} {r.reason}"
				if retries == 2:
					logging.warning(f"Delete a message Failed!! Reason: {e.__class__.__name__}: {e}")
					raise
				await asyncio.sleep(ratelimit)

async def get_message(url:str, authorization:bool=True) -> (dict | None):
	headers = {
		"User-Agent": config.bot_agent
	}
	if authorization:
		headers['Authorization'] = f'Bot {config.token}'
	js = None
	try:
		async with aiohttp.ClientSession() as session:
			async with session.get(url=url, headers=headers) as r:
				js = await r.json()
				if r.status == 404 and js['code'] == 10008:
					return None
				r.raise_for_status()
				return js
	except Exception as e:
		logging.warning(f"Check message data Failed!! Reason: {e.__class__.__name__}: {e} | Discord payload: {js}")
		raise