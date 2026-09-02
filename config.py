import os
import logging.handlers
import uuid
from pathlib import Path
import json

command_channel = {
	int(key): value
	for key, value in json.loads(os.environ["COMMAND_CHANNEL"]).items()
}

private_channel = json.loads(os.environ["PRIVATE_CHANNEL"])

notification_channel = json.loads(os.environ["NOTIFICATION_CHANNEL"])

do_not_delete_channel = json.loads(os.environ["DO_NOT_DELETE_CHANNEL"])

dir = Path(__file__).resolve().parent

handler = logging.handlers.RotatingFileHandler(filename=dir / "data" / "logs" / "syslog.txt",maxBytes=1048576,backupCount=2,encoding="UTF-8")

applications_id = int(os.environ["APPLICATIONS_ID"])

ownerid = int(os.environ["OWNER_ID"])

server_id = int(os.environ["SERVER_ID"])

server_info = int(os.environ["SERVER_INFO"])

bot_event = int(os.environ["BOT_EVENT"])

rules = int(os.environ["RULES_CHANNEL"])

chatgpt_channel = int(os.environ["CHATGPT_CHANNEL"])

chatgpt_image_channel = int(os.environ["CHATGPT_IMAGE_CHANNEL"])

member_role = int(os.environ["MEMBER_ROLE"])

youtube_vt_webhook = os.environ["YOUTUBE_VT_WEBHOOK"]

youtube_etc_webhook = os.environ["YOUTUBE_ETC_WEBHOOK"]

misskey_webhook = os.environ["MISSKEY_WEBHOOK"]

misskeyall_webhook = os.environ["MISSKEY_ALL_WEBHOOK"]

fantia_webhook = os.environ["FANTIA_WEBHOOK"]

bluesky_webhook = os.environ["BLUESKY_WEBHOOK"]

twitch_webhook = os.environ["TWITCH_WEBHOOK"]

token = os.environ["BOT_TOKEN"]

youtube_api = os.environ["YOUTUBE_API"]

misskey = os.environ["MISSKEY"]

misskey_antenna = os.environ["MISSKEY_ANTENNA"]

twitch_id = os.environ["TWITCH_ID"]

twitch_secret = os.environ["TWITCH_SECRET"]

chatgpt_girl_token = os.environ["CHATGPT_GIRL_TOKEN"]

chatgpt_boy_token = os.environ["CHATGPT_BOY_TOKEN"]

chatgpt_moderations = os.environ["CHATGPT_MODERATIONS"]

chatgpt_user = os.environ["CHATGPT_USER"]

bot_public_key = os.environ["BOT_PUBLIC_KEY"]

bot_auth_secret = os.environ["BOT_AUTH_SECRET"]

the_cat_api = os.environ["THE_CAT_API"]

the_auth_api = os.environ["THE_AUTH_API"]

status_page_key = os.environ["STATUS_PAGE_KEY"]

status_page_id = os.environ["STATUS_PAGE_ID"]

cloudflare_origin_ca_key = os.environ["CF_ORIGIN_CA_KEY"]

cloudflare_zone_id = os.environ["CF_ZONE_ID"]

cloudflare_yuki_zone_id = os.environ["CF_YUKI_ZONE_ID"]

cloudflare_turnstile_key = os.environ["CF_TURNSTILE_KEY"]

porkbun_api_key = os.environ["PORKBUN_API_KEY"]

porkbun_secret_key = os.environ["PORKBUN_SECRET"]

line_secret = os.environ["LINE_SECRET"]

line_token = os.environ["LINE_TOKEN"]

icloud_username = os.environ["ICLOUD_USERNAME"]

icloud_app_password = os.environ["ICLOUD_APP_PASSWORD"]

mail_send_name = os.environ["MAIL_SEND_NAME"]

mail_send_address = os.environ["MAIL_SEND_ADDRESS"]

mail_recv_name = os.environ["MAIL_RECV_NAME"]

mail_recv_address = os.environ["MAIL_RECV_ADDRESS"]

nginx_certificate_path = os.environ["NGINX_CERT_PATH"]

nginx_yuki_certificate_path = os.environ["NGINX_YUKI_CERT_PATH"]

bluesky_bot_pds = os.environ["BLUESKY_BOT_PDS"]

bluesky_admin_pds = os.environ["BLUESKY_ADMIN_PDS"]

uuid_namespace = uuid.UUID(os.environ["WEB_NAMESPACE"])

verify = os.environ["WEB_VERIFY"]

secret = os.environ["WEB_SECRET"]

web_secret = os.environ["WEB_TOKEN"]

domain = os.environ["DOMAIN"]

yuki_domain = domain = os.environ["YUKI_DOMAIN"]

onion_domain = os.environ["ONION_DOMAIN"]

bot_agent = f"modbot/1.0 (+https://{domain})"

git_identity_file = os.environ["GIT_IDENTITY_PATH"]