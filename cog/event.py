import discord
from discord.ext import commands
import config
import sends
import datetime
import asyncio
import pytz
import contextlib

tz = pytz.timezone('Asia/Taipei')

class Event(commands.Cog):
	def __init__(self, bot):
		self.bot:commands.Bot = bot
		self.lock = asyncio.Semaphore(1)
		self.rate = 0
		self.ratetime = int(datetime.datetime.now().timestamp())

	@contextlib.asynccontextmanager
	async def cleanup_rate(self):
		await self.lock.acquire()
		now = int(datetime.datetime.now().timestamp())
		if now == self.ratetime:
			self.rate += 1
		else:
			self.rate = 1
			self.ratetime = now
		if self.rate >= 5:
			await asyncio.sleep(1)
		try:
			yield
		finally:
			self.lock.release()

	@commands.Cog.listener()
	async def on_message(self, message:discord.Message):
		async with self.cleanup_rate():
			for channel_id, keyword in config.command_channel.items():
				channel = self.bot.get_channel(channel_id)
				if not message.author.bot and message.channel.id in config.command_channel and channel.id in config.command_channel and config.command_channel[channel.id] != message.type:
					try:
						await message.delete()
					except discord.errors.NotFound:
						return

	@commands.Cog.listener()
	async def on_message_delete(self, message:discord.Message):
		if message.author.bot and message.channel.id not in config.private_channel:
			avatar_url = message.author.display_avatar.url
			embed = discord.Embed(description=f"**Message Deleted in** <#{message.channel.id}>\n{message.content}", color=0xFF5733)
			embed.set_author(name=message.author.display_name, icon_url=avatar_url)
			embed.set_footer(text='訊息發送時間')
			embed.timestamp = message.created_at

			message_send = {
				'embeds': [embed.to_dict()]
			}
			try:
				await sends.by_bot(config.server_info, message_send)
			except:
				pass

	@commands.Cog.listener()
	async def on_raw_message_delete(self, payload:discord.RawMessageDeleteEvent):
		channel_id = payload.channel_id
		message_id = payload.message_id
		if channel_id not in config.private_channel and payload.cached_message is None:
			binid = bin(message_id)[2:]
			snowflake = str(binid).zfill(64)
			bintime = snowflake[:42]
			unixtime = (int(bintime, 2) + 1420070400000)/1000
			createtime = datetime.datetime.fromtimestamp(unixtime,tz=tz)
			embed = discord.Embed(title="A Raw Message Deleted", description=f'Channel: <#{channel_id}>\nMassage ID: {message_id}', color=0xFFAA00)
			embed.set_footer(text='訊息發送時間')
			embed.timestamp = createtime

			message_send = {
				'embeds': [embed.to_dict()]
			}
			try:
				await sends.by_bot(config.server_info, message_send)
			except:
				pass

async def setup(bot):
	await bot.add_cog(Event(bot))