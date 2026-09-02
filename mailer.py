import smtplib
from email.message import EmailMessage
from email.headerregistry import Address
from jinja2 import Environment, FileSystemLoader
import config
from datetime import datetime
import pytz

env = Environment(loader=FileSystemLoader(config.dir / "html" / "mail"))
tz = pytz.timezone('Asia/Taipei')

SMTP_HOST = "smtp.mail.me.com"
SMTP_PORT = 587
SERVERNAME = f"mail.{config.domain}"

def send_email(subject:str, html:str, text:str):
	msg = EmailMessage()

	msg["From"] = Address(config.mail_send_name, addr_spec=config.mail_send_address)
	msg["To"] = Address(config.mail_recv_name, addr_spec=config.mail_recv_address)
	msg["Subject"] = subject

	msg.set_content(text)
	msg.add_alternative(html, subtype="html")

	with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
		smtp.ehlo(SERVERNAME)
		smtp.starttls()
		smtp.ehlo(SERVERNAME)

		smtp.login(config.icloud_username, config.icloud_app_password)
		smtp.send_message(msg)

def login_notification(headers:dict, action_url:str, method:str):
	now = datetime.now(tz=tz).strftime("%Y-%m-%d %H:%M:%S%z")

	args = {
		"title": "新的登入活動",
		"message": "您的帳號剛剛完成了一次登入。",
		"event": {
			"time": now,
			"ip": headers.get("X-Real-IP"),
			"location": headers.get("CF-IPCountry"),
			"authentication": method,
			"user_agent": headers.get("User-Agent"),
		},
		"action_url": action_url
	}

	text = env.get_template("login.txt").render(**args)
	html = env.get_template("login.html").render(**args)

	send_email(subject="新的登入活動", text=text, html=html)

# usage
# await asyncio.to_thread(login_notification, headers=Request.headers, action_url=action_url, method="Passkey")