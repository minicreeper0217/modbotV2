import discord
from datetime import datetime
import config
import aiohttp
import json
import pytz
import sends
import os
import re
import logging
import random
import asyncio
from PIL import Image
from io import BytesIO
import yarl
import sqlite3

tz = pytz.timezone('Asia/Taipei')
lock = asyncio.Lock()

async def youtube(video_id:str, youtubedb:sqlite3.Connection, channel_id:str=None, unlisted:bool=False, test:bool=False, repost_time:int | None=None):
	global lock
	async with lock:
		cursor = youtubedb.execute('SELECT msgid, etag, image_etag, image_type, live_stream FROM videoid WHERE id = ?', (video_id,)).fetchone()
		if cursor is not None:
			msgid, etag, image_etag, thumbnail_type, liveStream = cursor
		else:
			msgid = None
			etag = ""
			image_etag = ""
			thumbnail_type = None
			liveStream = 0
		if unlisted and not test:
			etag = ""

		async with aiohttp.ClientSession() as session:
			channel_cursor = youtubedb.execute('SELECT channel FROM subscribe WHERE id = ?', (channel_id,)).fetchone()
			if channel_cursor is None:
				logging.warning(f"Can't find channel webhook url! | Channel ID:{channel_id}")
				return
			elif channel_cursor[0] == 1:
				webhook_url = config.youtube_vt_webhook
			elif channel_cursor[0] == 2:
				webhook_url = config.youtube_etc_webhook
			
			async with session.get(f"https://www.youtube.com/shorts/{video_id}", allow_redirects=False) as r:
				if r.status == 200:
					if msgid is not None:
						await sends.delete_bywebhook(webhook_url, msgid)
						youtubedb.execute('DELETE FROM videoid WHERE id = ?', (video_id,))
						youtubedb.commit()
					return

			url = f'https://youtube.googleapis.com/youtube/v3/videos?part=snippet&part=liveStreamingDetails&part=status&part=contentDetails&id={video_id}'
			headers = {
				"Accept": "application/json",
				"Accept-Charset": "utf-8",
				"If-None-Match": etag,
				"X-goog-api-key": config.youtube_api
			}
			try:
				async with session.get(url, headers=headers) as r:
					r.raise_for_status()
					if r.status == 304:
						return
					data = await r.json()
					item = data['items'][0]
					etag = data['etag']
					if channel_id is None:
						channel_id = item['snippet']['channelId']
			except Exception as e:
				logging.warning(f'Check Video Failed: {e.__class__.__name__}: {e}')
				repost(video_id, repost_time, channel_id)
				return

			headers = {
				"Accept": "application/json",
				"Accept-Charset": "utf-8",
				"X-goog-api-key": config.youtube_api
			}
			url = f'https://youtube.googleapis.com/youtube/v3/channels?part=snippet&id={channel_id}'
			try:
				async with session.get(url, headers=headers) as r:
					r.raise_for_status()
					data = await r.json()
				channel_title = data['items'][0]['snippet']['title']
				channel_avatar_url = data['items'][0]['snippet']['thumbnails']['high']['url']
				channel_custom_url = data['items'][0]['snippet']['customUrl']
			except Exception as e:
				logging.warning(f'Check Channel info Failed: {e.__class__.__name__}: {e}')
				repost(video_id, repost_time, channel_id)
				return

			url = f"https://youtube.googleapis.com/youtube/v3/playlistItems?part=snippet&playlistId={re.sub(r"^UC", "UUMO", channel_id)}&maxResults=1&videoId={video_id}"
			Members_only = False
			try:
				async with session.get(url, headers=headers) as r:
					r.raise_for_status()
					data = await r.json()
					Members_only = True if data["items"] else False
			except:
				pass

			video_title = item['snippet']['title']
			video_publish_time = item['snippet']['publishedAt']
			video_id = item['id']
			livebroadcast = item['snippet']['liveBroadcastContent']

			if item['snippet']['thumbnails'].get('maxres'):
				video_thumbnail = item['snippet']['thumbnails']['maxres']['url']
				crop = False
			elif item['snippet']['thumbnails'].get('standard'):
				video_thumbnail = item['snippet']['thumbnails']['standard']['url']
				crop = True
			else:
				video_thumbnail = item['snippet']['thumbnails']['high']['url']
				crop = True
			if "_live" in video_thumbnail:
				video_thumbnail = video_thumbnail.replace("_live", "")
			thumbnail_newtype = video_thumbnail.split("/")[-1]

			premiere = False
			if livebroadcast == "none":
				if item.get("liveStreamingDetails") and item['liveStreamingDetails'].get('actualStartTime'):
					video_publish_time = item['liveStreamingDetails']['actualStartTime']
				if liveStream:
					footext = "直播開始時間"
				else:
					footext = "發佈時間"
			elif livebroadcast == "upcoming":
				if item.get("liveStreamingDetails") and item['liveStreamingDetails'].get('scheduledStartTime'):
					video_publish_time = item['liveStreamingDetails']['scheduledStartTime']
				if item['status']['uploadStatus'] == "uploaded":
					footext = "預定直播時間"
					liveStream = 1
				elif item['status']['uploadStatus'] == "processed":
					footext = "預定首播時間"
					premiere = True
				else:
					return
			elif livebroadcast == "live":
				if item.get("liveStreamingDetails") and item['liveStreamingDetails'].get('actualStartTime'):
					video_publish_time = item['liveStreamingDetails']['actualStartTime']
				if item['status']['uploadStatus'] == "uploaded":
					footext = "直播開始時間"
					liveStream = 1
				elif item['status']['uploadStatus'] == "processed":
					footext = "首播開始時間"
				else:
					return

			dur = ""
			if unlisted:
				dur += "🔗不公開 | "
			duration = item['contentDetails'].get('duration')
			if duration is not None:
				pattern = r'P((\d+)D)?(T)?((\d+)H)?((\d+)M)?((\d+)S)?'
				match = re.match(pattern, duration)
				if match:
					days  = int(match.group(2) or 0)
					hours = int(match.group(5) or 0)
					minutes = "{:02}".format(int(match.group(7) or 0))
					seconds = "{:02}".format(int(match.group(9) or 0))
					if days > 0:
						hours = int(hours + days*24)
					if hours == 0 and minutes == "00" and seconds == "00":
						pass
					elif (hours == 0 and minutes == "00") or (hours == 0 and minutes == "01" and seconds == "00"):
						return
					elif hours == 0:
						dur += f"{minutes}:{seconds} | "
					elif hours != 0:
						dur += f"{hours}:{minutes}:{seconds} | "
			if Members_only:
				dur += "會員專屬影片 | "

			publish_time = datetime.strptime(video_publish_time, "%Y-%m-%dT%H:%M:%S%z").timestamp()
			if test:
				image_etag = ""
			elif msgid is not None:
				if thumbnail_type != thumbnail_newtype:
					image_etag = ""
			else:
				now = datetime.now().timestamp()
				if publish_time < (now - 86400):
					return
				image_etag = ""

			try:
				headers = {
					"If-None-Match": image_etag
				}
				async with session.get(video_thumbnail, headers=headers) as r:
					r.raise_for_status()
					image_newetag = r.headers['ETag']
					if r.status == 200:
						image_data = await r.read()
						image_name = f'{"".join(random.sample("0123456789abcdef", 6))}_thumbnail.jpg'
						image_id = 0
						if crop:
							img = Image.open(BytesIO(image_data)).convert("RGB")
							width, height = img.size
							crop_size = (height - (width // 16 * 9)) // 2
							cropped_image = img.crop((0, crop_size, width, height - crop_size))
							output_buffer = BytesIO()
							cropped_image.save(output_buffer, format='JPEG')
							image_data = output_buffer.getvalue()
					elif r.status == 304:
						url = f"{webhook_url}/messages/{msgid}"
						msg_data = await sends.get_message(url=url, authorization=False)
						if msg_data is None:
							return
						old_embed = discord.Embed().from_dict(msg_data['embeds'][0])
						if video_title == old_embed.title and f"{dur}{footext}" == old_embed.footer.text and int(publish_time) == int(old_embed.timestamp.timestamp()):
							return
						embed_image = yarl.URL(old_embed.image.url).path
						image_name = embed_image.split("/")[-1]
						image_id = embed_image.split("/")[-2]
						image_data = None
					else:
						raise Exception(f"Unknown status {r.status}")
			except Exception as e:
				logging.warning(f'Get Video Thumbnail Failed: {e.__class__.__name__}: {e}')
				repost(video_id, repost_time, channel_id)
				return

			if premiere:
				try:
					with sqlite3.connect(os.path.join(config.dir, 'database', 'webapp.db')) as webappdb:
						webappdb.execute('INSERT INTO premiere VALUES (?, ?, ?)', (video_id, int(publish_time + 3600), channel_id))
						webappdb.commit()
				except sqlite3.IntegrityError:
					pass

			embed = discord.Embed(title=video_title, url=f'https://youtu.be/{video_id}', color=0xFF0000)
			embed.set_image(url=f"attachment://{image_name}")
			embed.set_author(name=channel_title, url=f"https://www.youtube.com/{channel_custom_url}", icon_url=channel_avatar_url)
			embed.set_footer(text=f"{dur}{footext}")
			embed.timestamp = datetime.strptime(video_publish_time, "%Y-%m-%dT%H:%M:%S%z")

			if msgid is None:
				message_send = {
					'embeds': [embed.to_dict()],
					'username': channel_title,
					'avatar_url': channel_avatar_url,
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
				formdata = aiohttp.FormData()
				formdata.add_field(name="payload_json", value=json.dumps(message_send), content_type="application/json")
				formdata.add_field(name=f"files[{image_id}]", value=image_data, filename=image_name, content_type="image/jpeg")
				try:
					msgid = await sends.by_webhook(webhook_url, formdata)
					youtubedb.execute('INSERT INTO videoid VALUES (?, ?, ?, ?, ?, ?)', (video_id, msgid, etag, image_newetag, thumbnail_newtype, liveStream))
					youtubedb.commit()
				except Exception as e:
					logging.warning(f'Send Video Notification Failed: {e}')
					repost(video_id, repost_time, channel_id)
					return
			else:
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
				formdata = aiohttp.FormData()
				formdata.add_field(name="payload_json", value=json.dumps(message_send), content_type="application/json")
				if image_data is not None:
					formdata.add_field(name=f"files[{image_id}]", value=image_data, filename=image_name, content_type="image/jpeg")
				try:
					await sends.update_bywebhook(webhook_url, msgid, formdata)
					youtubedb.execute('UPDATE videoid SET etag = ?, image_etag = ?, image_type = ? WHERE id = ?', (etag, image_newetag, thumbnail_newtype, video_id))
					youtubedb.commit()
				except Exception as e:
					logging.warning(f'Update Video Notification Failed: {e}')
					repost(video_id, repost_time, channel_id)
					return

async def youtube_delete(video_id:str, channel_id:str, youtubedb:sqlite3.Connection):
	global lock
	async with lock:
		cursor = youtubedb.execute('SELECT msgid FROM videoid WHERE id = ?', (video_id,)).fetchone()
		if cursor is not None:
			msgid = cursor[0]
		else:
			return
		async with aiohttp.ClientSession() as session:
			url = f'https://youtube.googleapis.com/youtube/v3/videos?part=snippet&id={video_id}&key={config.youtube_api}'
			headers = {
				"Accept": "application/json",
				"Accept-Charset": "utf-8"
			}
			try:
				async with session.get(url, headers=headers) as r:
					r.raise_for_status()
					data = await r.json()
					channel_id = data['items'][0]['snippet']['channelId']
					asyncio.create_task(youtube(channel_id=channel_id, video_id=video_id, unlisted=True, youtubedb=youtubedb))
					return
			except (aiohttp.ClientResponseError, IndexError, KeyError):
				pass
		channel_cursor = youtubedb.execute('SELECT channel FROM subscribe WHERE id = ?', (channel_id,)).fetchone()
		if channel_cursor[0] == 1:
			webhook_url = config.youtube_vt_webhook
		elif channel_cursor[0] == 2:
			webhook_url = config.youtube_etc_webhook

		url = f"{webhook_url}/messages/{msgid}"
		msg = await sends.get_message(url, authorization=False)
		if msg is None:
			return
		await sends.delete_bywebhook(webhook_url, msgid)
		youtubedb.execute('DELETE FROM videoid WHERE id = ?', (video_id,))
		youtubedb.commit()

		old_embed = discord.Embed().from_dict(msg['embeds'][0])
		if old_embed.description is not None:
			text = old_embed.description
		else:
			text = f"[{old_embed.title}]({old_embed.url})"
		embed = discord.Embed(description=f"**Message Deleted in** <#{msg['channel_id']}>\n{text}", color=0xFF5733)
		embed.set_author(name=old_embed.author.name, icon_url=old_embed.author.icon_url)
		embed.set_footer(text="訊息發送時間")
		try:
			embed.timestamp = datetime.strptime(msg['timestamp'], "%Y-%m-%dT%H:%M:%S.%f%z")
		except ValueError:
			embed.timestamp = datetime.strptime(msg['timestamp'], "%Y-%m-%dT%H:%M:%S%z")
		message_send = {
			"embeds": [embed.to_dict()],
			"allowed_mentions": {
				"parse": []
			}
		}
		await sends.by_bot(config.server_info, message_send)

def repost(id:str, repost_time:int | None, channel_id:str):
	if repost_time is None:
		repost_time = 1
	else:
		repost_time += 1
	with sqlite3.connect(os.path.join(config.dir, 'database', 'webapp.db')) as db:
		try:
			db.execute('INSERT INTO repost VALUES (?, ?, ?, ?)', (id, "youtube", channel_id, repost_time))
			db.commit()
		except sqlite3.IntegrityError:
			pass